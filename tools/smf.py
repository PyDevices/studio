# SPDX-FileCopyrightText: 2026 PyDevices
#
# SPDX-License-Identifier: MIT
"""A Standard MIDI File, read into one list of timed events.

    events, seconds = smf.read("song.mid")

``events`` is ``[(time in seconds, bytes), ...]`` in play order: channel
messages and SysEx (``F0 ... F7``), every track merged, the tempo map
applied. ``seconds`` is when the last event falls. Meta events other than
Set Tempo are dropped. Runs on CPython, MicroPython and CircuitPython.
"""


def _varlen(data, i):
    v = 0
    while True:
        b = data[i]
        i += 1
        v = (v << 7) | (b & 0x7F)
        if not b & 0x80:
            return v, i


def _u32(data, i):
    return (data[i] << 24) | (data[i + 1] << 16) | (data[i + 2] << 8) | data[i + 3]


def _track(data, i, end, out, track):
    """Append (tick, track, order, bytes) for each event; tempos are (tick, None, order, tempo)."""
    tick = 0
    status = 0
    order = 0
    while i < end:
        delta, i = _varlen(data, i)
        tick += delta
        b = data[i]
        if b == 0xFF:
            kind = data[i + 1]
            length, j = _varlen(data, i + 2)
            if kind == 0x51 and length == 3:
                out.append((tick, -1, order, (data[j] << 16) | (data[j + 1] << 8) | data[j + 2]))
            i = j + length
            if kind == 0x2F:
                break
        elif b in (0xF0, 0xF7):
            length, j = _varlen(data, i + 1)
            body = bytes(data[j:j + length])
            if b == 0xF0:
                out.append((tick, track, order, b"\xf0" + body))
            i = j + length
            status = 0
        else:
            if b & 0x80:
                status = b
                i += 1
            need = 1 if (status & 0xF0) in (0xC0, 0xD0) else 2
            out.append((tick, track, order, bytes((status,)) + bytes(data[i:i + need])))
            i += need
        order += 1


def read(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"MThd":
        raise ValueError("not a Standard MIDI File")
    ntracks = (data[10] << 8) | data[11]
    division = (data[12] << 8) | data[13]
    if division & 0x8000:
        raise ValueError("SMPTE time isn't read here")
    i = 8 + _u32(data, 4)
    raw = []
    for t in range(ntracks):
        if data[i:i + 4] != b"MTrk":
            break
        length = _u32(data, i + 4)
        _track(data, i + 8, i + 8 + length, raw, t)
        i += 8 + length
    # Tempo changes first at a tick, then by track and order within it.
    raw.sort(key=lambda e: (e[0], 0 if e[1] < 0 else 1, e[1], e[2]))
    events = []
    tempo = 500000
    last_tick = 0
    now = 0.0
    for tick, track, _order, item in raw:
        now += (tick - last_tick) * tempo / (division * 1e6)
        last_tick = tick
        if track < 0:
            tempo = item
        else:
            events.append((now, item))
    return events, now
