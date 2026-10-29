# SPDX-FileCopyrightText: 2026 PyDevices
#
# SPDX-License-Identifier: MIT
"""Run the sound module headless: MIDI in from a port, sound out to a WAV
file (and the sound card, where the interpreter has ``audiodev``).

    python -m soundmodule.run --port "Sound module device side" --wav song.wav
    micropython -m soundmodule.run --pipe /tmp/sm --wav song.wav --idle 4

``--port NAME`` opens the first MIDI input and output whose names contain
NAME, through ``midiports`` (Windows' own ports, or ALSA raw MIDI on
Linux). ``--pipe FOLDER`` reads and writes the two files
``tools/bridge.py`` keeps in step with a Windows port, for an interpreter
under WSL (``soundmodule.pipes``). Nothing is set up on the module: what
plays is whatever the MIDI asks for.

The sound is rendered as it is due, by the clock, so what reaches the WAV
is what a sound card would have played. ``--play`` plays it through
``audiodev`` instead, where there is one. It stops after ``--seconds`` (default
600), or ``--idle`` seconds after the last MIDI byte once some has come,
or ``--wait`` seconds after starting if none ever does; then it renders
``--tail`` seconds more, so the reverb dies away in the file.

At the end it prints what each part played and how far the renderer ever
fell behind the clock, and exits non-zero if no MIDI arrived.
"""

import sys
import time

from . import engine

RATE = 48000
CHANNELS = 2

if hasattr(time, "ticks_ms"):
    ticks_ms, ticks_diff = time.ticks_ms, time.ticks_diff
else:
    def ticks_ms():
        return int(time.monotonic() * 1000)

    def ticks_diff(a, b):
        return a - b


def _sleep_ms(ms):
    fn = getattr(time, "sleep_ms", None)
    if fn is not None:
        fn(ms)
    else:
        time.sleep(ms / 1000.0)


def _interpreter():
    impl = sys.implementation.name
    return "%s %s (%s)" % (impl, ".".join(str(v) for v in sys.implementation.version[:3]), sys.platform)


class Wav:
    """A 16-bit PCM WAV file written as it goes; ``close`` fixes the sizes."""

    def __init__(self, path, rate=RATE, channels=CHANNELS):
        self.f = open(path, "wb")
        self.rate = rate
        self.channels = channels
        self.bytes = 0
        self.f.write(self._header())

    def _header(self):
        import struct

        block = self.channels * 2
        return (b"RIFF" + struct.pack("<I", 36 + self.bytes) + b"WAVEfmt " +
                struct.pack("<IHHIIHH", 16, 1, self.channels, self.rate, self.rate * block, block, 16) +
                b"data" + struct.pack("<I", self.bytes))

    def write(self, data):
        self.f.write(data)
        self.bytes += len(data)

    def close(self):
        self.f.seek(0)
        self.f.write(self._header())
        self.f.close()


def _open_ports(port, pipe):
    if pipe:
        from . import pipes

        return pipes.ports(pipe)
    if sys.platform.startswith("win"):
        from midiports import windows as backend
    else:
        from midiports import linux as backend
    return backend.ports(port)


def _audio_out(module):
    """``audiodev``'s output playing the module, or None where there isn't one."""
    try:
        from audiodev import AudioFormat, AudioOut
    except ImportError:
        return None
    out = AudioOut(AudioFormat(RATE, CHANNELS, 16))
    out.play(module.output)
    return out


def _frames(data):
    """Frames in a block of 16-bit samples, from its bytes: CircuitPython's
    buffer counts samples, not bytes, so count ``bytes(buffer)``."""
    return len(data) // (2 * CHANNELS)


