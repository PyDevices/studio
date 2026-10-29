# SPDX-FileCopyrightText: 2026 PyDevices
#
# SPDX-License-Identifier: MIT
"""Write the gate's song: one Standard MIDI File per part, plus what the
module should report after hearing it.

    python tools/song.py OUT_FOLDER

Every part chooses its own sound at its first tick with Bank Select and
Program Change, so nothing needs setting up on the module. The parts come
in one at a time, two bars apart, so each one can be heard arriving:

    bar 2   channel 1   juno106 pad (bank 80), its Cutoff macro (CC 103) swept from bar 8
    bar 4   channel 2   minimoog bass (bank 80, patch 1 by bank LSB)
    bar 6   channel 10  tr808 (bank 81)
    bar 8   channel 3   rhodes, panned left to right by CC 10 from bar 10
    bar 10  channel 4   prophet5 lead, faded up by CC 7
    bar 14  the end

Bar 1 holds only the setup and an Identity Request, so the module has a
bar to build its instruments before the first note. 120 bpm: a bar is two
seconds. ``song.json`` beside the files lists each part's channel, bank,
program, note count and when it starts, for ``tools/gate.py`` to check.
"""

import json
import os
import struct
import sys

PPQ = 480
BAR = 4 * PPQ
BPM = 120
SECONDS_PER_BAR = 4 * 60.0 / BPM
BARS = 13  # the music ends here; bar 14 lets it ring

BANK_INSTRUMENTS = 80
BANK_DRUM_MACHINES = 81
IDENTITY_REQUEST = bytes([0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7])


def _bar(n, beat=0.0):
    """The tick of bar ``n`` (from 1), plus ``beat`` quarter notes."""
    return (n - 1) * BAR + int(beat * PPQ)


def _vlq(n):
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


class Track:
    def __init__(self, name, channel):
        self.name = name
        self.channel = channel  # 0-based
        self.events = []  # (tick, order, bytes)
        self.notes = 0

    def add(self, tick, data, order=1):
        self.events.append((tick, order, bytes(data)))

    def setup(self, msb, lsb, program):
        c = self.channel
        self.add(0, [0xB0 | c, 0, msb], 0)
        self.add(0, [0xB0 | c, 32, lsb], 0)
        self.add(0, [0xC0 | c, program], 0)

    def note(self, tick, length, key, velocity=96):
        c = self.channel
        self.add(tick, [0x90 | c, key, velocity], 2)
        self.add(tick + length, [0x80 | c, key, 0], 0)  # offs before ons at the same tick
        self.notes += 1

    def ramp(self, cc, start, end, low, high, steps=32):
        for i in range(steps + 1):
            self.add(start + (end - start) * i // steps, [0xB0 | self.channel, cc, low + (high - low) * i // steps])

    def smf(self):
        body = bytearray()
        body += b"\x00\xff\x03" + _vlq(len(self.name)) + self.name.encode()
        body += b"\x00\xff\x51\x03" + struct.pack(">I", 60000000 // BPM)[1:]
        now = 0
        for tick, _, data in sorted(self.events, key=lambda e: (e[0], e[1])):
            body += _vlq(tick - now)
            if data[0] == 0xF0:
                body += b"\xf0" + _vlq(len(data) - 1) + data[1:]
            else:
                body += data
            now = tick
        body += _vlq(_bar(BARS + 1) - now) + b"\xff\x2f\x00"
        return b"MThd" + struct.pack(">IHHH", 6, 0, 1, PPQ) + b"MTrk" + struct.pack(">I", len(body)) + bytes(body)


CHORDS = ((57, 60, 64), (53, 57, 60), (48, 52, 55), (55, 59, 62))  # Am F C G
ROOTS = (45, 41, 36, 43)


def song():
    tracks = []

    pad = Track("juno106 pad", 0)
    pad.setup(BANK_INSTRUMENTS, 0, 14)
    for bar in range(2, BARS + 1):
        for key in CHORDS[bar % 4]:
            pad.note(_bar(bar), BAR - 20, key, 80)
    pad.ramp(103, _bar(8), _bar(12), 20, 120)  # Cutoff
    tracks.append((pad, 2, "juno106"))

    bass = Track("minimoog bass", 1)
    bass.setup(BANK_INSTRUMENTS, 1, 20)
    for bar in range(4, BARS + 1):
        root = ROOTS[bar % 4]
        for eighth in range(8):
            bass.note(_bar(bar, eighth / 2), PPQ // 2 - 30, root + (12 if eighth % 4 == 3 else 0), 100)
    tracks.append((bass, 4, "minimoog"))

    drums = Track("tr808", 9)
    drums.setup(BANK_DRUM_MACHINES, 0, 10)
    for bar in range(6, BARS + 1):
        for beat in range(4):
            drums.note(_bar(bar, beat), PPQ // 4, 36 if beat % 2 == 0 else 38, 110)
        for eighth in range(8):
            drums.note(_bar(bar, eighth / 2), PPQ // 8, 42, 70)
    tracks.append((drums, 6, "tr808"))

    keys = Track("rhodes", 2)
    keys.setup(BANK_INSTRUMENTS, 0, 32)
    for bar in range(8, BARS + 1):
        for half in (0, 2):
            for key in CHORDS[bar % 4]:
                keys.note(_bar(bar, half + 0.5), PPQ, key + 12, 85)
    keys.ramp(10, _bar(10), _bar(13), 0, 127)
    tracks.append((keys, 8, "rhodes"))

    lead = Track("prophet5 lead", 3)
    lead.setup(BANK_INSTRUMENTS, 0, 30)
    melody = (76, 74, 72, 74, 76, 76, 76, 79, 77, 76, 74, 72, 71, 72, 74, 76)
    for i in range((BARS - 9) * 4):
        lead.note(_bar(10, i), PPQ - 40, melody[i % len(melody)], 100)
    lead.ramp(7, _bar(10), _bar(11), 40, 120, 16)
    lead.add(0, IDENTITY_REQUEST)  # the module answers through its MIDI out
    tracks.append((lead, 10, "prophet5"))
    return tracks


def main(argv):
    if len(argv) != 1:
        raise SystemExit("usage: song.py OUT_FOLDER")
    folder = argv[0]
    os.makedirs(folder, exist_ok=True)
    parts = []
    for track, bar, inst in song():
        name = "ch%02d-%s.mid" % (track.channel + 1, inst)
        with open(os.path.join(folder, name), "wb") as f:
            f.write(track.smf())
        setup = [e[2] for e in track.events if e[1] == 0 and e[0] == 0 and e[2][0] & 0xF0 in (0xB0, 0xC0)]
        parts.append({
            "file": name,
            "channel": track.channel + 1,
            "instrument": inst,
            "bank_msb": setup[0][2],
            "bank_lsb": setup[1][2],
            "program": setup[2][1],
            "notes": track.notes,
            "starts": (bar - 1) * SECONDS_PER_BAR,
        })
    info = {"bpm": BPM, "seconds": (BARS + 1) * SECONDS_PER_BAR, "bar_seconds": SECONDS_PER_BAR,
            "music_ends": BARS * SECONDS_PER_BAR,
            "identity_requests": 1, "parts": parts}
    with open(os.path.join(folder, "song.json"), "w") as f:
        json.dump(info, f, indent=1)
    for p in parts:
        print("%-22s channel %2d  bank %d:%d program %2d  %3d notes from %4.1f s"
              % (p["file"], p["channel"], p["bank_msb"], p["bank_lsb"], p["program"], p["notes"], p["starts"]))


if __name__ == "__main__":
    main(sys.argv[1:])
