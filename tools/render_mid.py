# SPDX-FileCopyrightText: 2026 PyDevices
#
# SPDX-License-Identifier: MIT
"""Render a Standard MIDI File through the sound module, offline, to a WAV.

    python tools/render_mid.py SONG.mid OUT.wav [--bank X.sf2] [--path DIR ...]
        [--tail SECONDS] [--plant NAME]
    micropython -X heapsize=64M tools/render_mid.py ...

Each event is fed to a ``soundmodule.engine.Module`` set up as the headless
runner sets it up, before the block it falls in is pulled, so timing is to
the block. Runs on CPython, MicroPython and CircuitPython. ``--path`` puts
folders first on ``sys.path`` (audiocomponents' ``lib``, this repository's
``lib``). ``--plant`` breaks the module in a known way first: one of
``engine_contract.PLANTS``, or ``note-off-dropped`` (every 5th note-off is
lost), so the gate can show its checks fail.

At the end it prints one line per measure, ``key value``, for the gate:
``notes`` (note-ons the file has), ``played`` (note-ons the parts took),
``held`` (notes a part still holds after the song), ``sounding`` (engine
voices still sounding after the tail), ``song`` and ``render`` (seconds of
music and of wall clock), and ``error`` if one was raised.
"""

import sys
import time

if hasattr(time, "ticks_ms"):
    def _now():
        return time.ticks_ms()

    def _since(t):
        return time.ticks_diff(time.ticks_ms(), t) / 1000.0
else:
    def _now():
        return time.monotonic()

    def _since(t):
        return time.monotonic() - t


def _dir(path):
    sep = "\\" if "\\" in path else "/"
    return path.rsplit(sep, 1)[0] if sep in path else ".", sep


def main(argv):
    args = []
    bank = None
    tail = 3.0
    plant = None
    paths = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--bank":
            bank = argv[i + 1]
            i += 1
        elif a == "--tail":
            tail = float(argv[i + 1])
            i += 1
        elif a == "--plant":
            plant = argv[i + 1]
            i += 1
        elif a == "--path":
            paths.append(argv[i + 1])
            i += 1
        else:
            args.append(a)
        i += 1
    song, out = args[0], args[1]
    here, sep = _dir(__file__ if "__file__" in globals() else sys.argv[0])
    for p in reversed(paths + [here, sep.join((here, "..", "lib")), sep.join((here, "..", "tests"))]):
        sys.path.insert(0, p)

    import audiocore
    import smf
    from soundmodule import engine, run

    if plant == "note-off-dropped":
        old = engine.Module.note_off
        count = [0]

        def note_off(self, part, note):
            count[0] += 1
            if count[0] % 5:
                old(self, part, note)

        engine.Module.note_off = note_off
        print("PLANTED " + plant)
    elif plant:
        import engine_contract
        engine_contract.plant(plant)

    events, seconds = smf.read(song)
    notes = 0
    for _t, m in events:
        if (m[0] & 0xF0) == 0x90 and len(m) > 2 and m[2]:
            notes += 1
    module = engine.Module(run.RATE, run.CHANNELS, bank=bank)
    module.prepare()
    if module.error is not None:
        print("error %r" % (module.error,))
    wav = run.Wav(out)
    started = _now()
    frames = 0
    k = 0
    end = int((seconds + tail) * run.RATE)
    error = None
    try:
        while frames < end:
            now = frames / run.RATE
            if k < len(events) and events[k][0] <= now:
                with engine.deferred(module):
                    while k < len(events) and events[k][0] <= now:
                        module.feed(events[k][1])
                        k += 1
            data = bytes(audiocore.get_buffer(module.output)[1])
            wav.write(data)
            frames += run._frames(data)
    except Exception as e:  # reported, and the gate fails on it
        error = e
        sys.print_exception(e) if hasattr(sys, "print_exception") else __import__("traceback").print_exc()
    wav.close()
    render = _since(started)
    played = 0
    held = 0
    for part in module.parts:
        played += part.notes
        held += len(part.held) + len(part.sustained)
    pool = module.voice_pool
    sounding = pool.sounding() if pool is not None and hasattr(pool, "sounding") else -1
    print("notes %d" % notes)
    print("played %d" % played)
    print("held %d" % held)
    print("sounding %d" % sounding)
    print("song %.3f" % seconds)
    print("render %.3f" % render)
    print("reverb %d" % module.reverb_level)
    print("chorus %d" % module.chorus_level)
    if error is not None:
        print("error %r" % (error,))
    run.report(module, 0, [0, 0])
    module.close()


main(sys.argv[1:])
