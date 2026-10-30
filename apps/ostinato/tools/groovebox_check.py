"""Phase 4 (the groovebox): its gate, through the real UI.

    .venv/bin/python tools/groovebox_check.py --cdp http://127.0.0.1:9222
    .venv/bin/python tools/groovebox_check.py --desktop processes|threads|threads-ft

Each part clicks the app's own controls (positions from the UI's geometry)
and listens to the tap:

  grid    drum hits clicked into the step grid play back on exactly those steps
  roll    notes clicked into the piano roll, one dragged to a new pitch, play
          back as the edited melody
  record  a loop played loosely from the fake keyboard records quantised
          to the grid and plays back note for note
  busy    a dense piano roll edited while it plays: frame time, no dropouts

Song order is heard in tools/files_check.py's live take.
"""

import base64
import json
import math
import pathlib
import subprocess
import sys
import time

import numpy as np
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from tap_check import pitch_hz  # noqa: E402

SR = 48000
_TAKES = 0
TEMPO = 110.0
STEP_S = 60.0 / TEMPO / 4

TAKE = """() => {
  const chunks = window.__ostinato.audio.tapChunks.splice(0);
  return chunks.map((c) => {
    const u8 = new Uint8Array(c.pcm.buffer);
    let s = "";
    for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
    return [c.start, c.channels, btoa(s)];
  });
}"""


class App:
    def __init__(self, page):
        self.page = page
        self.dpr = page.evaluate("devicePixelRatio")

    def geom(self):
        self.page.evaluate("window.__geom = null; window.__ostinato.ui.send({kind: 'geom'})")
        self.page.wait_for_function("window.__geom !== null", timeout=5000)
        return self.page.evaluate("window.__geom")

    def project(self):
        self.page.evaluate("window.__project = null; window.__ostinato.ui.send({kind: 'project'})")
        self.page.wait_for_function("window.__project !== null", timeout=5000)
        return self.page.evaluate("window.__project")

    def click_rect(self, r):
        self.page.mouse.click((r[0] + r[2] / 2) / self.dpr, (r[1] + r[3] / 2) / self.dpr)
        time.sleep(0.25)

    # The pointer, in canvas pixels (the UI's geometry): the desktop app's
    # session (tools/desktopkit.py) has the same calls.
    def click(self, x, y):
        self.page.mouse.click(x / self.dpr, y / self.dpr)

    def move(self, x, y):
        self.page.mouse.move(x / self.dpr, y / self.dpr)

    def down(self):
        self.page.mouse.down()

    def up(self):
        self.page.mouse.up()

    def play_midi(self, events):
        """[[ms from now, [status, d1, d2]], ...] from the fake keyboard."""
        self.page.evaluate("ev => window.__fakeMidi.play(ev)", events)

    def uistats(self):
        self.page.evaluate("window.__uistats = null; window.__ostinato.ui.send({kind: 'uistats'})")
        self.page.wait_for_function("window.__uistats !== null", timeout=5000)
        return self.page.evaluate("window.__uistats")

    def masterstats(self):
        """Each track's and the master's meter, as the UI reads them."""
        self.page.evaluate("window.__masterstats = null; window.__ostinato.ui.send({kind: 'masterstats'})")
        self.page.wait_for_function("window.__masterstats !== null", timeout=5000)
        return self.page.evaluate("window.__masterstats")

    def fresh(self):
        """A new, empty project, as File > New gives you."""
        sys.path.insert(0, str(ROOT / "app" / "shared"))
        import model
        p = model.new_project()
        self.page.evaluate("j => window.__ostinato.ui.send({kind: 'loaded', json: j})", json.dumps(p))
        time.sleep(1.5)

    def select(self, i):
        self.page.evaluate("i => window.__ostinato.ui.send({kind: 'select', index: i})", i)
        time.sleep(0.6)

    def record(self, seconds, before=None):
        """Turn the tap on, wait until it's really recording, do ``before``,
        and collect until ``seconds`` of audio have arrived."""
        self.page.evaluate("window.__ostinato.audio.tapChunks.length = 0; window.__ostinato.audio.tap(true)")
        self.page.wait_for_function("window.__ostinato.audio.tapChunks.length > 0", timeout=10_000)
        self.page.evaluate("window.__ostinato.audio.tapChunks.length = 0")
        if before:
            before()
        need = int(seconds * 2) + 1                  # chunks are half a second
        self.page.wait_for_function("n => window.__ostinato.audio.tapChunks.length >= n", arg=need,
                                    timeout=int(seconds * 1000) + 20_000)
        self.page.evaluate("window.__ostinato.audio.tap(false)")
        chunks = sorted(self.page.evaluate(TAKE))
        self.last_chunks = [c[0] for c in chunks]
        if not chunks:
            return np.zeros(0)
        first = chunks[0][0]
        frames = chunks[-1][0] - first + len(base64.b64decode(chunks[-1][2])) // 4 // chunks[-1][1]
        out = np.zeros(frames, dtype=np.float32)
        for s, ch, b64 in chunks:
            d = np.frombuffer(base64.b64decode(b64), dtype=np.float32).reshape(-1, ch).mean(axis=1)
            out[s - first : s - first + len(d)] = d
        global _TAKES
        _TAKES += 1
        import soundfile
        soundfile.write(str(ROOT / "runs" / ("groovebox_take%d.wav" % _TAKES)), out, SR)
        return out


