"""The second UI audit's changes and the groups added with them, through the real UI.

    .venv/bin/python tools/workflow2_check.py --cdp http://127.0.0.1:9222
    .venv/bin/python tools/workflow2_check.py --desktop processes|threads|threads-ft

  patterns  each track plays its own pattern: a change while playing waits for
            the bar line and moves only that track; Shift-click moves every
            track; slots with notes show a dot
  scenes    Duplicate copies a scene; a letter opens a picker that sets it
  pads      the keyboard's pad 5 records the grid's fifth row, not note 40;
            messages that arrive together all play then (a late render once
            left the last of them for the next key, so a new pad played the
            previous pad's note)
  mix       mute stays out of undo; pan hard left silences the right meter;
            slider bank B pans
  velocity  Shift-drag sets a note's and a hit's velocity, the readout shows
            it, and Shift-click doesn't toggle a step
  takes     with overdub off a take replaces the pattern (one undo brings it
            back); in a song, a take lands in the scene's pattern
  effects   the selected effect moves down the chain
  editing   Delete removes the selected note; the wheel scrolls the roll
  knobs     a knob drags up, a double-click resets it; the next-instrument
            button steps the list
  files     Save asks once, then Ctrl+S saves without asking; the status bar
            says when there are unsaved changes; "Save first" saves, then goes on
  keyboard  the DMK's Forward, Loop and Rewind; Learn says what to do; the
            on-screen keys move an octave
"""
import argparse, json, pathlib, sys, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools")); sys.path.insert(0, str(ROOT / "app" / "shared"))
import model
from files_check import menu
from master_check import readings


def pad_order(instrument="tr808", patch=0):
    """The kit's notes in grid order, as the app works it out."""
    info = json.loads((ROOT / "assets" / "catalog.json").read_text())["instruments"][instrument]
    audible = info.get("pads", {}).get(str(patch))
    return [n for n, _ in info["notes"] if audible is None or n in audible]


