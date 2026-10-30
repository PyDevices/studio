"""The desktop mixer: the AudioWorklet's job, in Python, on WASAPI.

It asks each engine for blocks, sums them through the shared master
(``mastercore.MasterMix``), applies the master gain and a limiter, and writes
the result to WASAPI through ``audiodev.win_audio``. The per-sample work is
all in C (``_audiodsp.mix_s16``/``multiply_s16``, ``audiodynamics``'s
limiter); Python only decides when.

The queue is WASAPI's own buffer: what's written and not yet played (its
padding). The mixer keeps it at a target and adapts the target with the rules
of pyscript-template#12's worklet (workers/audio_worklet.js): up 512 frames on
a stall, at most every quarter second, then a hold of 2 s (10 s if stalls keep
coming); down by what was spare, in one step, when the low point over 2 s
leaves 512 frames or more above the safety margin; down a block at a time
otherwise, to ``min_frames``. A stall is what the worklet counts as one: the
output running dry, or any engine's stream running short.

Context frames: frame k of the stream plays at context frame k, and the
context frame playing now is frames written minus padding. Engines learn
where their frames land (``offset``) and the clock (for MIDI timestamps)
through their rings.

The tap records everything written, and the timing of each write, so a
checker can say when each frame left WASAPI's buffer.
"""

import array
import json
import os
import time

import audiodynamics
from audiodev import AudioFormat
from audiodev import win_audio
from engine import BLOCK, BLOCK_BYTES
from mastercore import MasterMix
from ring import ASKED, CFRAME, CNS, OFFSET, PAUSE, UNDER

SR = 48000
GAIN_RESTORE = True        # the master sums 6 dB down; the output puts it back (as setGain(2.0) does)


def ns():
    return time.perf_counter_ns()


def timer_resolution_1ms():
    """Windows waits and sleeps tick at 15.6 ms unless a process asks for 1 ms."""
    try:
        import ctypes

        ctypes.windll.winmm.timeBeginPeriod(1)
    except Exception:  # noqa: BLE001
        pass


def stream_latency(out):
    """WASAPI's own figures: the stream latency and the device periods, in ms.

    Diagnostics only (the run reports them): win_audio has no public call for
    them, so this asks its client directly and gives up quietly without one."""
    import ctypes

    import uwin32 as win

    client = getattr(out, "_client", None)
    if not client:
        return {}
    lat = ctypes.c_int64()
    dflt, mn = ctypes.c_int64(), ctypes.c_int64()
    out = {}
    try:
        win._vcall(client, 5, ctypes.c_long, (ctypes.POINTER(ctypes.c_int64),), ctypes.byref(lat))
        out["stream_latency_ms"] = lat.value / 10000.0
    except Exception as e:  # noqa: BLE001
        out["stream_latency_err"] = str(e)
    try:
        win._vcall(client, 9, ctypes.c_long, (ctypes.POINTER(ctypes.c_int64), ctypes.POINTER(ctypes.c_int64)),
                   ctypes.byref(dflt), ctypes.byref(mn))
        out["default_period_ms"] = dflt.value / 10000.0
        out["min_period_ms"] = mn.value / 10000.0
    except Exception as e:  # noqa: BLE001
        out["period_err"] = str(e)
    return out


