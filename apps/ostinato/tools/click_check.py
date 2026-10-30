"""Check the metronome in a tap: every beat there, on time, downbeats higher.

    .venv/bin/python tools/click_check.py runs/p2-metronome [--bpm 110]

Finds each click's onset, then checks that consecutive clicks are one beat
apart to within one 256-frame block, that none is missing, and that every
fourth is the high (1760 Hz) downbeat click and the rest the low (1320 Hz).
"""

import argparse
import json
import pathlib
import sys

import numpy as np
import soundfile


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--bpm", type=float, default=110.0)
    a = ap.parse_args()
    d = pathlib.Path(a.run)
    x, sr = soundfile.read(d / "tap.wav", dtype="float32")
    mono = x.mean(axis=1) if x.ndim == 2 else x
    w = sr // 1000
    env = np.sqrt(np.convolve(mono * mono, np.ones(w) / w, "same"))
    above = env > 0.02
    rises = np.flatnonzero(above[1:] & ~above[:-1]) + 1
    onsets = [r for i, r in enumerate(rises) if i == 0 or r - rises[i - 1] > sr // 10]
    beat = 60.0 / a.bpm * sr
    gaps = np.diff(onsets)
    err = gaps - beat
    missing = int(np.sum(np.round(gaps / beat) > 1))
    pitches = []
    for o in onsets:
        seg = mono[o + sr // 500 : o + sr // 500 + sr // 50]
        spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), 1 << 14))
        pitches.append(np.argmax(spec) * sr / (1 << 14))
    high = [p > 1540 for p in pitches]
    # The pattern should be high, low, low, low, repeating, from some phase.
    phase_ok = any(all(h == ((i + k) % 4 == 0) for i, h in enumerate(high)) for k in range(4))
    ok_timing = len(err) > 0 and np.max(np.abs(err)) <= 256 and missing == 0
    print("clicks %d over %.1f s; beat %.1f frames; worst timing error %.1f frames (limit 256); missing %d" % (
        len(onsets), len(mono) / sr, beat, np.max(np.abs(err)) if len(err) else float("nan"), missing))
    print("downbeat pattern %s (high clicks %d of %d)" % ("OK" if phase_ok else "WRONG", sum(high), len(high)))
    run = json.loads((d / "run.json").read_text())
    if run.get("stats"):
        print("worklet underruns %d, final queue target %d" % (run["stats"][-1]["underruns"], run["stats"][-1]["target"]))
    (d / "check.json").write_text(json.dumps({"clicks": len(onsets), "worst_error_frames": float(np.max(np.abs(err))) if len(err) else None,
                                              "missing": missing, "downbeats_ok": phase_ok}))
    sys.exit(0 if ok_timing and phase_ok else 1)


if __name__ == "__main__":
    main()
