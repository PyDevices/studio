"""Check what Ostinato played against what the keyboard sent.

    .venv/bin/python tools/tap_check.py runs/p0-a [--max-p99-ms 40]

Reads ``tap.wav`` and ``run.json`` from tools/midi_play.py and runs four
checks, each of which has been shown to fail on a planted fault
(midi_play.py --plant):

  notes    every note-on the keyboard sent was heard, once, in order: an
           onset out of silence, or out of the last note's release tail
           (planted: lose-note drops every tenth note-on)
  pitch    each heard note is within 50 cents of the note sent; a
           sub-oscillator an octave down doesn't count as the note
           (planted: detune plays every note a semitone sharp; an octave
           up or down fails too)
  latency  key-to-sound: from when the key was pressed to the onset in
           the tap, median and 99th percentile; fails above --max-p99-ms
           (planted: delay holds every note-on back six blocks, 32 ms)
  gaps     no run of exact digital silence inside a sounding note, and the
           worklet counted no underruns
           (planted: drop-block silences one quantum every second)

The browser's own output latency comes after the tap and is reported on
its own line, never folded into the number.

Exit status 0 only if all four pass.
"""

import argparse
import json
import math
import pathlib
import sys

import numpy as np
import soundfile

THRESHOLD = 0.01        # onset: the 1 ms envelope crosses this
TAIL_MS = 20            # the level just before a key: the last note's release, if any
RISE = 1.5              # out of a tail, a note goes on to this much louder than it
QUIET_MS = 10           # a gap this long in a note's loud samples: what came before was the tail
SUB_RATIO = 4.0         # even/odd partial power above this: the reading was a sub-octave
SEARCH_MS = 400         # look this far after the key for its onset
GAP_SAMPLES = 32        # a run of exact zeros this long inside a note is a gap