class Lane:
    """One engine's ring, as the worklet sees a stream."""

    def __init__(self, index, ring, io):
        self.index = index
        self.ring = ring
        self.io = io
        self.requested = 0
        self.seen_w = 0
        self.played = 0
        self.started = False
        self.paused = False
        self.pause_gen = 0
        self.underruns = 0
        self.offset = None

    def update(self):
        g = self.ring.h[PAUSE]
        if g != self.pause_gen:
            self.pause_gen = g
            self.paused = bool(g & 1)
            if not self.paused:
                # The stream starts afresh. What it had queued is never played,
                # but the engine rendered it: count it as played, or every
                # restart would leave the engine that much further ahead of
                # where its offset says its blocks land (a transport start a
                # few restarts later misses its first notes).
                self.played += self.ring.queued()
                self.ring.drop()
                self.requested = 0
                self.started = False
        w = self.ring.h[0]
        if w != self.seen_w:
            self.requested = max(0, self.requested - (w - self.seen_w))
            self.seen_w = w

    def top_up(self, frames, asked):
        if self.paused:
            return
        short = frames - self.ring.queued() - self.requested
        if short > 0:
            blocks = (short + BLOCK - 1) // BLOCK
            self.requested += blocks * BLOCK
            self.io.need(self.index, blocks, asked)

    def ready(self):
        return self.paused or self.ring.queued() >= BLOCK or not self.started

    def take(self, master_frame, delay):
        if self.paused:
            return None
        if self.ring.queued() >= BLOCK:
            block = self.ring.read(BLOCK)
            self.played += BLOCK
            self.started = True
        else:
            block = None
            if self.started:
                self.underruns += 1
                self.ring.h[UNDER] = self.underruns
        if self.started:
            offset = delay + master_frame + BLOCK - self.played
            if offset != self.offset:
                self.offset = offset
                self.ring.h[OFFSET] = offset
        return block


