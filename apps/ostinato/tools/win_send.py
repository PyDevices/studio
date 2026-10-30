"""Send a scenario into a Windows MIDI port in real time (runs under python.exe).

    python.exe win_send.py "Ostinato Loopback" events.json sent.json

tools/midi_play.py --real-midi stages this, midiports, uwin32 and
adafruit_midi in C:\\temp\\ostinato-midi and runs it, so the installed app in
Windows Chrome gets real Web MIDI from a real (loopback) port. events.json is
[[ms_from_start, [bytes...]], ...]; sent.json records when each was written.
"""

import json
import sys
import time

sys.path.insert(0, ".")
from midiports import windows  # noqa: E402


def main():
    name, events_path, sent_path = sys.argv[1:4]
    events = json.load(open(events_path))
    _, midi_out = windows.ports(name)
    sent = []
    t0 = time.perf_counter()
    for ms, data in events:
        due = t0 + ms / 1000
        while True:
            left = due - time.perf_counter()
            if left <= 0:
                break
            time.sleep(left - 0.002 if left > 0.003 else 0)
        buf = bytes(data)
        midi_out.write(buf, len(buf))
        sent.append({"ms": (time.perf_counter() - t0) * 1000, "d": data})
    time.sleep(0.5)
    midi_out.close()
    json.dump(sent, open(sent_path, "w"))
    print("sent", len(sent), flush=True)


if __name__ == "__main__":
    main()
