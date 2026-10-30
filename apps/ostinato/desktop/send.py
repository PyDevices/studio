"""Play keys into a Windows MIDI port, stamping each with perf_counter_ns
(QueryPerformanceCounter: the same clock in every process on the machine).

    python.exe -I desktop/send.py --port "Ostinato Loopback" --out runs/x --seconds 60

Waits for ``<out>/ready`` (the app writes it when the song starts), then
sends a note-on every 400-700 ms (random, seeded) at a random pitch from
C3 to G5 (where YIN reads Karplus reliably), each held 250 ms, until ``--seconds`` have passed. Writes
``<out>/sent.json``: [[ns, [status, d1, d2]], ...].
"""

import argparse
import json
import os
import random
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(os.path.join(ROOT, "lib"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="Ostinato Loopback")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--wait", type=float, default=180, help="seconds to wait for the app to be ready")
    a = ap.parse_args()
    import ctypes

    ctypes.windll.winmm.timeBeginPeriod(1)
    from midiports import windows

    outs = windows.find(a.port, "out")
    if not outs:
        sys.exit("no MIDI output matching %r" % a.port)
    port = windows.open_port(outs[0])
    ready = os.path.join(a.out, "ready")
    end_wait = time.monotonic() + a.wait
    while not os.path.exists(ready):
        if time.monotonic() > end_wait:
            sys.exit("the app never got ready")
        time.sleep(0.05)
    time.sleep(2.0)
    rng = random.Random(a.seed)
    sent = []
    t_end = time.perf_counter() + a.seconds
    off_at = None
    pitch = None
    next_on = time.perf_counter()

    def write(data):
        port.write(bytes(data), len(data))
        sent.append([time.perf_counter_ns(), data])

    while True:
        now = time.perf_counter()
        if off_at is not None and now >= off_at:
            write([0x80, pitch, 0])
            off_at = None
        if now >= next_on:
            if now >= t_end:
                break
            pitch = rng.randint(48, 79)
            write([0x90, pitch, rng.randint(80, 120)])
            off_at = now + 0.25
            next_on = now + rng.uniform(0.4, 0.7)
        nxt = min(x for x in (off_at, next_on) if x is not None)
        left = nxt - time.perf_counter()
        if left > 0.003:
            time.sleep(left - 0.002)
    if off_at is not None:
        write([0x80, pitch, 0])
    time.sleep(0.3)
    port.close()
    with open(os.path.join(a.out, "sent.json"), "w") as f:
        json.dump(sent, f)
    print("sent", sum(1 for _, d in sent if d[0] == 0x90), "notes", flush=True)


if __name__ == "__main__":
    main()
