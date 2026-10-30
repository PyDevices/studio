"""MIDI in on the desktop: Windows MIDI ports, read once, relayed to every engine.

The browser's page relays Web MIDI to the engine workers; here one thread in
the UI process reads ``midiports.windows`` and hands each message, stamped
with ``perf_counter_ns``, to the runner, which sends it to every engine. The
engines turn the stamp into an audio frame through their ring's clock.

winmm posts a port's input to the thread that opened it, so this thread
opens the ports and reads them. ``names`` picks ports by part of their name
(case-insensitive); empty means every input there is.
"""

import threading
import time


class MidiIn:
    def __init__(self, runner, names=()):
        self.runner = runner
        self.names = [n for n in names if n]
        self.stop_flag = threading.Event()
        self.opened = []
        self.thread = threading.Thread(target=self._run, name="midi-in", daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_flag.set()
        self.thread.join(2)

    def _ports(self, windows):
        if not self.names:
            return list(windows.find("", "in"))
        out = []
        for n in self.names:
            for p in windows.find(n, "in"):
                if p not in out:
                    out.append(p)
        return out

    def _run(self):
        try:
            import ctypes

            ctypes.windll.winmm.timeBeginPeriod(1)     # 1 ms sleeps, not 15.6
        except Exception:  # noqa: BLE001
            pass
        import adafruit_midi
        # adafruit_midi parses only the message types whose classes are imported.
        from adafruit_midi.channel_pressure import ChannelPressure  # noqa: F401
        from adafruit_midi.control_change import ControlChange  # noqa: F401
        from adafruit_midi.note_off import NoteOff  # noqa: F401
        from adafruit_midi.note_on import NoteOn  # noqa: F401
        from adafruit_midi.pitch_bend import PitchBend  # noqa: F401
        from midiports import windows

        readers = []
        for info in self._ports(windows):
            try:
                port = windows.open_port(info)
            except OSError as e:
                print("MIDI in: %s didn't open: %s" % (info.name, e), flush=True)
                continue
            readers.append((port, adafruit_midi.MIDI(midi_in=port, in_buf_size=64)))
            self.opened.append(info.name)
        print("MIDI in:", ", ".join(self.opened) or "none", flush=True)
        try:
            while not self.stop_flag.is_set():
                busy = False
                for port, midi in readers:
                    if not port.any() and not midi._in_buf:
                        continue
                    busy = True
                    stamp = time.perf_counter_ns()
                    while True:
                        msg = midi.receive()
                        if msg is None:
                            break
                        if getattr(msg, "_STATUS", None) is None:
                            continue
                        d = bytes(msg)
                        if len(d) < 2 or d[0] >= 0xF0:
                            continue
                        self.runner.midi(d[0], d[1], d[2] if len(d) > 2 else 0, stamp)
                if not busy:
                    time.sleep(0.0005)
        finally:
            for port, _ in readers:
                port.close()
