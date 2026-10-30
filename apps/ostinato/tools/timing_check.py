"""Playback timing of the demo song, Bass soloed, while its knobs, patch and
instrument change.

    .venv/bin/python tools/timing_check.py --cdp http://127.0.0.1:9222 [--phase-s 10]
    .venv/bin/python tools/timing_check.py --desktop processes|threads|threads-ft [--phase-s 10]

The demo song is cut to the scenes the run covers, starting where Bass
comes in, with Bass soloed and selected. That cut is exported first: the
export renders offline, so it is when every note should sound. Then the same
cut plays live in song mode with the tap on, through these phases, each
--phase-s long:

  still     nothing changes
  knobs     the twelve DMK-25 knobs swept, about 60 changes a second
  patches   minimoog, its three patches in turn every 1.5 s
  instr     a different instrument every 3 s
  still2    nothing changes again

Timing: the live tap's note onsets are lined up against the export's, one
second at a time; the shift that lines them up is how late (or early) the
playback is in that second, relative to the first phase. Timbre doesn't
matter, only when notes start.

Per phase: the shift's median and worst, seconds whose onsets didn't match
(no confident shift), silence where the export sounds (dropouts, or a new
instrument being built), the worklet's underruns, and its queue target: it
grows after an underrun, and every frame of it is latency added to what the
keyboard plays. Writes runs/timing_live.wav, runs/timing_ref.wav and
runs/timing.json.

It passes when the still phases have no underruns, and in the others the
queue is back at its floor within 5 s of the last underrun (the adaptive
queue's rules: a stall raises it, a hold of 2 s, then down by what was spare).
The desktop app's output is the mixer's (desktop/mixer.py), with its own copy
of those rules; its queue is WASAPI's.
"""
import argparse, json, pathlib, random, sys, time

import numpy as np
import soundfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools")); sys.path.insert(0, str(ROOT.parent.parent / "lib"))
from songlink import model
from checkkit import assemble  # noqa: F401  (tools that read old takes import it from here)
from files_check import export

SR = 48000
HOP = 240                      # 5 ms frames for the onset curve
INSTRUMENTS = ["tb303", "juno106", "minimoog", "dx7", "sh101", "prophet5", "jupiter8", "virus"]
PHASES = ["still", "knobs", "patches", "instr", "still2"]
STILL = ("still", "still2")
RECOVER_S = 5.0                # back at the floor within this long of the last underrun
BROWSER_FLOOR = 1024           # index.html's minFrames (the desktop mixer reports its own)


def onset_curve(x):
    """Positive change of the log level in 5 ms frames: peaks where notes start."""
    n = len(x) // HOP
    rms = np.sqrt((x[: n * HOP].reshape(n, HOP) ** 2).mean(axis=1) + 1e-10)
    lv = np.log(rms + 1e-4)
    d = np.maximum(0.0, np.diff(lv, prepend=lv[0]))
    return d


def best_shift(a, b, at_a, at_b, length, search):
    """Shift (frames of HOP) of b against a near at_b, by correlation; and its confidence."""
    wa = a[at_a: at_a + length]
    if len(wa) < length or wa.std() < 1e-6:
        return None, 0.0
    best, best_c = None, -1.0
    scores = []
    for k in range(-search, search + 1):
        s0 = at_b + k
        if s0 < 0 or s0 + length > len(b):
            continue
        wb = b[s0: s0 + length]
        if wb.std() < 1e-6:
            continue
        c = float(np.corrcoef(wa, wb)[0, 1])
        scores.append(c)
        if c > best_c:
            best, best_c = k, c
    return best, best_c


def cut_demo(p, seconds):
    """The demo from the first scene Bass plays in, for ``seconds`` and a little more."""
    bass = next(t for t in p["tracks"] if t["name"] == "Bass")
    key = str(bass["id"])
    start = next(i for i, sc in enumerate(p["song"]) if sc["patterns"].get(key, -1) >= 0)
    bar_s = model.BEATS_PER_BAR * 60 / p["tempo"]
    song, total = [], 0.0
    for sc in p["song"][start:]:
        bars = p["tracks"][0]["patterns"][0].get("bars", 4)
        song.append(sc)
        total += 4 * bar_s * sc.get("repeat", 1)
        if total > seconds + 4:
            break
    p["song"] = song
    p["song_mode"] = True
    p["loop_scene"] = None
    for t in p["tracks"]:
        t["solo"] = t["id"] == bass["id"]
    return bass["id"], start


