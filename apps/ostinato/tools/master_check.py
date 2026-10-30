"""The master track: its effects and volume reach the sound, live and exported.

    .venv/bin/python tools/master_check.py --cdp http://127.0.0.1:9222
    .venv/bin/python tools/master_check.py --desktop processes|threads|threads-ft

Hats on every sixteenth, through the real app:

  live    a master LowPass, turned down, takes the treble off what plays
          (the output's own tap: the AudioWorklet's, or the desktop mixer's): at least 12 dB less above 4 kHz
  export  the same in a WAV export, and master volume 50 is 6 dB down
  swing   off-beat hats land half a sixteenth late at 75% swing in the
          export, and on time straight (the master's dry export)
  toggle  switching master effects on and off while the demo song plays,
          with a Reverb on the master, drops nothing
  meters  hats on the Drums alone light the Drums and Master meters, left
          and right, and leave the other tracks dark; muted, Drums goes dark
          (read during the dry live take)
  ui      the Master row opens the master rack in the inspector
          (runs/master_inspector.png)
"""
import argparse, json, pathlib, sys, time
import numpy as np
import soundfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools")); sys.path.insert(0, str(ROOT.parent.parent / "lib"))
from songlink import model
from files_check import export

LOWPASS = {"name": "LowPass", "bypass": False, "macros": {"0": 20}}


def hats(master_rack=(), volume=100):
    p = model.new_project()
    p["tracks"][0]["patterns"][0]["notes"] = [[k * model.STEP, 12, 42, 110] for k in range(16)]
    p["master"] = {"volume": volume, "rack": [dict(e) for e in master_rack]}
    return p


def treble_db(x, sr):
    """Energy above 4 kHz relative to all of it, in dB."""
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    f = np.fft.rfftfreq(len(x), 1 / sr)
    return 10 * np.log10(spec[f > 4000].sum() / spec.sum() + 1e-12)


def rms_db(x):
    return 20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-12)


def hats_late(wav, sr, tempo):
    """How late the off-beat hats start, in sixteenths, against the on-beat ones."""
    x = np.abs(wav.mean(axis=1))
    step = sr * 60 / tempo / 4
    th = 0.2 * x.max()
    starts = []
    for k in range(16):
        seg = x[int(k * step): int((k + 1) * step + step * 0.6)]
        starts.append(int(np.argmax(seg > th)) / step)
    return np.mean(starts[1::2]) - np.mean(starts[0::2])


def readings(s):
    """Each track's and the master's loudest meter reading over a few polls."""
    vals = []
    for _ in range(6):                    # hats are short: take the loudest
        vals.append(s.app.masterstats() or {})
        time.sleep(0.25)
    tracks = {}
    for m in vals:
        for k, v in (m.get("meters") or {}).items():
            old = tracks.get(k, [-100, -100])
            tracks[k] = [max(old[0], v[0]), max(old[1], v[1])]
    master = [max(m.get("meter", [-100, -100])[i] for m in vals) for i in (0, 1)]
    return tracks, master


def run(s):
    app = s.app
    out = []
    check = lambda ok, line: out.append((ok, line))

    # -- live: the tap, with and without a master LowPass; the meters on the dry take
    live = {}
    for name, rack in (("dry", ()), ("lowpass", (LOWPASS,))):
        s.reset(hats(rack))
        ids = [str(t["id"]) for t in app.project()["tracks"]]
        g = app.geom()
        app.click_rect(g["play"]); time.sleep(1.5)
        read = []
        x = app.record(2.0, before=(lambda: read.append(readings(s))) if name == "dry" else None)
        if name == "dry":
            tracks, master = read[0]
            drums = tracks.get(ids[0], [-100, -100])
            quiet = [max(tracks.get(k, [-100, -100])) for k in ids[1:]]
            check(min(drums) > -40 and min(master) > -40 and max(quiet) <= -90,
                  "meters: Drums %s dB, Master %s dB, the others %s" % (drums, master, quiet))
        live[name] = treble_db(x, 48000)
        if name == "dry":
            s.op({"op": "set", "path": [["track", int(ids[0])], "mute"], "value": True})
            time.sleep(1.0)
            tracks, master = readings(s)
            muted = max(tracks.get(ids[0], [-100, -100]))
            check(muted <= -90 and max(master) <= -90,
                  "meters: Drums muted reads %s dB, Master %s dB" % (muted, max(master)))
        app.click_rect(g["stop"])
    drop = live["dry"] - live["lowpass"]
    check(drop >= 12, "live: the master LowPass takes %.1f dB off the treble (want 12 or more)" % drop)

    # -- export: the same, master volume, and swing (the dry export is the straight one)
    exp = {}
    for name, p in (("dry", hats()), ("lowpass", hats((LOWPASS,))), ("half", hats(volume=50)),
                    ("swung", dict(hats(), swing=50))):
        s.load(p)
        exp[name], sr = export(s, app)
    drop = treble_db(exp["dry"].mean(axis=1), sr) - treble_db(exp["lowpass"].mean(axis=1), sr)
    check(drop >= 12, "export: the master LowPass takes %.1f dB off the treble" % drop)
    # The export normalises only above 0.97: hats stay under it.
    diff = rms_db(exp["dry"].mean(axis=1)) - rms_db(exp["half"].mean(axis=1))
    check(abs(diff - 6.0) < 0.5, "export: master volume 50 is %.2f dB down (want 6.0)" % diff)
    tempo = model.new_project()["tempo"]
    straight, swung = hats_late(exp["dry"], sr, tempo), hats_late(exp["swung"], sr, tempo)
    check(abs(straight) < 0.05 and abs(swung - 0.5) < 0.05,
          "swing: off-beat hats late by %.2f of a sixteenth straight, %.2f at 75%% (want 0, 0.5)" % (straight, swung))

    # -- toggle: master effects on and off while the demo plays
    demo = json.loads((ROOT / "assets" / "demo_song.json").read_text())
    demo["master"]["rack"] = [{"name": "Reverb", "bypass": False, "macros": {}}]
    s.load(demo)
    g = app.geom()
    under_now = lambda: s.stats()["underruns"]
    s0 = under_now()
    app.click_rect(g["play"]); time.sleep(2)
    steps = [("bypass %s" % ("on" if k % 2 == 0 else "off"),
              {"op": "set", "path": ["master", "rack", 0, "bypass"], "value": k % 2 == 0}) for k in range(4)]
    steps.append(("add LowPass", {"op": "insert", "path": ["master", "rack", -1], "value": dict(LOWPASS)}))
    per = []
    at = s0
    for name, o in steps:
        s.op(o)
        time.sleep(1.5)
        now = under_now()
        per.append("%s %d" % (name, now - at))
        at = now
    app.click_rect(g["stop"])
    check(at - s0 == 0, "toggle: 4 bypass switches and an added effect while playing, %d dropouts (%s)"
          % (at - s0, ", ".join(per)))

    # -- ui: the Master row shows the master rack
    rows = app.geom()["tracks"]
    app.click_rect(rows[-1]); time.sleep(0.5)
    s.screenshot(str(ROOT / "runs" / "master_inspector.png"))
    return out


def main():
    from checkkit import add_session_args, report, session
    ap = argparse.ArgumentParser(); add_session_args(ap); a = ap.parse_args()
    with session(a) as s:
        ok = report("master", run(s))
    print("(the inspector: runs/master_inspector.png)")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
