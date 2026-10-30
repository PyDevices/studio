"""The workflow improvements from the UI audit, through the real UI.

    .venv/bin/python tools/workflow_check.py --cdp http://127.0.0.1:9222
    .venv/bin/python tools/workflow_check.py --desktop processes|threads|threads-ft

  knobs     every instrument and effect knob is on screen (Juno: 16, ParametricEQ: 16)
  copy      Copy puts this track's pattern in its next empty slot and goes there;
            1 bar to 2 repeats the bar
  confirm   New after an edit asks first; Cancel keeps the work, Discard starts over
  song      a scene's play button starts there; its loop button repeats it;
            holding a letter opens that pattern with the song still on
  tempo     holding + repeats, dragging the value changes it, taps 0.4 s apart set ~150;
            a whole hold is one undo
  accent    a step cycles hit (100), accent (127), off
  record    play with record armed counts in a bar first; with quantise off a
            note keeps its timing
  hardware  with the master shown, the keyboard's knobs turn its selected
            effect, and the status line says what moved
  undo      an instrument change undoes in one step, its knob settings back too
  rec look  armed record is red, not the orange of Play, and the track is marked
  tooltips  the mouse resting on an icon button lights it and shows what it
            does; moving away or pressing hides it
"""
import argparse, json, pathlib, sys, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools")); sys.path.insert(0, str(ROOT.parent.parent / "lib"))
from songlink import model
from files_check import menu