def serve(module, midi_in, wav=None, seconds=600, idle=None, wait=None, tail=2.0, out=None, log=print):
    """Feed ``midi_in`` to ``module`` and render its output by the clock
    into ``wav`` until one of the stops in the module's docstring. Returns
    ``(bytes of MIDI, [the most the render fell behind in ms before the
    first note, and after it])``."""
    import audiocore

    buf = bytearray(256)
    start = ticks_ms()
    last = None  # when MIDI last arrived
    total = 0
    frames = 0
    worst = [0, 0]  # behind the clock most, before the first note and after it
    playing = 0
    ending = None  # frames to stop at, once a stop is due
    while True:
        n = midi_in.readinto(buf) if midi_in is not None else None
        now = ticks_ms()
        if n:
            with engine.deferred(module):
                module.feed(buf, n)
            total += n
            last = now
        elapsed = ticks_diff(now, start)
        if ending is None:
            if elapsed >= seconds * 1000 or (
                    last is not None and idle is not None and ticks_diff(now, last) >= idle * 1000) or (
                    last is None and wait is not None and elapsed >= wait * 1000):
                ending = frames + int(tail * RATE)
        due = elapsed * RATE // 1000
        if ending is not None and frames >= ending:
            break
        if wav is not None and frames < due:
            data = bytes(audiocore.get_buffer(module.output)[1])
            wav.write(data)
            frames += _frames(data)
            behind = (due - frames) * 1000 // RATE
            if not playing:
                for part in module.parts:
                    if part.notes:
                        playing = 1
                        break
            if behind > worst[playing]:
                worst[playing] = behind
            continue  # the port again before the next block
        if wav is None:
            frames = due
        if out is not None:
            service = getattr(out, "service", None)
            if service is not None:
                service()
        _sleep_ms(1)
    return total, worst


def report(module, total, worst, log=print):
    """What came in and what played. ``worst`` is how far the render fell
    behind the clock at most, before the first note and after it: behind
    while notes play is what a listener would hear as a gap."""
    log("soundmodule: %s: %d bytes of MIDI, %d Identity Requests answered, render behind by at most %d ms"
        " while playing (%d ms before the first note)"
        % (_interpreter(), total, module.identities, worst[1], worst[0]))
    for part in module.parts:
        for key in sorted(part.played):
            notes, name = part.played[key]
            log("  channel %2d: %4d notes, bank %d:%d program %d, %s"
                % (part.channel + 1, notes, key[0], key[1], key[2], name))
    if module.error is not None:
        log("  last error: %s" % (module.error,))


def _args(argv):
    opts = {"port": "Sound module device side", "pipe": None, "wav": None, "seconds": 600.0, "idle": None,
            "wait": None, "tail": 2.0, "play": False, "bank": None}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--play":
            opts["play"] = True
            i += 1
            continue
        if not a.startswith("--") or a[2:] not in opts or i + 1 >= len(argv):
            raise SystemExit("usage: run.py [--port NAME | --pipe FOLDER] [--wav PATH] [--seconds S] "
                             "[--idle S] [--wait S] [--tail S] [--bank SF2] [--play]")
        key, value = a[2:], argv[i + 1]
        opts[key] = float(value) if key in ("seconds", "idle", "wait", "tail") else value
        i += 2
    return opts


def main(argv=None):
    opts = _args(sys.argv[1:] if argv is None else argv)
    if opts["play"] and opts["wav"]:
        raise SystemExit("run.py: --play or --wav, not both: each would take every other block of sound")
    midi_in, midi_out = _open_ports(opts["port"], opts["pipe"])
    replies = [0, 0]  # sent, refused by the port

    def send(data):
        if midi_out is None:
            return
        try:
            midi_out.write(data)
            replies[0] += 1
        except NotImplementedError:  # a port that can't send SysEx
            replies[1] += 1

    module = engine.Module(RATE, CHANNELS, bank=opts["bank"], send=send)
    module.prepare()  # the SoundFont, if there is one, opened before anything plays
    wav = Wav(opts["wav"]) if opts["wav"] else None
    out = _audio_out(module) if opts["play"] else None
    print("soundmodule: listening on %s, %s" % (opts["pipe"] or opts["port"], _interpreter()))
    try:
        total, worst = serve(module, midi_in, wav, opts["seconds"], opts["idle"], opts["wait"], opts["tail"], out)
    finally:
        if wav is not None:
            wav.close()
        if out is not None:
            out.stop()
    report(module, total, worst)
    if replies[0] or replies[1]:
        print("  replies sent: %d, refused by the port: %d" % (replies[0], replies[1]))
    module.close()
    for p in (midi_in, midi_out):
        if p is not None:
            p.close()
    if not total:
        print("soundmodule: no MIDI arrived")
        sys.exit(1)


if __name__ == "__main__":
    main()