def run(s, phase_s=10.0):
    app = s.app
    s.reset(); s.send({"kind": "first-run"}); time.sleep(0.5); s.settle()
    p = app.project()
    tid, start = cut_demo(p, phase_s * len(PHASES) + 2)
    s.reset(p)                # closes the welcome guide the first run opened
    bi = next(i for i, t in enumerate(p["tracks"]) if t["id"] == tid)
    app.select(bi)
    print("demo from scene %d, %d scenes, Bass on %s; exporting the reference..." % (
        start + 1, len(p["song"]), p["tracks"][bi]["instrument"]), flush=True)
    ref, sr = export(s, app)
    ref = ref.mean(axis=1) if ref.ndim > 1 else ref
    assert sr == SR, sr
    (ROOT / "runs").mkdir(exist_ok=True)
    soundfile.write(str(ROOT / "runs" / "timing_ref.wav"), ref, SR)

    s.tap_on()
    stats, marks = [], []
    under0 = int(s.stats()["underruns"])       # the count is since the app started: the export before may have some
    t_start = time.monotonic()
    app.click_rect(app.geom()["play"])
    for phase in PHASES:
        marks.append((phase, s.now_frame()))
        end = time.monotonic() + phase_s
        n, last_stat, inst_i, patch_i = 0, 0.0, 0, 0
        next_change = time.monotonic()
        while time.monotonic() < end:
            t = time.monotonic()
            if phase == "knobs":
                s.send({"kind": "cc", "chan": 0, "cc": 30 + n % 12, "value": random.randrange(128)})
                n += 1
                time.sleep(1 / 60)
            elif phase == "patches" and t >= next_change:
                if patch_i == 0:
                    s.send({"kind": "instrument", "index": bi, "name": "minimoog"})
                else:
                    s.op({"op": "set", "path": model.track_path(tid, "patch"), "value": patch_i % 3})
                patch_i += 1
                next_change = t + 1.5
            elif phase == "instr" and t >= next_change:
                s.send({"kind": "instrument", "index": bi, "name": INSTRUMENTS[inst_i % len(INSTRUMENTS)]})
                inst_i += 1
                next_change = t + 3.0
            else:
                time.sleep(0.02)
            if t - last_stat > 0.5:
                st = s.stats()
                stats.append({"phase": phase, "t": round(t - t_start, 2), "underruns": st["underruns"],
                              "target": st["target"], "low": st.get("low"), "floor": st.get("floor")})
                s.tap_poll()
                last_stat = t
    marks.append(("end", s.now_frame()))
    app.click_rect(app.geom()["play"])
    time.sleep(0.4)
    live, first = s.tap_off()
    soundfile.write(str(ROOT / "runs" / "timing_live.wav"), live, SR)

    a, b = onset_curve(ref), onset_curve(live)
    win = int(1.0 * SR / HOP)
    # Where the song starts in the tap: line up the first phase's second second
    # against the export's, searching the first 3 s.
    m0 = int((marks[0][1] - first) / HOP)
    k0, c0 = None, -1
    for k in range(max(0, m0 - 40), m0 + int(3 * SR / HOP)):
        if k + 2 * win > len(b):
            break
        c = float(np.corrcoef(a[win: 3 * win], b[k + win: k + 3 * win])[0, 1])
        if c > c0:
            k0, c0 = k, c
    # Per second: the shift of the tap against the export, around where it should be.
    rows = []
    sec = 0
    phase_bounds = [(name, (m - first) / HOP) for name, m in marks]
    while (sec + 1) * win < len(a):
        at_b = k0 + sec * win
        if at_b + win > len(b):
            break
        k, c = best_shift(a, b, sec * win, at_b, win, int(0.4 * SR / HOP))
        phase = None
        for (name, lo), (_, hi) in zip(phase_bounds, phase_bounds[1:]):
            if lo <= at_b < hi:
                phase = name
        if phase is None:
            sec += 1
            continue
        # silence in the tap where the export sounds, this second (exact zeros, 1 ms runs)
        ra = ref[sec * win * HOP: (sec + 1) * win * HOP]
        lb = live[at_b * HOP: (at_b + win) * HOP]
        quiet = 0
        if len(ra) == len(lb) and np.abs(ra).max() > 0.01:
            blk = 48
            n = len(lb) // blk
            zb = np.abs(lb[: n * blk]).reshape(n, blk).max(axis=1) == 0
            sa = np.abs(ra[: n * blk]).reshape(n, blk).max(axis=1) > 0.001
            quiet = int((zb & sa).sum())
        rows.append({"second": sec, "phase": phase, "shift_ms": None if k is None else k * HOP * 1000 / SR,
                     "conf": round(c, 2), "silent_ms": quiet})
        sec += 1
    base = np.median([r["shift_ms"] for r in rows if r["phase"] == "still" and r["shift_ms"] is not None
                      and r["conf"] > 0.5] or [0])
    for r in rows:
        if r["shift_ms"] is not None:
            r["shift_ms"] -= base

    report = []
    prev_under = under0
    for ph in PHASES:
        rs = [r for r in rows if r["phase"] == ph]
        good = [r["shift_ms"] for r in rs if r["shift_ms"] is not None and r["conf"] > 0.5]
        st = [q for q in stats if q["phase"] == ph]
        under = (st[-1]["underruns"] - prev_under) if st else None
        if st:
            prev_under = st[-1]["underruns"]
        report.append({"phase": ph, "seconds": len(rs), "unmatched": len(rs) - len(good),
                       "shift_ms_median": round(float(np.median(good)), 1) if good else None,
                       "shift_ms_worst": round(float(max(good, key=abs)), 1) if good else None,
                       "silent_ms": sum(r["silent_ms"] for r in rs), "underruns": under,
                       "queue_max": max((q["target"] for q in st), default=None)})
    verdict = judge(report, stats, under0)
    (ROOT / "runs" / "timing.json").write_text(json.dumps({"start_scene": start + 1, "align_conf": c0,
                                                           "report": report, "verdict": [[bool(ok), line] for ok, line in verdict], "underruns_before": under0,
                                                           "adapt": s.stats().get("adapt"),
                                                           "seconds": rows,
                                                           "stats": stats}, indent=1))
    return report, c0, verdict


