"""A stand-in keyboard for the checks: plays MIDI into a port, on time.

    python desktop/midi_send.py "Ostinato Loopback"     (the control socket starts it)

Each line on stdin is a JSON list of [ms from now, [status, d1, d2]]. It runs
in a process of its own, as a real keyboard would be, so the app being busy
can't hold its notes back.
"""

import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "vendor", "lib"))


def main():
    import ctypes

    ctypes.windll.winmm.timeBeginPeriod(1)
    from midiports import windows

    outs = windows.find(sys.argv[1], "out")
    if not outs:
        print("midi_send: no MIDI output matching", sys.argv[1], flush=True)
        return
    port = windows.open_port(outs[0])
    try:
        for line in sys.stdin:
            if not line.strip():
                continue
            t0 = time.perf_counter()
            for ms, data in sorted(json.loads(line), key=lambda e: e[0]):
                due = t0 + ms / 1000.0
                wait = due - time.perf_counter()
                if wait > 0.002:
                    time.sleep(wait - 0.0015)
                while time.perf_counter() < due:
                    pass
                port.write(bytes(data))
    finally:
        port.close()


if __name__ == "__main__":
    main()
