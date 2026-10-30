"""The SoundFont instruments in the app: songs/sketches/soundfont_check.json
opens, exports with every track heard, and plays; and without a bank the
tracks stay silent while the status line says so.

    .venv/bin/python tools/soundfont_check.py --desktop processes [--size 1280x720] [--no-bank]

The song puts soundfont_drums on Drums and soundfont on Bass, Keys and
Lead, each a General MIDI program from the bank the machine has
(%USERPROFILE%\\soundfonts, or $SOUNDFONT). The export is measured per
track, as tools/demo_check.py does, and six seconds of live playback are
listened to. ``--no-bank`` restarts the app with SOUNDFONT pointing at a
file that isn't there: the project must still open, the app must still
answer, the export must be silent and the status line must say which track
can't play what.
"""

import argparse
import json
import pathlib
import sys
import time

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from files_check import export, menu  # noqa: E402

SONG = ROOT / "songs" / "sketches" / "soundfont_check.json"


def open_song(s, app):
    s.reset()
    s.set_open(SONG.read_text(encoding="utf-8"))
    menu(app, "open")
    time.sleep(0.3)
    s.settle()
    return app.project()


def run(s):
    app = s.app
    out = []
    check = lambda ok, line: out.append((bool(ok), line))
    song = json.loads(SONG.read_text(encoding="utf-8"))
    p = open_song(s, app)
    check(p.get("name") == song["name"], "Open loads %r" % p.get("name"))
    check([t["instrument"] for t in p["tracks"]] == ["soundfont_drums", "soundfont", "soundfont", "soundfont"]
          and [t["patch"] for t in p["tracks"]] == [0, 33, 0, 48],
          "four tracks on the SoundFont instruments, kit 0 and programs 33, 0, 48")
    s.screenshot(str(ROOT / "runs" / "soundfont_check.png"))
    mix, sr = export(s, app)
    mono = np.abs(mix).mean(axis=1) if mix.ndim > 1 else np.abs(mix)
    bars = sum(song["tracks"][0]["patterns"][0]["bars"] * sc["repeat"] for sc in song["song"])
    want = bars * 4 * 60 / song["tempo"]
    secs = len(mono) / sr
    check(want < secs < want + 4, "the WAV is the song (%.1f s for %.1f s)" % (secs, want))
    last = s.last_export()
    for t, level in zip(song["tracks"], last["rms"]):      # track i renders on engine i
        db = 20 * np.log10(level + 1e-9)
        check(db > -40, "%s (%s, patch %d) is heard: %.1f dBFS" % (t["name"], t["instrument"], t["patch"], db))
    check(last["peak"] < 1.0, "the mix peaks under full scale (peak %.2f)" % last["peak"])
    x = app.record(6.0, before=lambda: app.key("SPACE"))
    app.key("SPACE"); time.sleep(0.3)
    rms = [float(np.sqrt(np.mean(x[i:i + sr] ** 2))) for i in range(sr, len(x) - sr, sr)]
    check(rms and min(rms) > 0.01, "it plays live: %.1f s heard, quietest second at %.3f RMS"
          % (len(x) / sr, min(rms) if rms else 0))
    return out


def run_no_bank(s):
    app = s.app
    out = []
    check = lambda ok, line: out.append((bool(ok), line))
    s.stop()
    s.env = {"SOUNDFONT": r"C:\nowhere\none.sf2", "WSLENV": "SOUNDFONT/w"}
    s.boot(fresh=True)
    p = open_song(s, app)
    check(p.get("name") == "SoundFont Check", "with no bank the song still opens (%r)" % p.get("name"))
    msg = app.geom().get("message", "")
    check("can't play" in msg and "soundfont" in msg, "the status line says so: %r" % msg)
    check(s.ask({"desk": "ping"}, "pong").get("ready"), "the app still answers")
    mix, sr = export(s, app)
    last = s.last_export()
    dbs = [20 * np.log10(level + 1e-9) for level in last["rms"]]
    check(all(db < -60 for db in dbs), "every track exports silence (%s dBFS)" % ", ".join("%.0f" % d for d in dbs))
    check(len(mix) / sr > 15, "and the export is the song's length (%.1f s)" % (len(mix) / sr))
    s.env = {}
    return out


def main():
    from checkkit import add_session_args, report, session

    ap = argparse.ArgumentParser()
    add_session_args(ap)
    ap.add_argument("--no-bank", action="store_true", help="also restart without a bank and check the tracks stay silent")
    a = ap.parse_args()
    if not a.desktop:
        sys.exit("this check is for the desktop app: --desktop processes|threads|threads-ft")
    with session(a) as s:
        results = run(s)
        if a.no_bank:
            results += run_no_bank(s)
        if s.log:
            print("app errors:", s.log[:6])
    ok = report("soundfont", results)
    (ROOT / "runs").mkdir(exist_ok=True)
    (ROOT / "runs" / "soundfont_check.json").write_text(json.dumps(results, indent=1))
    sys.exit(0 if ok and results else 1)


if __name__ == "__main__":
    main()