def judge(report, stats, under0=0):
    """The gate: no underruns in the still phases; elsewhere, the queue back at
    its floor within RECOVER_S of the last underrun. -> [(ok, line), ...]"""
    out = []
    floor = next((q["floor"] for q in stats if q.get("floor")), None) or BROWSER_FLOOR
    for r in report:
        ph = r["phase"]
        if ph in STILL:
            out.append((bool(r["underruns"] == 0), "%s: %s underruns (want none)" % (ph, r["underruns"])))
            continue
        st = [q for q in stats if q["phase"] == ph]
        if not r["underruns"]:
            out.append((True, "%s: no underruns" % ph))
            continue
        before = [q for q in stats if q["t"] < st[0]["t"]]
        prev = before[-1]["underruns"] if before else under0
        last_t = None
        for q in st:
            if q["underruns"] > prev:
                last_t = q["t"]
            prev = q["underruns"]
        back = next((q["t"] for q in stats if last_t is not None and q["t"] > last_t and q["target"] <= floor), None)
        ok = bool(back is not None and back - last_t <= RECOVER_S)
        out.append((ok, "%s: %d underruns, the last at %.1f s; the queue back at its floor (%d) %s"
                    % (ph, r["underruns"], last_t, floor,
                       "%.1f s later" % (back - last_t) if back is not None else "never")))
    return out


def main():
    from checkkit import add_session_args, session
    ap = argparse.ArgumentParser(); add_session_args(ap); ap.add_argument("--phase-s", type=float, default=10.0)
    ap.add_argument("--seed", type=int, default=1, help="knob values are random from this seed, so runs compare")
    a = ap.parse_args()
    random.seed(a.seed)
    with session(a) as s:
        report, c0, verdict = run(s, a.phase_s)
    print("song start found in the tap with correlation %.2f" % c0)
    print("%-8s %8s %10s %20s %10s %10s %7s" % ("phase", "seconds", "unmatched", "late ms med / worst",
                                               "silent ms", "underruns", "queue"))
    for r in report:
        print("%-8s %8d %10d %9s / %-9s %10d %10s %7s" % (
            r["phase"], r["seconds"], r["unmatched"], r["shift_ms_median"], r["shift_ms_worst"],
            r["silent_ms"], r["underruns"], r["queue_max"]))
    for ok, line in verdict:
        print("%s  %s" % ("ok  " if ok else "FAIL", line))
    sys.exit(0 if all(ok for ok, _ in verdict) else 1)


if __name__ == "__main__":
    main()