def envelope(x, sr):
    w = max(1, sr // 1000)
    return np.sqrt(np.convolve(x * x, np.ones(w) / w, "same"))


def pitch_hz(seg, sr, lo=40.0, hi=2500.0, threshold=0.2):
    """Fundamental frequency by YIN (de Cheveigne & Kawahara, 2002): the
    cumulative-mean-normalised difference function's first dip below
    ``threshold``, refined by a parabola. Unlike a plain autocorrelation
    peak, it doesn't jump to a multiple of the period on bright, high notes."""
    x = np.asarray(seg, dtype=float)
    x = x - x.mean()
    if not np.any(x):
        return 0.0
    tau_max = min(len(x) // 2, int(sr / lo))
    tau_min = max(2, int(sr / hi))
    w = len(x) - tau_max
    n = 1 << int(math.ceil(math.log2(len(x) * 2)))
    f = np.fft.rfft(x, n)
    ac = np.fft.irfft(f * np.conj(f))[: tau_max + 1]          # autocorrelation over the full frame
    sq = np.concatenate([[0.0], np.cumsum(x * x)])
    energy0 = sq[w] - sq[0]
    d = np.empty(tau_max + 1)
    for tau in range(tau_max + 1):
        d[tau] = energy0 + (sq[tau + w] - sq[tau]) - 2 * np.dot(x[:w], x[tau : tau + w])
    cmnd = np.ones_like(d)
    cmnd[1:] = d[1:] * np.arange(1, tau_max + 1) / np.maximum(np.cumsum(d[1:]), 1e-12)
    tau = None
    for t in range(tau_min, tau_max):
        if cmnd[t] < threshold:
            while t + 1 < tau_max and cmnd[t + 1] < cmnd[t]:
                t += 1
            tau = t
            break
    if tau is None:
        tau = tau_min + int(np.argmin(cmnd[tau_min:tau_max]))
    if 0 < tau < tau_max:
        y0, y1, y2 = cmnd[tau - 1], cmnd[tau], cmnd[tau + 1]
        den = y0 - 2 * y1 + y2
        tau = tau + (0.5 * (y0 - y2) / den if den else 0.0)
    return sr / tau


def partials(seg, sr, f, n=8):
    """Magnitudes of the first ``n`` harmonics of ``f`` in ``seg`` (the
    largest bin within 3 % of each)."""
    x = np.asarray(seg, dtype=float)
    size = 1 << 16
    spec = np.abs(np.fft.rfft((x - x.mean()) * np.hanning(len(x)), size))
    freqs = np.fft.rfftfreq(size, 1.0 / sr)
    out = np.zeros(n)
    for k in range(1, n + 1):
        band = (freqs > k * f * 0.97) & (freqs < k * f * 1.03)
        if band.any():
            out[k - 1] = spec[band].max()
    return out


def note_hz(seg, sr):
    """The pitch a note is played at. A patch with a sub-oscillator (the
    Juno-106's, an octave down) repeats at half the note's frequency, so the
    period YIN finds can be the sub's. Then the reading's even harmonics,
    which are the note's own, carry far more power than its odd ones, which
    are only the sub's: the note is an octave up. A tone that really is at
    the reading has its fundamental and odd harmonics in proportion and is
    left alone, and a wrong note or a wrong octave moves every partial, so
    neither is excused."""
    f = pitch_hz(seg, sr)
    for _ in range(2):
        if f <= 0 or 8 * f > sr / 2:
            break
        p = partials(seg, sr, f) ** 2
        odd, even = p[0::2].sum(), p[1::2].sum()
        if odd <= 0 or even / odd < SUB_RATIO:
            break
        f *= 2
    return f


def onset(x, env, lo, hi, sr):
    """Where the note starting in ``x[lo:hi]`` begins, or None. Out of
    silence: the 1 ms envelope crossing THRESHOLD. Out of the last note's
    release tail, an absolute threshold finds the tail itself, ahead of the
    key; there the onset is where the samples get louder than any in the
    tail's last TAIL_MS, in the run that goes on to RISE times that."""
    base = float(np.abs(x[max(0, lo - sr * TAIL_MS // 1000) : lo]).max()) if lo > 0 else 0.0
    if base < THRESHOLD:
        above = np.flatnonzero(env[lo:hi] > THRESHOLD)
        return lo + int(above[0]) if len(above) else None
    mag = np.abs(x[lo:hi])
    rise = np.flatnonzero(mag > base * RISE)
    if not len(rise):
        return None
    # Back from there to the start of its run of loud samples: a swell in the
    # tail (detuned oscillators beating) can poke above it earlier, but is
    # followed by QUIET_MS without, where a note's waveform never is.
    over = np.flatnonzero(mag[: rise[0] + 1] > base)
    breaks = np.flatnonzero(np.diff(over) > sr * QUIET_MS // 1000)
    return lo + int(over[breaks[-1] + 1] if len(breaks) else over[0])


def cents(f, ref):
    return 1200 * math.log2(f / ref) if f > 0 else float("inf")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--max-p99-ms", type=float, default=40.0)
    ap.add_argument("--max-cents", type=float, default=50.0)
    a = ap.parse_args()
    d = pathlib.Path(a.run)
    x, sr = soundfile.read(d / "tap.wav", dtype="float32")
    if x.ndim == 2:
        x = x.mean(axis=1)
    run = json.loads((d / "run.json").read_text())
    first = run["tap_first_frame"]
    env = envelope(x, sr)

    # Stretches the recording lost (a tap chunk that never arrived): notes
    # there are skipped and counted, never judged.
    holes = [(a - run["tap_first_frame"], b - run["tap_first_frame"]) for a, b in run.get("tap_holes", [])]
    sent = [s for s in run["sent"] if (s["d"][0] & 0xF0) == 0x90 and len(s["d"]) > 2 and s["d"][2] > 0]
    seen = [m for m in run["midi_log"] if (m["d"][0] & 0xF0) == 0x90 and m["d"][2] > 0]
    results, report = {}, []

    # notes: each note-on the page saw must produce exactly one onset.
    heard, missing, lat, bad_pitch, extra = [], [], [], [], 0
    skipped = 0
    for k, m in enumerate(seen):
        i0 = int(round(m["ctxAt"] * sr)) - first
        if any(a - sr // 2 <= i0 <= b + sr // 2 for a, b in holes):
            skipped += 1
            continue
        nxt = int(round(seen[k + 1]["ctxAt"] * sr)) - first if k + 1 < len(seen) else len(x)
        lo, hi = max(0, i0 - sr // 200), min(len(x), i0 + sr * SEARCH_MS // 1000, nxt)
        if i0 < 0 or lo >= len(x):
            missing.append(m["d"])
            continue
        on = onset(x, env, lo, hi, sr)
        if on is None:
            missing.append(m["d"])
            continue
        lat.append((on - i0) * 1000 / sr)
        seg = x[on + sr // 50 : on + sr // 50 + sr // 16]      # 20 ms in, 62 ms long
        f = note_hz(seg, sr)
        ref = 440.0 * 2 ** ((m["d"][1] - 69) / 12)
        c = cents(f, ref)
        heard.append({"pitch": m["d"][1], "onset": on, "hz": f, "cents": c})
        if abs(c) > a.max_cents:
            bad_pitch.append((m["d"][1], round(f, 1), round(c) if math.isfinite(c) else "silent"))
    # Onsets nobody asked for: rising edges from silence, minus those matched.
    # A sound starting out of silence (50 ms quiet before it) that isn't one
    # of the heard notes' onsets.
    rising = np.flatnonzero((env[1:] > THRESHOLD) & (env[:-1] <= THRESHOLD))
    quiet = np.array([not np.any(env[max(0, r - sr // 20) : r] > THRESHOLD) for r in rising], dtype=bool)
    starts = rising[quiet] if len(rising) else rising
    onsets = np.array(sorted(h["onset"] for h in heard))
    extra = 0
    for r in starts:
        if any(a - sr // 2 <= r <= b + sr // 2 for a, b in holes):
            continue
        k = np.searchsorted(onsets, r)
        near = [abs(int(onsets[j]) - int(r)) for j in (k - 1, k) if 0 <= j < len(onsets)]
        if not near or min(near) > sr // 200:
            extra += 1
    lost_before_page = len(sent) - len(seen)
    ok = not missing and not extra and lost_before_page == 0
    results["notes"] = ok
    report.append("notes    %s  sent %d, page saw %d, heard %d, missing %d, extra %d%s" % (
        "PASS" if ok else "FAIL", len(sent), len(seen), len(heard), len(missing), extra,
        ", %d skipped in %d recording hole(s)" % (skipped, len(holes)) if holes else ""))

    ok = not bad_pitch and bool(heard)
    results["pitch"] = ok
    worst = max((abs(h["cents"]) for h in heard), default=float("nan"))
    report.append("pitch    %s  %d notes, worst %.0f cents%s" % (
        "PASS" if ok else "FAIL", len(heard), worst, (", off: %s" % bad_pitch[:5]) if bad_pitch else ""))

    if lat:
        la = np.array(lat)
        p50, p99, mx = np.percentile(la, 50), np.percentile(la, 99), la.max()
    else:
        p50 = p99 = mx = float("nan")
    ok = bool(lat) and p99 <= a.max_p99_ms
    results["latency"] = ok
    report.append("latency  %s  key-to-sound median %.1f ms, p99 %.1f ms, max %.1f ms (limit p99 %.0f)" % (
        "PASS" if ok else "FAIL", p50, p99, mx, a.max_p99_ms))
    info = run["info"]
    report.append("         + browser output latency %.1f ms (base %.1f ms), after the tap" % (
        1000 * (info.get("outputLatency") or 0), 1000 * (info.get("baseLatency") or 0)))

    # gaps: exact-zero runs while a note sounds (onset to 120 ms after).
    zero = x == 0.0
    gaps = 0
    for h in heard:
        if any(a - sr // 2 <= h["onset"] <= b for a, b in holes):
            continue
        z = zero[h["onset"] : h["onset"] + sr * 120 // 1000]
        if len(z):
            edges = np.diff(np.concatenate([[0], z.astype(np.int8), [0]]))
            lengths = np.flatnonzero(edges == -1) - np.flatnonzero(edges == 1)
            gaps += int((lengths >= GAP_SAMPLES).sum())
    snaps = [s for s in run["snapshots"] if s.get("worklet")]
    underruns = snaps[-1]["worklet"]["underruns"] if snaps else None
    ok = gaps == 0 and underruns == 0
    results["gaps"] = ok
    report.append("gaps     %s  %d silent runs inside notes; worklet underruns %s" % (
        "PASS" if ok else "FAIL", gaps, underruns))

    lag = np.array(run.get("lag_ms") or [0])
    report.append("page     event-loop lag p50 %.1f ms, p99 %.1f ms, max %.1f ms; LVGL frame p50 %.1f ms" % (
        np.percentile(lag, 50), np.percentile(lag, 99), lag.max(),
        np.percentile(run["page_frame_ms"], 50) if run.get("page_frame_ms") else float("nan")))
    lp = run.get("lv_pass_ms") or []
    if lp:
        report.append("lvgl     pass p50 %.1f ms, p99 %.1f ms, max %.1f ms (%d passes kept)" % (
            np.percentile(lp, 50), np.percentile(lp, 99), max(lp), len(lp)))
    print("\n".join(report))
    summary = {"results": results, "latency_ms": {"p50": p50, "p99": p99, "max": mx}, "gaps": gaps,
               "underruns": underruns, "notes": {"sent": len(sent), "heard": len(heard), "missing": len(missing),
                                                 "extra": extra}, "worst_cents": worst}
    (d / "check.json").write_text(json.dumps(summary, indent=2, default=float))
    sys.exit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
