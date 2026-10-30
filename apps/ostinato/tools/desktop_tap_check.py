"""Check a desktop spike run (desktop/app.py + desktop/send.py) the way
tap_check.py checks the browser: what the keyboard sent against what the
mixer wrote.

    .venv/bin/python tools/desktop_tap_check.py runs/d1 [--max-p99-ms 40] [--max-median-ms 25]

The mixer's tap says, for every block, when it was written and how much was
queued in front of it, so each frame's time leaving WASAPI's buffer is known
on the same clock (QueryPerformanceCounter) the sender stamped its keys with.
Key-to-sound runs from the sender's write into the loopback port to the
note's onset leaving WASAPI's buffer: the browser's equivalent point is the
AudioWorklet. WASAPI's own stream latency comes after, on its own line.

Checks, each shown failing on a planted fault (desktop/app.py --plant):

  notes    every key sent is heard once (onset in the live track's stream)
  pitch    each heard note within 50 cents
  latency  median and p99 under the limits (planted: delay holds note-ons 6 blocks)
  gaps     no device or engine underruns, no digital silence inside a note,
           no stretch of silence in the mix while the song plays
           (planted: drop writes one silent block a second)

Exit status 0 only if all pass.
"""

import argparse
import json
import pathlib
import sys

import numpy as np

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from tap_check import cents, envelope, note_hz, onset  # noqa: E402

THRESHOLD = 0.01
SEARCH_MS = 400
GAP_SAMPLES = 200             # Karplus's own waveform has flat zero stretches of up to ~100 samples;
                              # a lost engine block is 256
MIX_GAP = 200                 # exact zeros this long in the mix while the song plays (a lost block is 256)


