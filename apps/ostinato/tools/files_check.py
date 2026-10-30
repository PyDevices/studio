"""Phase 5 (files): its gate, through the real UI in Chrome.

    .venv/bin/python tools/files_check.py --cdp http://127.0.0.1:9222
    .venv/bin/python tools/files_check.py --desktop processes|threads|threads-ft

  song     song mode plays three bass patterns in order (the live take)
  save     File > Save writes the project; File > Open, on a new project,
           brings it back exactly
  autosave an edit survives a reload with no saving at all
  wav      File > Export WAV renders the song offline: same notes, in order,
           as the live playback, level within 1 dB; render speed recorded
  midi     File > Export MIDI writes a file mido reads back with the song's
           notes, timing and tempo

The browser's own pickers can't be clicked by a script, so
tools/fake_pickers.js stands in for them (on the desktop, a folder stands in
for the dialogs: --test-files); everything else is the real UI.
"""

import argparse
import base64
import io
import json
import math
import pathlib
import subprocess
import sys
import time

import numpy as np
import soundfile
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT.parent.parent / "lib"))
from groovebox_check import App, SR, STEP_S, onsets, pitches_at  # noqa: E402
from songlink import model  # noqa: E402

BAR_S = STEP_S * 16
MENU = {"new": 0, "demo": 1, "open": 2, "save": 3, "save-as": 4, "wav": 5, "midi": 6}


def song_project():
    p = model.new_project()
    p["name"] = "files test"
    drums, bass = p["tracks"][0], p["tracks"][1]
    # Kicks off the bar starts, so each bar's first sound is the bass alone.
    drums["patterns"][0]["notes"] = [[96, 24, 36, 120], [288, 24, 36, 120]]
    bass["patterns"] = [{"bars": 1, "notes": [[0, 96, 45, 110]]}, {"bars": 1, "notes": [[0, 96, 52, 110]]},
                        {"bars": 1, "notes": [[0, 96, 57, 110]]}]
    p["song"] = [{"patterns": {str(drums["id"]): 0, str(bass["id"]): i}, "repeat": 1} for i in range(3)]
    p["song_mode"] = True
    return p


LOG = []     # a check may fill this with the page's console, for a failed export


def export(s, app, shot=None):
    s.clear_files()
    menu(app, "wav")
    t0 = time.monotonic()
    if shot:
        time.sleep(8)
        s.screenshot(str(ROOT / "runs" / shot))
    while s.file(".wav") is None:
        if time.monotonic() - t0 > 300:
            s.screenshot(str(ROOT / "runs" / "demo_export_failed.png"))
            print("\n".join(LOG[-15:]))
            raise TimeoutError("the WAV export never finished (screenshot: runs/demo_export_failed.png)")
        time.sleep(2)
        if int(time.monotonic() - t0) % 10 < 2:
            print("      rendering, %.0f s: %s" % (time.monotonic() - t0, s.export_progress()), flush=True)
    wav, sr = soundfile.read(io.BytesIO(s.file(".wav")), dtype="float32")
    return wav, sr


def menu(app, item):
    app.click_rect(app.geom()["file"])
    time.sleep(0.3)
    app.click_rect(app.geom()["menu"][MENU[item]])
    time.sleep(0.3)


