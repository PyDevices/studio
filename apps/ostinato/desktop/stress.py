"""Free-threading stress test for _audiodsp and LVGL (Phase 0).

    python3.15t.exe -I -X gil=0 desktop/stress.py [--threads 16] [--rounds 3] [--blocks 400]

1. Each instrument renders a fixed phrase alone, twice (it must match itself:
   anything random in it would make the comparison meaningless).
2. Then many threads render at once, several copies of each instrument, and
   every thread's output is compared sample for sample with the solo render.
3. LVGL draws a busy screen in one thread (fixed ticks, so its frames are
   deterministic) while the instrument threads render; its framebuffer hash is
   compared with the same drawing done with nothing else running.

Prints the GIL state after the imports (it must stay off), every mismatch,
and the speed-up of the threaded round over one after another.
"""

import argparse
import hashlib
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[0:0] = [os.path.join(ROOT, "app", "engine"), os.path.join(ROOT, "..", "..", "lib")]
if "--synthio-tls" in sys.argv:
    # An experiment: synthio.py with its block clock per thread (see the packet).
    sys.path.insert(0, os.path.join(ROOT, "patched"))

import audioeffects  # noqa: E402
import audioinstruments  # noqa: E402

from engine import Puller  # noqa: E402

SR = 48000
INSTRUMENTS = ["tr808", "virus", "juno106", "dx7", "minimoog", "karplus", "rhodes", "jupiter8", "tb303", "solina"]
RACKS = {"juno106": ("Chorus", "Reverb"), "dx7": ("DigitalDelay",), "virus": ("Compressor",)}
CREATE_LOCK = threading.Lock() if os.environ.get("STRESS_CREATE_LOCK") else None
PLANT = os.environ.get("STRESS_PLANT")       # "detune": thread 3 plays one note a semitone sharp; "lvgl": one label differs


def phrase(name):
    drums = name in audioinstruments.DRUM_MACHINES
    notes = [36, 38, 42, 46] if drums else [48, 55, 60, 64, 67, 72]
    return [(i * 6, notes[i % len(notes)], 100 - (i % 5) * 7) for i in range(60)]


def render(name, blocks, inst=None, detune=False):
    """The phrase on ``name``, through its rack if it has one: int16 stereo bytes."""
    return b"".join(renderer(name, blocks, inst, detune))


def renderer(name, blocks, inst=None, detune=False):
    """``render`` one block at a time (a generator), so renders can interleave."""
    own = inst is None
    if own:
        if CREATE_LOCK is not None:
            with CREATE_LOCK:      # audioinstruments' factories share a module-level list (_support._PENDING)
                inst = audioinstruments.create(name, SR, channel_count=2)
        else:
            inst = audioinstruments.create(name, SR, channel_count=2)
    rack = None
    src = inst.output
    if name in RACKS and own:
        rack = audioeffects.create("Rack", inst.output, SR, chain=tuple((e, {}) for e in RACKS[name]))
        src = rack.output
    pull = Puller(src)
    ph = phrase(name)
    if detune:
        t0, p0, v0 = ph[10]
        ph[10] = (t0, p0 + 1, v0)
    held = []
    for b in range(blocks):
        while ph and ph[0][0] == b:
            _, p, v = ph.pop(0)
            inst.note_on(p, v, sample_position=(b * 37) % 256)
            held.append((b + 4, p))
        while held and held[0][0] == b:
            inst.note_off(held.pop(0)[1], sample_position=17)
        yield pull.block()
    if own:
        if rack is not None:
            rack.deinit()
        inst.deinit()


