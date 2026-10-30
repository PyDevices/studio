"""Check the knob scenario: the sound's brightness follows CC 31 (Cutoff).

    .venv/bin/python tools/knob_check.py runs/p3-knob

Computes the spectral centroid every 50 ms while the note holds, pairs it
with the CC value the page received at that moment, and passes if the two
rise and fall together (Spearman correlation above 0.8).
"""
import json, pathlib, sys
import numpy as np, soundfile

d = pathlib.Path(sys.argv[1])
x, sr = soundfile.read(d / "tap.wav", dtype="float32")
x = x.mean(axis=1) if x.ndim == 2 else x
run = json.loads((d / "run.json").read_text())
first = run["tap_first_frame"]
cc = [(m["ctxAt"], m["d"][2]) for m in run["midi_log"] if m["d"][0] == 0xB0 and m["d"][1] == 31]
ons = [m["ctxAt"] for m in run["midi_log"] if m["d"][0] == 0x90]
pairs = []
for t in ons:
    # The knob's value when this note started, and the note's brightness.
    before = [v for (tc, v) in cc if tc <= t]
    if not before:
        continue
    v = before[-1]
    i = int(t * sr) - first + sr // 20
    seg = x[i : i + 2048]
    if len(seg) < 2048 or np.max(np.abs(seg)) < 0.01:
        continue
    spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg))))
    f = np.fft.rfftfreq(len(seg), 1 / sr)
    pairs.append((v, float((spec * f).sum() / spec.sum())))
# (one point per note)
if len(pairs) < 10:
    print("FAIL  only %d measurable points" % len(pairs)); sys.exit(1)
v, c = np.array(pairs).T
rank = lambda a: np.argsort(np.argsort(a))
rho = np.corrcoef(rank(v), rank(c))[0, 1]
print("%s  %d points; brightness follows the knob, Spearman %.2f; centroid %.0f Hz at CC 0..10, %.0f Hz at CC 117..127" % (
    "PASS" if rho > 0.8 else "FAIL", len(pairs), rho, c[v <= 10].mean(), c[v >= 117].mean()))
sys.exit(0 if rho > 0.8 else 1)