def run(s):
    """Every part, in session ``s`` (a checkkit session, the browser's or the
    desktop app's); returns the results. Also reports "song": the live take
    is song mode playing three patterns in order, the groovebox's song check."""
    results = {}
    proj = song_project()
    s.reset(proj)
    app = s.app
    g = app.geom()
    live = app.record(7.2, before=lambda: app.click_rect(g["play"]))
    app.click_rect(g["stop"])
    live_ons = onsets(live, gap_s=1.0)
    live_ps = pitches_at(live, live_ons[0], BAR_S, 3) if live_ons else []
    results["song"] = {"pass": live_ps == [45, 52, 57], "pitches": live_ps,
                       "peak": round(float(np.max(np.abs(live))), 4) if len(live) else None}

    # -- save
    s.clear_files()
    menu(app, "save")
    saved = s.wait_file(".ostinato.json").decode("utf-8")

    # -- wav, from the same page
    wav, wsr = export(s, app)
    speed = s.last_export()
    wmono = wav.mean(axis=1)
    w_ons = onsets(wmono, gap_s=1.0)
    w_ps = pitches_at(wmono, w_ons[0], BAR_S, 3) if w_ons else []
    seg = lambda x, o: x[o: o + int(0.3 * SR)]
    lv = [float(np.sqrt(np.mean(seg(live, o) ** 2))) for o in live_ons[:3]]
    wl = [float(np.sqrt(np.mean(seg(wmono, o) ** 2))) for o in w_ons[:3]]
    db = max(abs(20 * math.log10(b / a)) for a, b in zip(lv, wl)) if lv and wl else None
    results["wav"] = {"pass": w_ps == live_ps == [45, 52, 57] and db is not None and db <= 1.0,
                      "live": live_ps, "wav": w_ps, "level_diff_db": round(db, 2) if db is not None else None,
                      "seconds": speed and round(speed["seconds"], 2),
                      "x_realtime": speed and round(speed["seconds"] / (speed["renderMs"] / 1000), 1)}
    soundfile.write(str(ROOT / "runs" / "export.wav"), wav, wsr)

    # -- midi
    menu(app, "midi")
    mid = s.wait_file(".mid")
    (ROOT / "runs" / "export.mid").write_bytes(mid)
    import mido
    mf = mido.MidiFile(file=io.BytesIO(mid))
    tempo = next(m.tempo for m in mf.tracks[0] if m.type == "set_tempo")
    bass = []
    t = 0
    for m in mf.tracks[2]:
        t += m.time
        if m.type == "note_on" and m.velocity:
            bass.append((t, m.note))
    results["midi"] = {"pass": bass == [(0, 45), (384, 52), (768, 57)] and round(mido.tempo2bpm(tempo)) == 110,
                       "bass": bass, "bpm": round(mido.tempo2bpm(tempo), 2), "tracks": len(mf.tracks)}

    # -- open: a new project first, so everything that comes back came from the file.
    # (The same project plays the same notes: the live take above already heard them.)
    s.reset()
    s.set_open(saved)
    menu(app, "open")
    time.sleep(0.3)
    s.settle()
    back = app.project()
    same = json.dumps(back["tracks"], sort_keys=True) == json.dumps(json.loads(saved)["tracks"], sort_keys=True)
    results["save"] = {"pass": same and back.get("name") == proj["name"], "project_identical": same,
                       "name": back.get("name")}

    # -- autosave: an edit, a reload, and it's still there
    app.select(1)
    g = app.geom()
    roll = g.get("roll")
    if roll and back.get("song_mode"):
        app.click_rect(g["song"])          # back to pattern view to edit
        time.sleep(0.5)
        g = app.geom()
        roll = g.get("roll")
    if roll:
        rx, ry, rw, rh = roll["rect"]
        row = roll["low"] + roll["rows"] - 1 - 60
        app.click(rx + 8.5 * rw / roll["steps"], ry + (row + 0.5) * rh / roll["rows"])
    time.sleep(3.5)                        # autosave waits for 1.5 s of quiet
    edited = app.project()
    # Start again, not fresh (a reload at the plain address; the desktop app
    # quits and starts). The one restart in the checks: surviving it is the point.
    s.restart()
    time.sleep(1)
    restored = s.app.project()
    diff = [(e["name"], [len(p["notes"]) for p in e["patterns"]], [len(p["notes"]) for p in r["patterns"]])
            for e, r in zip(edited["tracks"], restored["tracks"]) if json.dumps(e, sort_keys=True) != json.dumps(r, sort_keys=True)]
    results["autosave"] = {"pass": bool(roll) and not diff and json.dumps(edited["tracks"]) != json.dumps(back["tracks"]),
                           "edit_made": json.dumps(edited["tracks"]) != json.dumps(back["tracks"]),
                           "differs": diff, "restored_name": restored.get("name")}
    return results


def main():
    import argparse
    from checkkit import add_session_args, report, session
    ap = argparse.ArgumentParser()
    add_session_args(ap)
    a = ap.parse_args()
    with session(a) as s:
        results = run(s)
    ok = report("files", results)
    (ROOT / "runs" / "files_check.json").write_text(json.dumps(results, indent=1))
    sys.exit(0 if ok and results else 1)


if __name__ == "__main__":
    main()
