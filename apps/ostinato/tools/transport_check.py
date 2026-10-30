"""Check the transport scenario: CC 19 (Play) starts, CC 18 (Stop) stops.

    .venv/bin/python tools/transport_check.py runs/p3-transport

With the metronome on, clicks must sound after each Play and stop after each
Stop (allowing 200 ms either side for the transport's start margin).
"""
import json, pathlib, sys
import numpy as np, soundfile

d = pathlib.Path(sys.argv[1])
x, sr = soundfile.read(d / "tap.wav", dtype="float32")
x = x.mean(axis=1) if x.ndim == 2 else x
run = json.loads((d / "run.json").read_text())
first = run["tap_first_frame"]
evs = [(m["ctxAt"], m["d"][1]) for m in run["midi_log"] if m["d"][0] == 0xB0 and m["d"][2] > 0]
env = np.abs(x) > 0.02
def sound(t0, t1):
    a, b = int(t0 * sr) - first, int(t1 * sr) - first
    return bool(env[max(0, a):max(0, b)].any())
ok = True
for (t, cc), nxt in zip(evs, evs[1:] + [(evs[-1][0] + 3, None)]):
    if cc == 19:
        heard = sound(t + 0.2, nxt[0])
        print("Play at %.2f s: clicks %s" % (t, "heard" if heard else "MISSING"))
        ok &= heard
    elif cc == 18:
        quiet = not sound(t + 0.3, nxt[0] - 0.05)
        print("Stop at %.2f s: %s" % (t, "silent after" if quiet else "STILL SOUNDING"))
        ok &= quiet
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