def lvgl_frames(n, plant=False):
    """Draw a busy screen ``n`` times with fixed ticks; hash of every frame."""
    import lvgl as lv

    if not lv.is_initialized():
        lv.init()
    w, h = 800, 480
    buf = bytearray(w * h * 2)
    disp = lv.display_create(w, h)
    disp.set_color_format(lv.COLOR_FORMAT.RGB565)
    digest = hashlib.sha256()

    def flush(d, area, px):
        digest.update(bytes(buf[:64]))
        d.flush_ready()

    disp.set_flush_cb(flush)
    disp.set_buffers(buf, None, len(buf), lv.DISPLAY_RENDER_MODE.FULL)
    scr = lv.obj(None)
    lv.screen_load(scr)
    bars, labels, arcs = [], [], []
    for i in range(24):
        b = lv.bar(scr)
        b.set_size(300, 12)
        b.set_pos(10, 10 + i * 18)
        bars.append(b)
        lab = lv.label(scr)
        lab.set_pos(330, 8 + i * 18)
        labels.append(lab)
    for i in range(6):
        a = lv.arc(scr)
        a.set_size(110, 110)
        a.set_pos(420 + (i % 3) * 120, 20 + (i // 3) * 130)
        arcs.append(a)
    for f in range(n):
        for i, b in enumerate(bars):
            b.set_value((f * 7 + i * 13) % 100, False)
        for i, lab in enumerate(labels):
            lab.set_text("track %d  %d" % (i, (f * 31 + i + (1 if plant and f == 10 and i == 0 else 0)) % 1000))
        for i, a in enumerate(arcs):
            a.set_value((f * 3 + i * 17) % 100)
        scr.invalidate()
        lv.tick_inc(33)
        lv.timer_handler()
        digest.update(bytes(buf))
    for x in bars + labels + arcs:
        x.delete()
    scr.delete()
    disp.delete()
    return digest.hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--threads", type=int, default=16)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--blocks", type=int, default=400)
    ap.add_argument("--lv-frames", type=int, default=150)
    ap.add_argument("--synthio-tls", action="store_true", help="use patched/synthio.py (thread-local block clock)")
    ap.add_argument("--interleave", action="store_true",
                    help="also render the threads' jobs on ONE thread, all alive at once, block by block")
    a = ap.parse_args()
    import lvgl  # noqa: F401
    import _audiodsp  # noqa: F401

    gil = getattr(sys, "_is_gil_enabled", lambda: True)()
    import synthio

    print("python", sys.version.split()[0], "GIL enabled after imports:", gil, "synthio from", synthio.__file__, flush=True)
    names = [n for n in INSTRUMENTS if n in audioinstruments.DRUM_MACHINES or n in audioinstruments.MELODIC]
    solo, solo_s = {}, 0.0
    nondet = []
    for name in names:
        t = time.perf_counter()
        x = render(name, a.blocks)
        solo_s += time.perf_counter() - t
        y = render(name, a.blocks)
        if x != y:
            nondet.append(name)
        solo[name] = x
    print("solo: %d instruments, %.2f s one after another; not deterministic alone: %s" % (
        len(names), solo_s, nondet or "none"), flush=True)
    good = [n for n in names if n not in nondet]
    if a.interleave:
        jobs = [good[i % len(good)] for i in range(a.threads)]
        gens = [renderer(n, a.blocks) for n in jobs]
        outs = [bytearray() for _ in jobs]
        for _ in range(a.blocks):
            for g, o in zip(gens, outs):
                o += next(g)
        for g in gens:
            for _ in g:
                pass
        bad = [(i, n) for i, n in enumerate(jobs) if bytes(outs[i]) != solo[n]]
        print("interleaved on one thread, %d alive at once: mismatches %d %s" % (len(jobs), len(bad), bad), flush=True)
    ref_lv = lvgl_frames(a.lv_frames)
    print("lvgl alone: frames hash", ref_lv[:16], flush=True)
    failures = 0
    for rnd in range(a.rounds):
        jobs = [good[i % len(good)] for i in range(a.threads)]
        results = [None] * len(jobs)
        errors = []
        start = threading.Barrier(len(jobs) + 1)

        def work(i, name):
            try:
                start.wait()
                results[i] = render(name, a.blocks, detune=PLANT == "detune" and i == 3)
            except Exception as e:  # noqa: BLE001
                errors.append((i, name, repr(e)))

        lv_hash = [None]
        ths = [threading.Thread(target=work, args=(i, n)) for i, n in enumerate(jobs)]
        lvt = threading.Thread(target=lambda: lv_hash.__setitem__(0, lvgl_frames(a.lv_frames, PLANT == "lvgl")))
        for th in ths:
            th.start()
        t = time.perf_counter()
        start.wait()
        lvt.start()
        for th in ths:
            th.join()
        wall = time.perf_counter() - t
        lvt.join()
        bad = [(i, n) for i, n in enumerate(jobs) if results[i] != solo[n]]
        serial = solo_s / len(names) * len(jobs)
        failures += len(bad) + len(errors) + (lv_hash[0] != ref_lv)
        print("round %d: %d threads + LVGL, %.2f s (one after another would take about %.2f s: %.1fx); "
              "mismatches %d %s; errors %s; LVGL frames %s" % (
                  rnd + 1, len(jobs), wall, serial, serial / wall, len(bad), bad[:6], errors[:3] or "none",
                  "identical" if lv_hash[0] == ref_lv else "DIFFERENT"), flush=True)
    print("GIL enabled at the end:", getattr(sys, "_is_gil_enabled", lambda: True)())
    print("RESULT", "PASS" if failures == 0 and not gil else "FAIL", "(%d failures)" % failures)
    sys.exit(0 if failures == 0 else 1)


if __name__ == "__main__":
    main()
