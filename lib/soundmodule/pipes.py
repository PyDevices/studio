# SPDX-FileCopyrightText: 2026 PyDevices
#
# SPDX-License-Identifier: MIT
"""MIDI through two files, for an interpreter that can't open the MIDI port
itself: MicroPython's and CircuitPython's unix ports under WSL, which see
no Windows MIDI port and have no ALSA one to open.

``tools/bridge.py``, on the Windows side, appends what a Windows MIDI port
receives to ``<folder>/in.mid`` and sends what is appended to
``<folder>/out.mid``. The module reads one file and appends to the other,
through ports with midiports' contract (``read``, ``readinto``, ``any``
and ``write``), so the rest of the module can't tell.

A read at the end of the file returns nothing and leaves the file open;
bytes appended later are read on a later call. Nothing here blocks.
"""

IN = "in.mid"
OUT = "out.mid"


def _count(buf, nbytes):
    n = len(buf)
    return n if nbytes is None or nbytes > n else nbytes


class FileIn:
    """The bytes appended to a file, as a MIDI input port."""

    def __init__(self, path, size=256):
        open(path, "ab").close()  # there, and empty if new
        try:
            self._f = open(path, "rb", buffering=0)  # CPython: no read-ahead past what is there
        except TypeError:
            self._f = open(path, "rb")
        self._scratch = bytearray(size)
        self._pending = 0

    def any(self):
        """Bytes read ahead and not yet taken: a peek reads up to the scratch's size."""
        if not self._pending:
            n = self._f.readinto(self._scratch)
            self._pending = n or 0
        return self._pending

    def readinto(self, buf, nbytes=None):
        want = _count(buf, nbytes)
        got = 0
        if self._pending:
            n = min(want, self._pending)
            for i in range(n):
                buf[i] = self._scratch[i]
            rest = self._pending - n
            for i in range(rest):
                self._scratch[i] = self._scratch[n + i]
            self._pending = rest
            got = n
        if got < want:
            if got:
                view = memoryview(buf)[got:want]
                n = self._f.readinto(view)
            elif want == len(buf):
                n = self._f.readinto(buf)
            else:
                n = self._f.readinto(memoryview(buf)[:want])
            got += n or 0
        return got or None

    def read(self, nbytes=None):
        buf = bytearray(_count(self._scratch, nbytes))
        n = self.readinto(buf)
        return bytes(buf[:n]) if n else None

    def close(self):
        if self._f is not None:
            self._f.close()
            self._f = None

    deinit = close


class FileOut:
    """A MIDI output port that appends to a file."""

    def __init__(self, path):
        self._f = open(path, "ab")

    def write(self, buf, nbytes=None):
        n = _count(buf, nbytes)
        self._f.write(bytes(buf[:n]) if n != len(buf) else buf)
        self._f.flush()
        return n

    def close(self):
        if self._f is not None:
            self._f.close()
            self._f = None

    deinit = close


def ports(folder):
    """``(midi_in, midi_out)`` through ``folder``'s two files."""
    folder = folder.rstrip("/")
    return FileIn(folder + "/" + IN), FileOut(folder + "/" + OUT)