def pct(x, q):
    return float(np.percentile(x, q)) if len(x) else float("nan")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--max-p99-ms", type=float, default=40.0)
    ap.add_argument("--max-median-ms", type=float, default=25.0)
    ap.add_argument("--max-cents", type=float, default=50.0)
    a = ap.parse_args()
    d = pathlib.Path(a.run)
    tap = json.loads((d / "tap.json").read_text())
    run = json.loads((d / "run.json").read_text())
    sent = json.loads((d / "sent.json").read_text())
    sr = tap["sample_rate"]
    lane = np.fromfile(d / "lane.raw", dtype="<i2").reshape(-1, 2).mean(axis=1) / 32768.0
    mix = np.fromfile(d / "mix.raw", dtype="<i2").reshape(-1, 2)
    times = np.fromfile(d / "times.bin", dtype="<i8").reshape(-1, 3)
    frames, wns, pad = times[:, 0], times[:, 1].astype(np.float64), times[:, 2]
    n = min(len(lane), len(times) * 256)
    lane = lane[:n]
    holes = int(np.count_nonzero(np.diff(frames) != 256))
    # When each block's first frame leaves WASAPI's buffer, in ns.
    play0 = wns + pad * 1e9 / sr

    def play_ns(i):
        k = i // 256
        return play0[k] + (i % 256) * 1e9 / sr

    def index_at(t_ns):
        k = int(np.searchsorted(play0, t_ns)) - 1
        k = max(0, k)
        return k * 256 + int(max(0.0, (t_ns - play0[k]) * sr / 1e9))

    env = envelope(lane, sr)
    ons = [(t, dd) for t, dd in sent if (dd[0] & 0xF0) == 0x90 and dd[2] > 0]
    heard, missing, lat, bad = [], [], [], []
    for k, (t, dd) in enumerate(ons):
        i0 = index_at(t)
        nxt = index_at(ons[k + 1][0]) if k + 1 < len(ons) else n
        lo, hi = max(0, i0 - sr // 200), min(n, i0 + sr * SEARCH_MS // 1000, nxt)
        if lo >= n:
            missing.append(dd)
            continue
        on = onset(lane, env, lo, hi, sr)
        if on is None:
            missing.append(dd)
            continue
        lat.append((play_ns(on) - t) / 1e6)
        seg = lane[on + sr // 50: on + sr // 50 + sr // 16]
        f = note_hz(seg, sr)
        c = cents(f, 440.0 * 2 ** ((dd[1] - 69) / 12))
        heard.append({"pitch": dd[1], "onset": on, "cents": c})
        if abs(c) > a.max_cents:
            bad.append((dd[1], round(f, 1)))
    # Onsets out of silence that no key asked for.
    rising = np.flatnonzero((env[1:] > THRESHOLD) & (env[:-1] <= THRESHOLD))
    starts = [r for r in rising if not np.any(env[max(0, r - sr // 20): r] > THRESHOLD)]
    onsets = np.array(sorted(h["onset"] for h in heard))
    extra = 0
    for r in starts:
        j = np.searchsorted(onsets, r)
        near = [abs(int(onsets[x]) - int(r)) for x in (j - 1, j) if 0 <= x < len(onsets)]
        if not near or min(near) > sr // 200:
            extra += 1
    results, rep = {}, []
    ok = not missing and not extra and len(ons) > 0
    results["notes"] = ok
    rep.append("notes    %s  sent %d, heard %d, missing %d, extra %d, tap holes %d" % (
        "PASS" if ok else "FAIL", len(ons), len(heard), len(missing), extra, holes))
    worst = max((abs(h["cents"]) for h in heard), default=float("nan"))
    ok = not bad and bool(heard)
    results["pitch"] = ok
    rep.append("pitch    %s  %d notes, worst %.0f cents%s" % ("PASS" if ok else "FAIL", len(heard), worst,
                                                              (", off: %s" % bad[:5]) if bad else ""))
    la = np.array(lat)
    p50, p99, mx = pct(la, 50), pct(la, 99), (la.max() if len(la) else float("nan"))
    ok = bool(len(la)) and p50 <= a.max_median_ms and p99 <= a.max_p99_ms
    results["latency"] = ok
    rep.append("latency  %s  key-to-sound median %.1f ms, p99 %.1f ms, max %.1f ms (limits %.0f / %.0f)" % (
        "PASS" if ok else "FAIL", p50, p99, mx, a.max_median_ms, a.max_p99_ms))
    w = tap.get("wasapi", {})
    rep.append("         + WASAPI after the tap: stream latency %s ms, device period %s ms (min %s), buffer %s frames" % (
        w.get("stream_latency_ms"), w.get("default_period_ms"), w.get("min_period_ms"), w.get("buffer_frames")))
    # gaps
    zero = lane == 0.0
    inner = 0
    for h in heard:
        z = zero[h["onset"]: h["onset"] + sr * 120 // 1000]
        if len(z):
            e = np.diff(np.concatenate([[0], z.astype(np.int8), [0]]))
            inner += int(((np.flatnonzero(e == -1) - np.flatnonzero(e == 1)) >= GAP_SAMPLES).sum())
    mz = np.all(mix == 0, axis=1)
    song = mz[sr * 2: len(mz) - sr]             # skip the start (the song comes in) and the end
    e = np.diff(np.concatenate([[0], song.astype(np.int8), [0]]))
    mix_gaps = int(((np.flatnonzero(e == -1) - np.flatnonzero(e == 1)) >= MIX_GAP).sum())
    r = tap.get("report") or {}
    dev, lanes, late = r.get("underruns"), r.get("lane_underruns"), r.get("late")
    ok = inner == 0 and mix_gaps == 0 and dev == 0 and not any(lanes or []) and holes == 0
    results["gaps"] = ok
    rep.append("gaps     %s  device underruns %s, engine underruns %s, late mixes %s, silent runs in notes %d, "
               "in the mix %d" % ("PASS" if ok else "FAIL", dev, lanes, late, inner, mix_gaps))
    secs = len(times) * 256 / sr
    rep.append("run      %s, %s, GIL %s, UI %s, %.0f s tapped, queue target at end %s frames" % (
        run["engines"], run["python"].split()[0], run["gil"], run["ui"], secs, tap.get("target_end")))
    def series(name):          # name.f32 beside tap.json (older runs: a list in it)
        f = d / (name + ".f32")
        return np.fromfile(f, dtype="<f4") if f.exists() else np.array(tap.get(name) or [])

    wl, rf, lw = series("wake_late_ms"), series("refill_ms"), series("low_ms")
    rep.append("mixer    wake lateness p50 %.2f p99 %.2f max %.2f ms; refill after a device pull p50 %.2f p99 %.2f "
               "max %.2f ms; queue low point min %.2f p1 %.2f ms" % (
                   pct(wl, 50), pct(wl, 99), wl.max() if len(wl) else float("nan"), pct(rf, 50), pct(rf, 99),
                   rf.max() if len(rf) else float("nan"), lw.min() if len(lw) else float("nan"), pct(lw, 1)))
    tg = [s.get("target") for s in run.get("stats", []) if s.get("target")]
    if tg:
        rep.append("queue    target min %d, median %d, max %d frames over the run" % (
            min(tg), int(np.median(tg)), max(tg)))
    loads = [s["loads"] for s in run.get("stats", []) if s.get("loads")]
    if loads:
        cols = list(zip(*loads))
        rep.append("engines  load (share of real time) median per engine: %s" % (
            ", ".join("%.2f" % float(np.median([x for x in c if x is not None] or [0])) for c in cols)))
    lv = np.array(run.get("lv_frames") or [])
    if len(lv):
        rep.append("lvgl     pass p50 %.1f ms, p99 %.1f ms, max %.1f ms (%d samples)" % (
            pct(lv, 50), pct(lv, 99), lv.max(), len(lv)))
    print("\n".join(rep))
    summary = {"results": results, "latency_ms": {"p50": p50, "p99": p99, "max": mx}, "notes": len(ons),
               "heard": len(heard), "missing": len(missing), "extra": extra, "device_underruns": dev,
               "engine_underruns": lanes, "late": late, "inner_gaps": inner, "mix_gaps": mix_gaps,
               "wake_late_ms": {"p50": pct(wl, 50), "p99": pct(wl, 99)},
               "refill_ms": {"p50": pct(rf, 50), "p99": pct(rf, 99), "max": float(rf.max()) if len(rf) else None},
               "low_ms_min": float(lw.min()) if len(lw) else None, "wasapi": w, "report": rep}
    (d / "check.json").write_text(json.dumps(summary, indent=1, default=float))
    sys.exit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
