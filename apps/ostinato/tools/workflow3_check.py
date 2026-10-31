"""Frozen canvas, scene moves and names, and selecting several notes.

    .venv/bin/python tools/workflow3_check.py --cdp http://127.0.0.1:9222
    .venv/bin/python tools/workflow3_check.py --desktop processes|threads|threads-ft

  canvas    a resize changes neither the canvas nor anything else: no reload;
            on the desktop the window goes back to its size
  scenes    a scene moves down; clicking its name opens a field, typed text
            (Space and R included) names it without playing or arming record;
            capitals typed with Caps Lock stay capitals
  held      a held note follows a knob: Cutoff turned down while a mono_synth
            note sounds darkens that same note
  select    Shift-drag a box selects the notes in it; dragging one moves them
            all; Ctrl+C then Ctrl+V pastes them at the clicked step; Ctrl+D
            repeats them straight after; Ctrl+A and Delete clear the pattern;
            one undo brings each step back
"""
import argparse, json, pathlib, sys, time

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools")); sys.path.insert(0, str(ROOT.parent.parent / "lib"))
from songlink import model


def run(s):
    app = s.app
    out = []
    check = lambda ok, line: out.append((bool(ok), line))
    dpr = app.dpr

    # -- canvas: frozen, no reload on resize (on the desktop: the window goes back to its size)
    s.reset()
    before = app.geom()["canvas"]
    win0 = s.window()
    loads = s.navigations
    s.resize(0.8)
    after = app.geom()["canvas"]
    win1 = s.window()
    s.unresize()
    held = win1.get("client") == win0.get("client") if "maximized" in win0 else True
    check(after == before and s.navigations == loads and held,
          "canvas: a resize (to 80%%) left the canvas at %s and didn't reload (%d loads); the window %s -> %s"
          % (after, s.navigations - loads, win0.get("client"), win1.get("client")))

    # -- scenes: move, and a name typed in
    q = model.new_project()
    tid = str(q["tracks"][2]["id"])
    q["tracks"][2]["patterns"] = [{"bars": 1, "notes": [[0, 24, 60, 100]]}, model.pattern(), model.pattern()]
    q["song"] = [{"patterns": {tid: k}, "repeat": 1} for k in range(3)]
    q["song_mode"] = True
    s.reset(q)
    sc = app.geom()["scenes"]
    app.click_rect(sc["moves"][0][0]); time.sleep(0.4)                 # scene 1 down
    order = [sc_["patterns"][tid] for sc_ in app.project()["song"]]
    check(order == [1, 0, 2], "scenes: scene 1 moved down a place %s" % order)
    sc = app.geom()["scenes"]
    app.click_rect(sc["names"][2]); time.sleep(0.4)
    # As a person types it: Shift held for the capitals. A real Shift+B sends key "B" with
    # shiftKey; Playwright sends that for "Shift+B" (holding Shift over press("b") sends "b").
    for ch in "Big Drop r":
        if ch.isupper():
            s.keyboard.press("Shift+" + ch)
        else:
            s.keyboard.press("Space" if ch == " " else ch)
        time.sleep(0.03)
    time.sleep(0.3)
    s.keyboard.press("Enter"); time.sleep(0.5)
    name = app.project()["song"][2].get("name")
    playing = app.geom()["tick"] is not None
    armed = app.geom()["armed"] is not None
    check(name == "Big Drop r" and not playing and not armed,
          "scenes: scene 3 is named '%s'; typing didn't play (%s) or arm record (%s)" % (name, playing, armed))
    # Capitals without Shift are how Caps Lock types (and what keyboard.type sends).
    sc = app.geom()["scenes"]
    app.click_rect(sc["names"][1]); time.sleep(0.4)
    s.keyboard.type("INTRO"); time.sleep(0.3)
    s.keyboard.press("Enter"); time.sleep(0.5)
    caps = app.project()["song"][1].get("name")
    check(caps == "INTRO", "scenes: capitals typed the Caps Lock way stay capitals: '%s'" % caps)

    # -- held: a knob reaches the note already sounding
    s.reset()
    app.select(1)
    s.send({"kind": "instrument", "index": 1, "name": "mono_synth"}); time.sleep(0.3)
    s.settle()
    s.send({"kind": "cc", "chan": 0, "cc": 30, "value": 127}); time.sleep(0.3)      # Cutoff open
    x = app.record(2.6, before=lambda: app.play_midi([
        [200, [0x90, 45, 100]], [1300, [0xB0, 30, 5]], [2400, [0x80, 45, 0]]]))
    sr = 48000
    on = int(np.argmax(np.abs(x) > 0.01))

    def centroid(a, b):
        seg = x[on + int(a * sr): on + int(b * sr)]
        mag = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
        f = np.fft.rfftfreq(len(seg), 1 / sr)
        return float((mag * f).sum() / max(1e-9, mag.sum()))

    before, after = centroid(0.5, 1.0), centroid(1.5, 2.0)
    check(after < before * 0.6, "held: turning Cutoff down mid-note darkened that note (centroid %.0f Hz -> %.0f Hz)"
          % (before, after))

    # -- select several notes
    p = model.new_project()
    p["tracks"][2]["patterns"][0] = {"bars": 1, "notes": [[0, 24, 60, 100], [48, 24, 62, 100], [96, 24, 64, 100],
                                                          [288, 24, 67, 100]]}
    s.reset(p)
    app.select(2)
    roll = app.geom()["roll"]
    rx, ry, rw, rh = roll["rect"]
    cw, chh = rw / roll["steps"], rh / roll["rows"]
    at = lambda step, pitch: ((rx + (step + 0.5) * cw) / dpr, (ry + (roll["low"] + roll["rows"] - 1 - pitch + 0.5) * chh) / dpr)
    # a box over steps 0-5, pitches 59-65: the first three notes
    x0, y0 = at(0, 65); x1, y1 = at(5, 59)
    s.keyboard.down("Shift"); time.sleep(0.2)
    s.mouse.move(x0 - 2, y0 - 2); s.mouse.down()
    for k in range(1, 11):
        s.mouse.move(x0 + (x1 - x0) * k / 10, y0 + (y1 - y0) * k / 10); time.sleep(0.02)
    s.mouse.up(); s.keyboard.up("Shift"); time.sleep(0.4)
    chosen = app.geom()["selection"]
    check(len(chosen) == 3, "select: a Shift-drag box picked %d notes (want 3)" % len(chosen))
    # drag one of them up two semitones: all three move
    sx, sy = at(0, 60)
    s.mouse.move(sx, sy); s.mouse.down()
    for k in range(1, 6):
        s.mouse.move(sx, sy - 2 * chh / dpr * k / 5); time.sleep(0.03)
    s.mouse.up(); time.sleep(0.4)
    pitches = sorted(n[2] for n in app.project()["tracks"][2]["patterns"][0]["notes"])
    check(pitches == [62, 64, 66, 67], "select: dragging one moved all three up a tone: %s" % pitches)
    app.click_rect(app.geom()["undo"]); time.sleep(0.3)
    undone = sorted(n[2] for n in app.project()["tracks"][2]["patterns"][0]["notes"])
    # select the three again, copy, click an empty step, paste
    s.keyboard.down("Shift"); time.sleep(0.2)
    s.mouse.move(x0 - 2, y0 - 2); s.mouse.down()
    for k in range(1, 11):
        s.mouse.move(x0 + (x1 - x0) * k / 10, y0 + (y1 - y0) * k / 10); time.sleep(0.02)
    s.mouse.up(); s.keyboard.up("Shift"); time.sleep(0.3)
    s.keyboard.press("Control+c"); time.sleep(0.3)
    ex, ey = at(8, 50)
    s.mouse.click(ex, ey); time.sleep(0.3)                      # adds a note at step 8 (and sets where to paste)
    app.click_rect(app.geom()["undo"]); time.sleep(0.3)          # take that note back; the click point stays
    s.keyboard.press("Control+v"); time.sleep(0.5)
    notes = app.project()["tracks"][2]["patterns"][0]["notes"]
    pasted = sorted((n[0], n[2]) for n in notes if n[0] >= 8 * model.STEP and n[2] != 67)
    check(undone == [60, 62, 64, 67] and pasted == [(192, 60), (240, 62), (288, 64)],
          "select: undo put them back %s; Ctrl+C, a click at step 8, Ctrl+V pasted %s" % (undone, pasted))
    s.keyboard.press("Control+d"); time.sleep(0.5)
    notes = app.project()["tracks"][2]["patterns"][0]["notes"]
    later = sorted((n[0], n[2]) for n in notes if n[0] >= 13 * model.STEP and n[2] != 67)
    check(later == [(312, 60), (360, 62)], "select: Ctrl+D repeated the pasted notes straight after (what fits): %s" % later)
    s.keyboard.press("Control+a"); time.sleep(0.3)
    s.keyboard.press("Delete"); time.sleep(0.4)
    left = app.project()["tracks"][2]["patterns"][0]["notes"]
    app.click_rect(app.geom()["undo"]); time.sleep(0.3)
    back = len(app.project()["tracks"][2]["patterns"][0]["notes"])
    check(left == [] and back == len(notes), "select: Ctrl+A and Delete cleared it; one undo brought back %d notes" % back)
    return out


def main():
    from checkkit import add_session_args, report, session
    ap = argparse.ArgumentParser(); add_session_args(ap); a = ap.parse_args()
    with session(a) as s:
        ok = report("workflow3", run(s))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
