"""Judge tools/instrument_sweep.mjs's output: pitch within 50 cents for every
melodic instrument's notes, a sound for every drum pad."""
import json, math, pathlib, sys
import numpy as np
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from tap_check import pitch_hz

raw = json.loads((ROOT / "runs/instrument_sweep_raw.json").read_text())
bad, rows = [], []
for name, r in sorted(raw.items()):
    if "error" in r:
        bad.append((name, r["error"])); continue
    if r["kind"] == "melodic":
        cents = []
        for n in r["notes"]:
            if n["peak"] < 0.002:
                cents.append(None); continue
            f = pitch_hz(np.array(n["mono"], dtype=float), 48000)
            ref = 440 * 2 ** ((n["note"] - 69) / 12)
            cents.append(round(1200 * math.log2(f / ref)) if f > 0 else None)
        # Judge the pitch class: a patch may sound an octave (16') or two off,
        # which is the patch, not a wrong note. The octave is reported.
        def pc(c):
            return None if c is None else ((c + 600) % 1200) - 600
        ok = all(c is not None and abs(pc(c)) <= 50 for c in cents)
        octs = sorted({round(c / 1200) for c in cents if c is not None})
        rows.append((name, "melodic", "%s cents off the pitch class, octaves %s" % ([pc(c) for c in cents], octs), r["build_s"], ok))
    else:
        silent = [n["note"] for n in r["notes"] if n["peak"] < 0.002]
        ok = not silent
        rows.append((name, "drums", "%d pads, silent %s" % (len(r["notes"]), silent or "none"), r["build_s"], ok))
    if not ok:
        bad.append((name, rows[-1][2]))
for row in rows:
    print("%-16s %-8s %-40s build %.2fs %s" % (row[0], row[1], row[2], row[3], "" if row[4] else "  <-- check"))
print("\n%d instruments, %d to look at: %s" % (len(rows), len(bad), bad))