def run(s):
    app = s.app
    out = []
    check = lambda ok, line: out.append((bool(ok), line))
    dpr = app.dpr
    centre = lambda r: ((r[0] + r[2] / 2) / dpr, (r[1] + r[3] / 2) / dpr)
    bar_s = lambda: 4 * 60 / app.project()["tempo"]

    def note_at_ms(ms, msg):
        app.play_midi([[ms, msg], [ms + 120, [msg[0] & 0xEF, msg[1], 0]]])

    # -- patterns: per track, queued to the bar; Shift for all; dots
    p = model.new_project()
    p["tracks"][1]["patterns"] = [{"bars": 1, "notes": [[0, 48, 45, 100]]}, model.pattern(),
                                  {"bars": 1, "notes": [[0, 48, 52, 100]]}]
    s.reset(p)
    app.select(1)
    g = app.geom()
    check(g["slot_dots"][:3] == [True, False, True], "patterns: Bass's slots with notes show dots %s" % g["slot_dots"][:3])
    app.click_rect(g["play"]); time.sleep(0.6)
    app.click_rect(g["slots"][2]); time.sleep(0.2)
    q = app.project()["tracks"]
    waiting = q[1]["current"] == 0 and (q[1]["next"] or {}).get("pattern") == 2
    time.sleep(bar_s() + 0.3)
    q = app.project()["tracks"]
    app.click_rect(g["stop"])
    check(waiting and q[1]["current"] == 2 and q[0]["current"] == 0,
          "patterns: C while playing waits for the bar (%s), then Bass is on C and Drums still on A (%d, %d)"
          % (waiting, q[1]["current"], q[0]["current"]))
    s.keyboard.down("Shift"); time.sleep(0.2)
    app.click_rect(app.geom()["slots"][1]); time.sleep(0.3)
    s.keyboard.up("Shift"); time.sleep(0.2)
    cur = [t["current"] for t in app.project()["tracks"]]
    check(cur == [1, 1, 1, 1], "patterns: Shift-click B puts every track on B %s" % cur)

    # -- scenes: duplicate, and the letter picker
    q = model.new_project()
    tid = [str(t["id"]) for t in q["tracks"]]
    q["tracks"][2]["patterns"] = [{"bars": 1, "notes": [[0, 24, 60, 100]]}, model.pattern()]
    q["song"] = [{"patterns": {tid[2]: 0}, "repeat": 1}, {"patterns": {tid[2]: 1}, "repeat": 1}]
    q["song_mode"] = True
    s.reset(q)
    sc = app.geom()["scenes"]
    app.click_rect(sc["dups"][0]); time.sleep(0.4)
    song = app.project()["song"]
    check(len(song) == 3 and song[1] == song[0], "scenes: Duplicate copies scene 1 after it (%d scenes)" % len(song))
    sc = app.geom()["scenes"]
    app.click_rect(sc["letters"][0][0]); time.sleep(0.4)          # scene 1, Drums
    picker = app.geom().get("picker")
    if picker:
        app.click_rect(picker[2]); time.sleep(0.4)                 # C
    chosen = app.project()["song"][0]["patterns"].get(tid[0])
    check(picker is not None and len(picker) == 9 and chosen == 2,
          "scenes: a letter opens A-H and - (%s), and C sets Drums to C (%s)" % (picker and len(picker), chosen))

    # -- pads: pad 5 plays the grid's fifth row
    s.reset()
    app.select(0)
    g = app.geom()
    app.click_rect(g["rec"]); app.click_rect(g["play"])
    note_at_ms(80 + bar_s() * 1000 + 300, [0x99, 40, 100])         # pad 5, after the count-in
    time.sleep(bar_s() + 1.0)
    app.click_rect(g["stop"]); app.click_rect(g["rec"]); time.sleep(0.3)
    pitches = sorted({n[2] for n in app.project()["tracks"][0]["patterns"][0]["notes"]})
    want = pad_order()[4]
    check(pitches == [want], "pads: pad 5 (note 40) recorded the grid's fifth row, note %d: %s" % (want, pitches))

    # Pad 1's hit and release and pad 5's hit together, as a late render sees
    # them; pad 8 alone 600 ms later. Each must record at its own time.
    s.reset()
    app.select(0)
    g = app.geom()
    app.click_rect(g["rec"]); app.click_rect(g["play"])
    t = 80 + bar_s() * 1000 + 300
    app.play_midi([
        [t, [0x99, 36, 100]], [t, [0x89, 36, 0]], [t, [0x99, 40, 100]], [t + 100, [0x89, 40, 0]],
        [t + 600, [0x99, 43, 100]], [t + 700, [0x89, 43, 0]]])
    time.sleep(bar_s() + 1.5)
    app.click_rect(g["stop"]); app.click_rect(g["rec"]); time.sleep(0.3)
    order = pad_order()
    got = sorted((n[0], n[2]) for n in app.project()["tracks"][0]["patterns"][0]["notes"])
    step_ms = 60_000 / app.project()["tempo"] / 4
    at = {pitch: round(tick / model.STEP * step_ms) for tick, pitch in got}
    ok = (set(at) == {order[0], order[4], order[7]} and abs(at[order[0]] - at[order[4]]) < 60
          and 450 < at[order[7]] - at[order[0]] < 750)
    check(ok, "pads: three messages together all play at once, the next pad on its own (ms into the bar): %s" % at)

    # -- mix: mute out of undo; pan; slider bank B
    hats = model.new_project()
    hats["tracks"][0]["patterns"][0]["notes"] = [[k * model.STEP, 12, 42, 110] for k in range(16)]
    s.reset(hats)
    app.select(0)
    grid = app.geom()["grid"]
    gx, gy, gw, gh = grid["rect"]
    s.mouse.click((gx + 2.5 * gw / grid["steps"]) / dpr, (gy + 0.5 * gh / len(grid["rows"])) / dpr); time.sleep(0.3)
    app.click_rect(app.geom()["mutes"][0]); time.sleep(0.3)
    app.click_rect(app.geom()["undo"]); time.sleep(0.3)
    q = app.project()["tracks"][0]
    kick_gone = not any(n[2] == 36 for n in q["patterns"][0]["notes"])
    check(q["mute"] is True and kick_gone, "mix: undo takes back the edit (%s) and leaves the mute on (%s)" % (kick_gone, q["mute"]))
    app.click_rect(app.geom()["mutes"][0]); time.sleep(0.2)
    s.send({"kind": "cc", "chan": 4, "cc": 7, "value": 0}); time.sleep(0.3)   # slider 5: Drums pan
    pan = app.project()["tracks"][0]["pan"]
    ids = [str(t["id"]) for t in app.project()["tracks"]]
    g = app.geom(); app.click_rect(g["play"]); time.sleep(1.5)
    tracks, _ = readings(s)
    app.click_rect(g["stop"])
    l, r = tracks.get(ids[0], [-100, -100])
    check(pan == 0 and l > -40 and r <= -90, "mix: slider 5 pans Drums hard left (%s): left %s dB, right %s dB" % (pan, l, r))

    # -- velocity: Shift-drag a note and a hit
    p = model.new_project()
    p["tracks"][2]["patterns"][0]["notes"] = [[0, 48, 60, 100]]
    s.reset(p)
    app.select(2)
    roll = app.geom()["roll"]
    rx, ry, rw, rh = roll["rect"]
    row = roll["low"] + roll["rows"] - 1 - 60
    nx, ny = (rx + 0.5 * rw / roll["steps"]) / dpr, (ry + (row + 0.5) * rh / roll["rows"]) / dpr
    s.keyboard.down("Shift"); time.sleep(0.2)
    s.mouse.move(nx, ny); s.mouse.down()
    for k in range(1, 11):
        s.mouse.move(nx, ny - 3 * k); time.sleep(0.02)
    s.mouse.up(); s.keyboard.up("Shift"); time.sleep(0.4)
    v = app.project()["tracks"][2]["patterns"][0]["notes"][0][3]
    info = app.geom()["note_info"]
    check(v > 100 and info and "vel %d" % v in info, "velocity: Shift-drag up raised the note to %d; the readout says '%s'" % (v, info))
    p = model.new_project()
    p["tracks"][0]["patterns"][0]["notes"] = [[0, 24, 36, 100]]
    s.reset(p)
    app.select(0)
    grid = app.geom()["grid"]
    gx, gy, gw, gh = grid["rect"]
    cx, cy = (gx + 0.5 * gw / grid["steps"]) / dpr, (gy + 0.5 * gh / len(grid["rows"])) / dpr
    s.keyboard.down("Shift"); time.sleep(0.2)
    s.mouse.click(cx, cy); time.sleep(0.3)
    still = len(app.project()["tracks"][0]["patterns"][0]["notes"]) == 1
    s.mouse.move(cx, cy); s.mouse.down()
    for k in range(1, 11):
        s.mouse.move(cx, cy + 3 * k); time.sleep(0.02)
    s.mouse.up(); s.keyboard.up("Shift"); time.sleep(0.4)
    ns = app.project()["tracks"][0]["patterns"][0]["notes"]
    check(still and len(ns) == 1 and ns[0][3] < 100,
          "velocity: Shift-click leaves the hit (%s); Shift-drag down softened it to %s" % (still, ns and ns[0][3]))

    # -- takes: overdub off replaces; song takes go to the scene's pattern
    p = model.new_project()
    p["tracks"][2]["patterns"][0]["notes"] = [[0, 24, 48, 100], [96, 24, 50, 100]]
    s.reset(p)
    app.select(2)
    g = app.geom()
    app.click_rect(g["overdub"]); time.sleep(0.2)
    app.click_rect(g["rec"]); app.click_rect(g["play"])
    note_at_ms(80 + bar_s() * 1000 + 300, [0x90, 64, 100])
    time.sleep(bar_s() + 1.0)
    app.click_rect(g["stop"]); app.click_rect(g["rec"]); time.sleep(0.3)
    took = [n[2] for n in app.project()["tracks"][2]["patterns"][0]["notes"]]
    app.click_rect(app.geom()["undo"]); time.sleep(0.3)
    back = sorted(n[2] for n in app.project()["tracks"][2]["patterns"][0]["notes"])
    app.click_rect(g["overdub"])
    check(took == [64] and back == [48, 50], "takes: overdub off, the take replaced the pattern %s; one undo brings back %s" % (took, back))
    q = model.new_project()
    tid = str(q["tracks"][2]["id"])
    q["tracks"][2]["patterns"] = [{"bars": 1, "notes": [[0, 24, 48, 100]]}, model.pattern()]
    q["song"] = [{"patterns": {tid: 0}, "repeat": 1}, {"patterns": {tid: 1}, "repeat": 1}]
    q["song_mode"] = True
    s.reset(q)
    app.select(2)
    s.send({"kind": "key", "key": "escape"})
    g = app.geom()
    app.click_rect(g["song"]) if not g.get("song_shown") else None
    app.click_rect(g["rec"])
    app.click_rect(app.geom()["scenes"]["nums"][1]); time.sleep(0.1)    # play from scene 2
    note_at_ms(400, [0x90, 67, 100])
    time.sleep(1.2)
    app.click_rect(app.geom()["stop"]); app.click_rect(g["rec"]); time.sleep(0.3)
    pats = app.project()["tracks"][2]["patterns"]
    check([n[2] for n in pats[1]["notes"]] == [67] and [n[2] for n in pats[0]["notes"]] == [48],
          "takes: in a song, a take in scene 2 went to its pattern B %s, A untouched %s"
          % ([n[2] for n in pats[1]["notes"]], [n[2] for n in pats[0]["notes"]]))

    # -- effects: move the selected one down the chain
    s.reset()
    app.select(2)
    kid = app.project()["tracks"][2]["id"]
    for name in ("Chorus", "Phaser"):
        s.op({"op": "insert", "path": [["track", kid], "rack", -1], "value": {"name": name, "bypass": False, "macros": {}}})
    time.sleep(0.4)
    s.send({"kind": "inspect", "fx": 0}); time.sleep(0.4)
    moves = app.geom()["fx_moves"]
    if moves:
        app.click_rect(moves[0]); time.sleep(0.4)                     # later in the chain
    order = [e["name"] for e in app.project()["tracks"][2]["rack"]]
    check(order == ["Phaser", "Chorus"], "effects: Chorus moved later in the chain: %s" % order)

    # -- editing: Delete, and the wheel
    p = model.new_project()
    p["tracks"][2]["patterns"][0]["notes"] = [[0, 48, 60, 100]]
    s.reset(p)
    app.select(2)
    roll = app.geom()["roll"]
    rx, ry, rw, rh = roll["rect"]
    row = roll["low"] + roll["rows"] - 1 - 60
    s.mouse.click((rx + 0.5 * rw / roll["steps"]) / dpr, (ry + (row + 0.5) * rh / roll["rows"]) / dpr); time.sleep(0.3)
    s.keyboard.press("Delete"); time.sleep(0.4)
    gone = not app.project()["tracks"][2]["patterns"][0]["notes"]
    low0 = app.geom()["roll"]["low"]
    s.mouse.move((rx + rw / 2) / dpr, (ry + rh / 2) / dpr); s.mouse.wheel(0, -120); time.sleep(0.5)
    low1 = app.geom()["roll"]["low"]
    check(gone and low1 == low0 + 2, "editing: Delete removed the note (%s); the wheel scrolled the roll %d to %d" % (gone, low0, low1))

    # -- knobs: drag, reset; the next instrument
    s.reset()
    app.select(2)
    kr = app.geom()["knob_rects"][1]                                 # Juno: Cutoff
    kid = app.project()["tracks"][2]["id"]
    before = json.loads((ROOT / "assets" / "catalog.json").read_text())["instruments"]["juno106"]["patch_values"]["0"][1]
    kx, ky = centre(kr)
    s.mouse.move(kx, ky); s.mouse.down()
    for k in range(1, 11):
        s.mouse.move(kx, ky + 2 * k); time.sleep(0.02)              # down: lower
    s.mouse.up(); time.sleep(0.4)
    dragged = app.project()["tracks"][2]["macros"].get("1")
    s.mouse.dblclick(kx, ky); time.sleep(0.4)
    reset = app.project()["tracks"][2]["macros"].get("1")
    check(dragged is not None and dragged < before and reset == before,
          "knobs: dragging Cutoff down went %d to %s; a double-click put it back (%s)" % (before, dragged, reset))
    app.click_rect(app.geom()["inst_steps"][1]); time.sleep(0.3)
    s.settle()
    inst = app.project()["tracks"][2]["instrument"]
    check(inst == "obxa", "knobs: the next-instrument button stepped Juno-106 to %s (OB-Xa follows it)" % inst)

    # -- files: Save asks once, then Ctrl+S doesn't; unsaved; Save first
    p = model.new_project()
    p["name"] = "saving test"
    s.reset(p)
    app.select(0)
    s.clear_files(); s.reset_pickers()
    grid = app.geom()["grid"]
    gx, gy, gw, gh = grid["rect"]
    s.mouse.click((gx + 0.5 * gw / grid["steps"]) / dpr, (gy + 0.5 * gh / len(grid["rows"])) / dpr); time.sleep(0.3)
    unsaved = "unsaved" in app.geom()["status_left"]
    menu(app, "save"); time.sleep(0.8)
    s.mouse.click((gx + 4.5 * gw / grid["steps"]) / dpr, (gy + 0.5 * gh / len(grid["rows"])) / dpr); time.sleep(0.3)
    s.keyboard.press("Control+s"); time.sleep(0.8)
    calls = s.picker_calls()
    saved = "unsaved" not in app.geom()["status_left"]
    check(unsaved and calls == 1 and saved, "files: unsaved shows (%s); Save asked once and Ctrl+S saved again without asking (%d picker); saved now (%s)"
          % (unsaved, calls, saved))
    s.mouse.click((gx + 8.5 * gw / grid["steps"]) / dpr, (gy + 0.5 * gh / len(grid["rows"])) / dpr); time.sleep(0.3)
    menu(app, "new"); time.sleep(0.4)
    g = app.geom()
    if "confirm" in g:
        app.click_rect(g["confirm"]["save"]); time.sleep(1.0)
    s.settle()
    q = app.project()
    stored = json.loads(s.file(".ostinato.json") or b"{}")
    hits = len(stored.get("tracks", [{}])[0].get("patterns", [{}])[0].get("notes", [])) if stored else 0
    check("confirm" in g and q["name"] == "Untitled" and hits == 3,
          "files: 'Save first' saved all three hits (%d) and then started the new project (%s)" % (hits, q["name"]))

    # -- keyboard: Forward, Loop, Rewind; Learn; octave
    q = model.new_project()
    tid = str(q["tracks"][2]["id"])
    q["tracks"][2]["patterns"] = [{"bars": 1, "notes": [[0, 24, 60, 100]]}]
    q["song"] = [{"patterns": {tid: 0}, "repeat": 1}] * 3
    q["song_mode"] = True
    s.reset(q)
    g = app.geom()
    app.click_rect(g["play"]); time.sleep(0.6)
    s.send({"kind": "cc", "chan": 0, "cc": 17, "value": 127}); time.sleep(0.6)        # Forward
    fwd = app.geom()["scenes"]["playing"]
    s.send({"kind": "cc", "chan": 0, "cc": 15, "value": 127}); time.sleep(0.4)        # Loop
    looped = app.project()["loop_scene"]
    s.send({"kind": "cc", "chan": 0, "cc": 15, "value": 127}); time.sleep(0.4)        # Loop again: off
    time.sleep(1.0)
    s.send({"kind": "cc", "chan": 0, "cc": 16, "value": 127}); time.sleep(0.4)        # Rewind
    tick = app.geom()["tick"]
    app.click_rect(app.geom()["stop"])
    check(fwd == 1 and looped == 1 and tick is not None and tick < model.BAR,
          "keyboard: Forward went to scene 2 (%s), Loop looped it (%s), Rewind went to the top (tick %s)"
          % (fwd, looped, tick and round(tick)))
    s.reset()
    app.click_rect(app.geom()["learn"]); time.sleep(0.4)
    said = app.geom()["status_right"]
    app.click_rect(app.geom()["learn"]); time.sleep(0.3)
    low0 = app.geom()["keys_low"]
    app.click_rect(app.geom()["keys_shift"][1]); time.sleep(0.4)
    low1 = app.geom()["keys_low"]
    check(said.startswith("Learn:") and low1 == low0 + 12,
          "keyboard: Learn says '%s...'; the on-screen keys moved up an octave (%d to %d)" % (said[:30], low0, low1))
    return out


def main():
    from checkkit import add_session_args, report, session
    ap = argparse.ArgumentParser(); add_session_args(ap); a = ap.parse_args()
    with session(a) as s:
        ok = report("workflow2", run(s))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