class Adapt:
    """The worklet's adaptive queue target (pyscript-template#12), in frames."""

    def __init__(self, start, lo, hi, safe):
        self.target, self.lo, self.hi, self.safe = start, lo, hi, safe
        self.window_low = self.fast_low = 1 << 30
        self.window_start = self.fast_start = 0
        self.hold_until = 0
        self.raised_at = -(1 << 40)
        self.last_short = -(1 << 40)
        self.hold = 2 * SR
        self.log = []          # (context second, what, target): the last changes, for the stats

    def _note(self, now, what):
        self.log.append((round(now / SR, 2), what, self.target))
        del self.log[:-12]

    def see(self, queued):
        if queued < self.window_low:
            self.window_low = queued
        if queued < self.fast_low:
            self.fast_low = queued

    def step(self, now, underran):
        if underran:
            quiet = now - self.last_short
            if quiet > SR:
                self.hold = (10 if quiet < 10 * SR else 2) * SR
            self.last_short = now
            if now - self.raised_at >= SR // 4:
                self.target = min(self.hi, self.target + 512)
                self.raised_at = now
            self.hold_until = now + self.hold
            self._note(now, "stall, hold %.0f s" % (self.hold / SR))
            self.window_start = self.fast_start = now
            self.window_low = self.fast_low = 1 << 30
            return
        if now - self.fast_start >= 2 * SR:
            # The worklet rounds the spare down to blocks; here the low point is
            # read just after WASAPI takes a whole 10 ms period, so it carries
            # that much more scheduling jitter: rounded to the nearest block, a
            # queue raised by one stall comes back to its floor in one step.
            spare = (self.fast_low - self.safe + BLOCK // 2) // BLOCK * BLOCK
            if now >= self.hold_until and spare >= 2 * BLOCK and self.target > self.lo:
                self.target = max(self.lo, self.target - spare)
                self._note(now, "spare %d" % spare)
                self.window_start = now
                self.window_low = 1 << 30
            self.fast_start = now
            self.fast_low = 1 << 30
        if now - self.window_start >= 2 * SR:
            if now >= self.hold_until and self.window_low > self.safe and self.target > self.lo:
                self.target = max(self.lo, self.target - BLOCK)
                self._note(now, "step")
            self.window_start = now
            self.window_low = 1 << 30


class Tap:
    """Everything written, and when: a raw int16 file of the mix, one of the
    tapped lane (as it entered the mix), and per write (stream frame, write
    time in ns, padding before the write).

    The files are opened, written and closed on a thread of their own: a file
    created on Windows can take tens of milliseconds (a virus scanner looks
    at it first), and the mixer can't wait that long. ``done`` is set once
    everything is on disk."""

    def __init__(self, folder, lane):
        import queue
        import threading

        self.folder = folder
        self.lane = lane
        self.times = array.array("q")
        self.first_frame = None
        self.q = queue.SimpleQueue()
        self.done = threading.Event()
        threading.Thread(target=self._writer, name="tap", daemon=True).start()

    def write(self, frame, mixed, lane_block, write_ns, pad):
        if self.first_frame is None:
            self.first_frame = frame
        self.q.put((mixed, lane_block))
        self.times.extend((frame, write_ns, pad))

    def close(self, extra, series=None):
        """``series``: name -> array of floats, written as ``name.f32`` (a
        JSON list of 20,000 numbers would hold this process's GIL long enough
        to starve the mixer)."""
        extra = dict(extra, first_frame=self.first_frame, sample_rate=SR, lane=self.lane)
        self.series = series or {}
        self.q.put(extra)

    def _writer(self):
        os.makedirs(self.folder, exist_ok=True)
        mix = open(os.path.join(self.folder, "mix.raw"), "wb", buffering=1 << 20)
        lanef = open(os.path.join(self.folder, "lane.raw"), "wb", buffering=1 << 20)
        while True:
            item = self.q.get()
            if isinstance(item, dict):
                break
            mixed, lane_block = item
            mix.write(mixed)
            lanef.write(lane_block if lane_block is not None else bytes(BLOCK_BYTES))
        mix.close()
        lanef.close()
        with open(os.path.join(self.folder, "times.bin"), "wb") as f:
            self.times.tofile(f)
        for name, arr in self.series.items():
            with open(os.path.join(self.folder, name + ".f32"), "wb") as f:
                arr.tofile(f)
        with open(os.path.join(self.folder, "tap.json"), "w") as f:
            json.dump(item, f, indent=1)
        self.done.set()


def run(rings, io, opts):
    timer_resolution_1ms()
    mm = MasterMix(SR)
    out = win_audio.pcm_out(AudioFormat(SR, 2, 16), latency="low", queue_ms=opts.get("buffer_ms", 100),
                            prebuffer_ms=0)
    out.open()
    wasapi = stream_latency(out)
    wasapi["buffer_frames"] = out.buffer_frames
    period = int(round(wasapi.get("default_period_ms", 10.0) * SR / 1000))
    lanes = [Lane(i, r, io) for i, r in enumerate(rings)]
    adapt = Adapt(opts.get("start_frames", 1536), opts.get("min_frames", 1024), opts.get("max_frames", 4096),
                  opts.get("safe_frames", 128))
    ahead = opts.get("ahead_frames", 0)      # frames each engine renders ahead of the mixer's need
    fixed = opts.get("fixed_frames")
    if fixed:
        adapt.target = adapt.lo = adapt.hi = fixed
    limiter = audiodynamics.Dynamics(audiodynamics.DYN_LIMIT, sample_rate=SR, threshold_db=-6.26,
                                     attack_ms=0.1, release_ms=50.0)
    lim = limiter._state
    import _audiodsp

    mix2 = _audiodsp.mix_s16
    written = 0                  # stream frames written
    started = False
    under_dev = 0                # device underruns: padding found empty while playing
    in_under = False
    lanes_seen = 0               # engine underruns the queue has answered
    late = 0                     # blocks mixed without a slow engine
    tap = None
    closing = []                 # taps still writing their files: (tap, folder)
    stats = {"wake_late_ms": array.array("f"), "refill_ms": array.array("f"), "low_ms": array.array("f"),
             "under_s": array.array("f")}
    last_pad = 0
    tick_ns = None               # when the device last took a period
    pull_ns = None               # a pull seen, not yet refilled
    expect_wake = None
    plant = opts.get("plant")
    plant_next = ns() + 1_000_000_000
    mixed_blocks = 0

    low = [1 << 30]              # the queue's low point since the last report

    def report():
        lo, low[0] = low[0], 1 << 30
        return {"t": "mixer", "underruns": under_dev, "lane_underruns": [ln.underruns for ln in lanes],
                "queued": [ln.ring.queued() for ln in lanes],
                "late": late, "target": adapt.target, "floor": adapt.lo, "low": None if lo == 1 << 30 else lo,
                "adapt": list(adapt.log),
                "frame": written - last_pad, "written": written, "wasapi": wasapi,
                "load": mm.load(), "meter": mm.report()["meter"]}

    while True:
        for m in io.control():
            t = m.get("t")
            if t == "op":
                mm.apply(m["op"])
            elif t == "ops":
                mm.apply_many(m["ops"])
            elif t == "stats":
                io.reply(report())
            elif t == "tap":
                if m.get("on"):
                    tap = Tap(m["folder"], m.get("lane", 0))
                    for k in stats:
                        stats[k] = array.array("f")
                    io.reply({"t": "tap_open", "folder": m["folder"]})
                elif tap is not None:
                    tap.close({"wasapi": wasapi, "report": report(), "target_end": adapt.target,
                               "under_s": list(stats["under_s"])},
                              {k: array.array("f", stats[k]) for k in ("wake_late_ms", "refill_ms", "low_ms")})
                    closing.append((tap, m.get("folder")))
                    tap = None
            elif t == "quit":
                if tap is not None:
                    tap.close({"wasapi": wasapi, "report": report()})
                    tap.done.wait(10)
                out.close()
                return
        while closing and closing[0][0].done.is_set():
            io.reply({"t": "tap_closed", "folder": closing.pop(0)[1]})
        now = ns()
        if expect_wake is not None:
            stats["wake_late_ms"].append((now - expect_wake) / 1e6)
        pad = out.device_frames()
        ctx_now = written - pad
        for ln in lanes:
            h = ln.ring.h
            h[CFRAME] = ctx_now
            h[CNS] = now
            h[ASKED] = ctx_now
            ln.update()
        if started:
            if pad < last_pad:
                tick_ns = now
                if pull_ns is None:
                    pull_ns = now
                stats["low_ms"].append(pad * 1000.0 / SR)
            newly = pad == 0 and not in_under
            if newly:
                under_dev += 1
                stats["under_s"].append(ctx_now / SR)
            in_under = pad == 0
            lanes_short = sum(ln.underruns for ln in lanes)
            adapt.see(pad)
            adapt.step(ctx_now, newly or lanes_short > lanes_seen)
            lanes_seen = lanes_short
            if pad < low[0]:
                low[0] = pad
        target = adapt.target
        want = (target - pad) // BLOCK
        waiting = False
        if want <= 0 and ahead:
            for ln in lanes:
                ln.top_up(ahead, ctx_now)
        if want > 0:
            for ln in lanes:
                ln.top_up(want * BLOCK + ahead, ctx_now)
            while want > 0:
                if not all(ln.ready() for ln in lanes):
                    # Wait for a slow engine only as long as the device can play
                    # on what it holds: until a millisecond before the tick that
                    # would find too little.
                    nxt = (tick_ns or now) + int((1 + pad // max(1, period)) * period * 1e9 / SR)
                    if not started or now < nxt - 1_000_000:
                        waiting = True
                        break
                    late += 1
                blocks = [ln.take(mm.frame, mm.delay) for ln in lanes]
                block = mm.mix(blocks)
                block = lim.process(block)
                if GAIN_RESTORE:
                    block = mix2(block, block)
                if plant == "drop" and now >= plant_next:
                    block = bytes(BLOCK_BYTES)            # one silent block a second
                    plant_next = now + 1_000_000_000
                wns = ns()
                got = out.write_now(block, start=False)
                if got < BLOCK_BYTES:
                    print("mixer: WASAPI took %d of %d bytes" % (got, BLOCK_BYTES))
                if tap is not None:
                    lb = blocks[tap.lane] if tap.lane < len(blocks) else None
                    tap.write(written, block, lb, wns, pad)
                written += BLOCK
                pad += BLOCK
                want -= 1
                mixed_blocks += 1
            if want <= 0 and pull_ns is not None:
                stats["refill_ms"].append((ns() - pull_ns) / 1e6)
                pull_ns = None
        if not started and pad >= target:
            started = out.start()
        last_pad = pad
        timeout = 0.002 if waiting else 0.001
        expect_wake = ns() + int(timeout * 1e9)
        io.wait(timeout)