def onsets(x, thr=0.02, gap_s=0.06):
    w = SR // 1000
    env = np.sqrt(np.convolve(x * x, np.ones(w) / w, "same"))
    above = env > thr
    rises = np.flatnonzero(above[1:] & ~above[:-1]) + 1
    out = []
    for r in rises:
        if not out or r - out[-1] > gap_s * SR:
            out.append(int(r))
    return out


def pitches(x, ons):
    out = []
    for o in ons:
        f = pitch_hz(x[o + SR // 40 : o + SR // 40 + 4096], SR)
        out.append(round(69 + 12 * math.log2(f / 440)) if f > 0 else None)
    return out


def step_strength(x, first, nsteps):
    """Onset strength at each sixteenth after ``first``: how much the level
    rises in the 25 ms after the step over the 25 ms before it, as a fraction
    of the biggest rise."""
    w = int(0.025 * SR)
    rises = []
    for k in range(nsteps):
        i = first + int(round(k * STEP_S * SR))
        pre = x[max(0, i - w):i]
        post = x[i:i + w]
        e0 = float(np.sqrt(np.mean(pre * pre))) if len(pre) else 0.0
        e1 = float(np.sqrt(np.mean(post * post))) if len(post) else 0.0
        rises.append(max(0.0, e1 - e0))
    top = max(rises) or 1.0
    return [r / top for r in rises]


def pitches_at(x, start, spacing_s, n, into_s=0.025):
    """Pitch of the note starting at each of ``n`` known positions, read
    ``into_s`` after its start (later for a patch with glide)."""
    out = []
    for k in range(n):
        i = start + int(round(k * spacing_s * SR)) + int(into_s * SR)
        seg = x[i : i + 4096]
        if len(seg) < 4096 or np.max(np.abs(seg)) < 0.005:
            out.append(None)
            continue
        f = pitch_hz(seg, SR)
        out.append(round(69 + 12 * math.log2(f / 440)) if f > 0 else None)
    return out


def steps_of(ons):
    """Onset times as sixteenth steps from the first onset."""
    return [round((o - ons[0]) / SR / STEP_S, 2) for o in ons]


def run(sess):
    """Every part, in session ``sess`` (a checkkit session, the browser's or
    the desktop app's); returns the results."""
    results = {}

    def boot():
        # A clean app for each part: a new project, nothing selected or armed.
        sess.reset()
        return sess.app

    app = boot()

    # -- grid: kick on 0 4 8 12, snare on 4 12, closed hat on the even steps
    app.select(0)
    g = app.geom()
    grid = g["grid"]
    rows, steps = grid["rows"], grid["steps"]
    gx, gy, gw, gh = grid["rect"]
    cw, ch = gw / steps, gh / len(rows)
    want = {36: [0, 4, 8, 12], 38: [4, 12], 42: [0, 2, 4, 6, 8, 10, 12, 14]}
    for pitch, ss in want.items():
        r = rows.index(pitch)
        for s in ss:
            app.click(gx + (s + 0.5) * cw, gy + (r + 0.5) * ch)
            time.sleep(0.12)
    x = app.record(4.6, before=lambda: app.click_rect(g["play"]))
    app.click_rect(g["stop"])
    ons = onsets(x)
    # Hits must start on the even steps and nowhere else: strong onsets
    # at 0, 2, ... 14 and weak ones on the odd steps, over two bars.
    strength = step_strength(x, ons[0], 32) if ons else []
    even = [strength[k] for k in range(0, 32, 2)]
    odd = [strength[k] for k in range(1, 32, 2)]
    ok = bool(strength) and min(even) > 0.06 and max(odd) < 0.03
    results["grid"] = {"pass": ok, "weakest_hit": round(min(even), 2) if even else None,
                       "strongest_gap": round(max(odd), 2) if odd else None}

    # -- roll: bass notes on steps 0 4 8 12, then drag the third down a tone
    app = boot()
    app.select(1)
    g = app.geom()
    roll = g["roll"]
    rx, ry, rw, rh = roll["rect"]
    cw, chh = rw / roll["steps"], rh / roll["rows"]

    def cell(s, p):
        row = roll["low"] + roll["rows"] - 1 - p
        return rx + (s + 0.5) * cw, ry + (row + 0.5) * chh

    for s, p in ((0, 50), (4, 53), (8, 57), (12, 60)):     # the roll shows 48..71
        app.click(*cell(s, p))
        time.sleep(0.3)
    sx, sy = cell(8, 57)
    ex, ey = cell(8, 55)
    app.move(sx, sy)
    app.down()
    for k in range(1, 6):
        app.move(sx, sy + (ey - sy) * k / 5)
        time.sleep(0.05)
    app.up()
    time.sleep(0.5)
    roll_notes = sorted(app.project()["tracks"][1]["patterns"][0]["notes"])
    x = app.record(2.4, before=lambda: app.click_rect(g["play"]))
    app.click_rect(g["stop"])
    ons = onsets(x, gap_s=0.45)
    ps = pitches_at(x, ons[0], 4 * STEP_S, 4) if ons else []
    # Undo puts the dragged note back; redo moves it again.
    app.click_rect(g["undo"])
    time.sleep(0.4)
    after_undo = sorted(n[2] for n in app.project()["tracks"][1]["patterns"][0]["notes"])
    app.click_rect(g["redo"])
    time.sleep(0.4)
    after_redo = sorted(n[2] for n in app.project()["tracks"][1]["patterns"][0]["notes"])
    undo_ok = after_undo == [50, 53, 57, 60] and after_redo == [50, 53, 55, 60]
    results["roll"] = {"pass": ps == [50, 53, 55, 60] and undo_ok, "pitches": ps, "notes": roll_notes,
                       "undo": after_undo, "redo": after_redo,
                       "drag": [round(sy, 1), round(ey, 1)], "dpr": app.dpr}

    # -- record: arm, play, keyboard plays loosely, stop, play back
    app = boot()
    app.select(3)
    g = app.geom()
    app.click_rect(g["rec"])
    loose = [[0, 60], [4, 62], [8, 64], [12, 65]]
    jitter = [18, -15, 22, -9]              # ms off the grid, as a player would be
    app.click_rect(g["play"])
    time.sleep(0.15)                          # transport starts 80 ms after the click
    events = []
    for (s, p), j in zip(loose, jitter):
        t = s * STEP_S * 1000 + j + 80 + 16 * STEP_S * 1000     # after the bar of count-in
        events += [[t, [0x90, p, 100]], [t + 150, [0x80, p, 0]]]
    app.play_midi(events)
    time.sleep(2.6 + 16 * STEP_S)
    app.click_rect(g["stop"])
    app.click_rect(g["rec"])                  # disarm
    time.sleep(0.5)
    recorded = sorted(app.project()["tracks"][3]["patterns"][0]["notes"])
    x = app.record(2.4, before=lambda: app.click_rect(g["play"]))
    app.click_rect(g["stop"])
    ons = onsets(x, gap_s=0.45)
    ps = pitches_at(x, ons[0], 4 * STEP_S, 4, into_s=0.08) if ons else []   # the lead glides
    # Quantised: each recorded note starts on its step, to within 8 ms (the
    # onset detection's own spread; the notes were played up to 22 ms off).
    st = [round((o - ons[0]) / SR / STEP_S, 2) for o in ons[:4]] if ons else []
    on_grid = len(st) == 4 and all(abs(a - b) * STEP_S * 1000 < 8.0 for a, b in zip(st, [0, 4, 8, 12]))
    # The pattern holds exactly what was played, on the grid.
    stored = [(n[0], n[2]) for n in recorded]
    # Where the take starts depends on when the keys came after Play;
    # what matters is that it's on the grid, a beat apart, in order.
    base = stored[0][0] if stored else 0
    rel = [(t - base, p) for t, p in stored]
    on_steps = all(t % 24 == 0 for t, _ in stored)
    # Quantisation is proven by the stored ticks (exact sixteenths); the
    # onset spacing is reported, not judged: a note's release can cross
    # the detector's threshold just before the next note.
    results["record"] = {"pass": rel == [(0, 60), (96, 62), (192, 64), (288, 65)] and on_steps and ps == [60, 62, 64, 65],
                         "onsets_on_grid": on_grid,
                         "stored": stored, "pitches": ps, "steps": st}

    # -- busy: a dense piano roll edited while it plays; frame time and dropouts
    app = boot()
    sys.path.insert(0, str(ROOT / "app" / "shared"))
    import model as _m
    dense = _m.new_project()
    dense["tracks"][3]["patterns"][0] = {"bars": 4, "notes": [[k * 24, 24, 48 + (k * 5) % 24, 100] for k in range(64)]}
    dense["tracks"][0]["patterns"][0] = {"bars": 1, "notes": [[k * 24, 24, 42, 90] for k in range(16)]}
    sess.load(dense)
    app.select(3)
    g = app.geom()
    roll = g["roll"]
    rx, ry, rw, rh = roll["rect"]
    st0 = sess.stats()
    before = st0["underruns"]
    app.click_rect(g["play"])
    app.uistats()                       # starts the frame-time sample afresh
    t_end = time.time() + 12
    k = 0
    while time.time() < t_end:
        # click empty cells and drag notes around, about four edits a second
        cx = rx + (k * 37 % roll["steps"] + 0.5) * rw / roll["steps"]
        cy = ry + (k * 7 % roll["rows"] + 0.5) * rh / roll["rows"]
        app.move(cx, cy)
        app.down()
        app.move(cx + 20 * app.dpr, cy - 15 * app.dpr)
        app.up()
        time.sleep(0.25)
        k += 1
    ms = app.uistats()["pass_ms"]
    st1 = sess.stats()
    after = st1["underruns"]
    app.click_rect(g["stop"])
    arr = np.array(ms) if ms else np.zeros(1)
    results["busy"] = {"pass": after == before, "edits": k, "lvgl_pass_ms_p50": float(np.percentile(arr, 50)),
                       "p99": float(np.percentile(arr, 99)), "max": float(arr.max()), "underruns": after - before}
    if "lane_underruns" in st1:          # the desktop mixer: which stream ran short
        results["busy"]["lanes"] = [b - a for a, b in zip(st0["lane_underruns"], st1["lane_underruns"])]

    return results


def main():
    import argparse
    from checkkit import add_session_args, report, session
    ap = argparse.ArgumentParser()
    add_session_args(ap)
    a = ap.parse_args()
    with session(a) as s:
        results = run(s)
        if s.log:
            print("page errors:", s.log[:6])
    ok = report("groovebox", results)
    (ROOT / "runs").mkdir(exist_ok=True)
    (ROOT / "runs" / "groovebox_check.json").write_text(json.dumps(results, indent=1))
    sys.exit(0 if ok and results else 1)


if __name__ == "__main__":
    main()