def run(s):
    app = s.app
    out = []
    check = lambda ok, line: out.append((bool(ok), line))
    dpr = app.dpr
    centre = lambda r: ((r[0] + r[2] / 2) / dpr, (r[1] + r[3] / 2) / dpr)

    # -- knobs
    s.reset()
    app.select(2)                                  # Keys: Juno-106, 16 knobs
    g = app.geom()
    s.op({"op": "insert", "path": [["track", app.project()["tracks"][2]["id"]], "rack", -1],
          "value": {"name": "ParametricEQ", "bypass": False, "macros": {}}})
    time.sleep(0.5)
    s.send({"kind": "inspect", "fx": 0}); time.sleep(0.5)
    g2 = app.geom()
    check(g["knobs"] == 16 and g2["fx_knobs"] == 16,
          "knobs: Juno shows %d of 16, ParametricEQ %d of 16" % (g["knobs"], g2["fx_knobs"]))

    # -- copy, and 1 bar to 2
    p = model.new_project()
    p["tracks"][0]["patterns"][0]["notes"] = [[0, 24, 36, 100], [192, 24, 38, 100]]
    p["tracks"][1]["patterns"][0]["notes"] = [[0, 48, 45, 100]]
    s.reset(p)
    app.select(1)
    app.click_rect(app.geom()["copy"]); time.sleep(0.5)
    q = app.project()
    bass_q = q["tracks"][1]
    others = all(len(t["patterns"]) < 2 or not t["patterns"][1]["notes"] for i, t in enumerate(q["tracks"]) if i != 1)
    check(bass_q["current"] == 1 and bass_q["patterns"][1]["notes"] == bass_q["patterns"][0]["notes"] and others,
          "copy: Bass's pattern A copied to its B, now on B (%d), the other tracks untouched (%s)" % (bass_q["current"], others))
    app.click_rect(app.geom()["bars"]["2"]); time.sleep(0.5)
    bass = app.project()["tracks"][1]["patterns"][1]
    check(bass["bars"] == 2 and sorted(bass["notes"]) == [[0, 48, 45, 100], [384, 48, 45, 100]],
          "copy: 1 bar to 2 repeats the bar: %s" % bass["notes"])

    # -- confirm before discarding
    menu(app, "new"); time.sleep(0.5)
    g = app.geom()
    asked = "confirm" in g
    if asked:
        app.click_rect(g["confirm"]["cancel"]); time.sleep(0.3)
    kept = app.project()["tracks"][1]["patterns"][1]["notes"] != []
    menu(app, "new"); time.sleep(0.5)
    g = app.geom()
    if "confirm" in g:
        app.click_rect(g["confirm"]["discard"]); time.sleep(0.5)
    s.settle()
    fresh = not any(n for t in app.project()["tracks"] for pt in t["patterns"] for n in pt["notes"])
    check(asked and kept and fresh, "confirm: New asks after an edit (%s), Cancel keeps it (%s), Discard starts over (%s)"
          % (asked, kept, fresh))

    # -- song: play from a scene, loop it, edit from it
    demo = json.loads((ROOT / "assets" / "demo_song.json").read_text())
    s.reset(demo)
    sc = app.geom()["scenes"]
    from_scene = 4
    want = sum(4 * model.BAR for _ in range(from_scene))         # demo scenes are 4 bars, x1
    app.click_rect(sc["nums"][from_scene]); time.sleep(1.0)
    tick = app.geom()["tick"]
    check(tick is not None and want <= tick < want + model.BAR, "song: scene 5's play starts at tick %s (want %d+)"
          % (tick and round(tick), want))
    app.click_rect(sc["loops"][1]); time.sleep(0.3)
    looped = app.project()["loop_scene"]
    time.sleep(8.0)                                  # longer than the 4-bar scene (7 s at 138 BPM)
    playing = app.geom()["scenes"]["playing"]
    check(looped == 1 and playing == 1, "song: the loop button repeats scene 2 (loop %s, playing %s after 8 s)"
          % (looped, playing))
    app.click_rect(sc["loops"][1]); time.sleep(0.3)
    app.click_rect(app.geom()["stop"])
    letter = app.geom()["scenes"]["letters"][5][0]               # scene 6, Drums: B
    x, y = centre(letter)
    s.mouse.move(x, y); s.mouse.down(); time.sleep(0.9); s.mouse.up(); time.sleep(0.5)
    g = app.geom(); q = app.project()
    check(not g["song_shown"] and q["song_mode"] and q["tracks"][0]["current"] == 1 and "grid" in g,
          "song: holding scene 6's Drums letter opens pattern B with the song on (shown %s, mode %s, pattern %s)"
          % (g["song_shown"], q["song_mode"], q["tracks"][0]["current"]))

    # -- tempo: hold, drag, tap; one undo for a hold
    s.reset()
    g = app.geom()
    t0 = app.project()["tempo"]
    tbox = g["tempo"]
    ty, th = tbox[1], tbox[3]
    plus_x = (tbox[0] + tbox[2] - th / 2) / dpr
    s.mouse.move(plus_x, (ty + th / 2) / dpr); s.mouse.down(); time.sleep(1.6); s.mouse.up(); time.sleep(0.3)
    held = app.project()["tempo"]
    app.click_rect(g["undo"]); time.sleep(0.3)
    undone = app.project()["tempo"]
    check(held >= t0 + 5 and undone == t0, "tempo: holding + went %d to %d; one undo back to %d" % (t0, held, undone))
    mid_x = (tbox[0] + tbox[2] / 2) / dpr
    s.mouse.move(mid_x, (ty + th / 2) / dpr); s.mouse.down()
    for k in range(1, 9):
        s.mouse.move(mid_x, (ty + th / 2) / dpr - k * 8); time.sleep(0.03)
    s.mouse.up(); time.sleep(0.3)
    dragged = app.project()["tempo"]
    check(dragged > t0, "tempo: dragging the value up went %d to %d" % (t0, dragged))
    # Taps 0.4 s apart, give or take how long a click takes to send (the desktop's
    # control socket holds each click a few frames): judged at the pace they went.
    at = []
    for k in range(5):
        at.append(time.monotonic())
        s.mouse.click(mid_x, (ty + th / 2) / dpr)
        time.sleep(max(0.0, at[-1] + 0.4 - time.monotonic()))
    pace = 60 / ((at[-1] - at[0]) / 4)
    tapped = app.project()["tempo"]
    check(abs(tapped - pace) <= 0.08 * pace,
          "tempo: five taps about 0.4 s apart set %d from %d (%.0f at the taps' own pace)" % (tapped, dragged, pace))

    # -- accent
    s.reset()
    app.select(0)
    grid = app.geom()["grid"]
    gx, gy, gw, gh = grid["rect"]
    cw, ch = gw / grid["steps"], gh / len(grid["rows"])
    cell = ((gx + 4.5 * cw) / dpr, (gy + 0.5 * ch) / dpr)              # Bass Drum, step 5
    vel = []
    for _ in range(3):
        s.mouse.click(*cell); time.sleep(0.4)
        ns = app.project()["tracks"][0]["patterns"][0]["notes"]
        vel.append(ns[0][3] if ns else None)
    check(vel == [100, 127, None], "accent: a step cycles %s (want hit 100, accent 127, off)" % vel)

    # -- record: count-in, and quantise off
    s.reset()
    app.select(2)
    g = app.geom()
    app.click_rect(g["quant"]); time.sleep(0.3)                  # quantise off
    q_off = app.project()["quantise"] is False
    app.click_rect(g["rec"])
    bar_ms = 4 * 60000 / app.project()["tempo"]
    step_ms = bar_ms / 16
    # Two notes after the count-in, four steps apart, the first 30 ms late. The
    # fake keyboard's clock and the transport's start are a click's latency
    # apart, so what's judged is the distance between them.
    at = 80 + bar_ms + 6 * step_ms + 30
    at2 = 80 + bar_ms + 10 * step_ms
    app.click_rect(g["play"])
    app.play_midi([[at, [0x90, 60, 100]], [at + 100, [0x80, 60, 0]],
                   [at2, [0x90, 64, 100]], [at2 + 100, [0x80, 64, 0]]])
    time.sleep(0.4)
    counting = app.geom()
    time.sleep((at2 + 600) / 1000)
    app.click_rect(app.geom()["stop"]); app.click_rect(g["rec"]); time.sleep(0.4)
    notes = sorted(app.project()["tracks"][2]["patterns"][0]["notes"], key=lambda n: n[2])
    gap = notes[1][0] - notes[0][0] if len(notes) == 2 else None
    late = 30 / 1000 * app.project()["tempo"] * model.PPQ / 60
    check(counting["tick"] is not None and counting["tick"] < 0 and counting["position"].startswith("-"),
          "record: Play with record armed counts in first (tick %s, shows '%s')"
          % (counting["tick"] and round(counting["tick"]), counting["position"]))
    want = 4 * model.STEP - late
    check(q_off and gap is not None and abs(gap - want) < 4,
          "record: with quantise off two notes four steps apart, the first 30 ms late, are %s ticks apart (want %.0f, not 96)"
          % (gap, want))
    app.click_rect(g["quant"])                                   # back on

    # -- hardware knobs with the master shown
    s.reset()
    s.op({"op": "insert", "path": ["master", "rack", -1], "value": {"name": "LowPass", "bypass": False, "macros": {}}})
    time.sleep(0.4)
    keys_before = app.project()["tracks"][2]["macros"]
    s.send({"kind": "inspect", "master": True, "fx": 0}); time.sleep(0.4)
    s.send({"kind": "cc", "chan": 0, "cc": 30, "value": 33}); time.sleep(0.4)
    q = app.project(); said = app.geom()["last_midi"]
    check(q["master"]["rack"][0]["macros"].get("0") == 33 and q["tracks"][2]["macros"] == keys_before
          and "Master: LowPass" in said,
          "hardware: with the master shown, knob 1 turned its LowPass (%s), not Keys; status '%s'"
          % (q["master"]["rack"][0]["macros"], said))

    # -- undo an instrument change
    s.reset()
    s.send({"kind": "cc", "chan": 0, "cc": 31, "value": 20}); time.sleep(0.4)   # Keys: knob 2 to 20
    before = app.project()["tracks"][2]
    s.send({"kind": "instrument", "index": 2, "name": "dx7"}); time.sleep(0.3)
    s.settle()
    changed = app.project()["tracks"][2]["instrument"]
    app.click_rect(app.geom()["undo"]); time.sleep(0.3)
    s.settle()
    after = app.project()["tracks"][2]
    check(changed == "dx7" and after["instrument"] == before["instrument"] and after["macros"] == before["macros"],
          "undo: Juno to DX7 and one undo brings back the Juno with its knob (%s, %s)" % (after["instrument"], after["macros"]))

    # -- the record button
    s.reset()
    g = app.geom()
    app.click_rect(g["rec"]); time.sleep(0.4)
    r = g["rec"]
    # One pixel near the button's corner, clear of the icon.
    px_ = s.pixel(r[0] + 8, r[1] + 8)
    armed = app.geom()["armed"]
    app.click_rect(g["rec"])
    check(px_[0] > 180 and px_[1] < 110 and armed == app.project()["tracks"][2]["id"],
          "rec look: armed record is red %s and Keys is marked armed (%s)" % (px_, armed))

    # -- tooltips (hover needs pydevices' display_driver to pass a released mouse on)
    s.reset()
    g = app.geom()
    away = (10 / dpr, (g["tracks"][-1][1] + g["tracks"][-1][3] + 40) / dpr)
    s.mouse.move(*away); time.sleep(0.3)
    s.mouse.move(*centre(g["metronome"])); time.sleep(0.9)
    over = app.geom()
    s.mouse.move(*away); time.sleep(0.4)
    left = app.geom()["tip"]
    s.mouse.move(*centre(g["undo"])); time.sleep(0.9)
    undo_tip = app.geom()["tip"]
    s.mouse.down(); time.sleep(0.2)
    pressed = app.geom()["tip"]
    s.mouse.up(); s.mouse.move(*away)
    check(over["tip"] == "Metronome" and over["hovered"]["metronome"] and left is None
          and undo_tip == "Undo  (Ctrl+Z)" and pressed is None,
          "tooltips: resting on the metronome lights it and says '%s'; away hides it (%s); undo says '%s'; a press hides it (%s)"
          % (over["tip"], left, undo_tip, pressed))
    return out


def main():
    from checkkit import add_session_args, report, session
    ap = argparse.ArgumentParser(); add_session_args(ap); a = ap.parse_args()
    with session(a) as s:
        ok = report("workflow", run(s))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
