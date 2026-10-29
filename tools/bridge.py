# SPDX-FileCopyrightText: 2026 PyDevices
#
# SPDX-License-Identifier: MIT
"""Carry a Windows MIDI port to the sound module under WSL, through two
files (``soundmodule.pipes``).

MicroPython's and CircuitPython's unix ports, run under WSL, see none of
Windows' MIDI ports. This runs on the Windows side, on CPython or
``micropython.exe`` with ``midiports``: what the port receives is appended
to ``<folder>/in.mid`` as it arrives, and what the module appends to
``<folder>/out.mid`` is sent out of the port.

    python.exe tools/bridge.py --port "Sound module device side" --pipe \\\\wsl.localhost\\Ubuntu\\tmp\\sm

It stops when a file named ``stop`` appears in the folder, or after
``--seconds``.
"""

import sys
import time


def _now():
    return time.monotonic() if hasattr(time, "monotonic") else time.time()


def main(argv):
    opts = {"port": "Sound module device side", "pipe": None, "seconds": "3600"}
    for i in range(0, len(argv) - 1, 2):
        opts[argv[i].lstrip("-")] = argv[i + 1]
    folder = opts["pipe"].rstrip("/\\")
    sep = "\\" if "\\" in folder else "/"
    from midiports import windows

    midi_in, midi_out = windows.ports(opts["port"])
    open(folder + sep + "out.mid", "ab").close()
    to_module = open(folder + sep + "in.mid", "ab")
    from_module = open(folder + sep + "out.mid", "rb")
    buf = bytearray(256)
    start = _now()
    sent = received = 0
    checked = start
    print("bridge: %s <-> %s" % (opts["port"], folder))
    while _now() - start < float(opts["seconds"]):
        n = midi_in.readinto(buf)
        if n:
            to_module.write(bytes(buf[:n]))
            to_module.flush()
            received += n
        back = from_module.read(256)
        if back:
            try:
                midi_out.write(back)
            except NotImplementedError as e:  # a SysEx this port can't send
                print("bridge: %s" % (e,))
            sent += len(back)
        if not n:
            if _now() - checked >= 0.2:  # the stop file, a few times a second
                checked = _now()
                try:
                    open(folder + sep + "stop", "rb").close()
                    break
                except OSError:
                    pass
            time.sleep(0.001)
    to_module.close()
    from_module.close()
    midi_in.close()
    midi_out.close()
    print("bridge: %d bytes to the module, %d back" % (received, sent))


if __name__ == "__main__":
    main(sys.argv[1:])
