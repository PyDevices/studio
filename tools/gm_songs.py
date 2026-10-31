# SPDX-FileCopyrightText: 2026 PyDevices
#
# SPDX-License-Identifier: MIT
"""The project 5 gate's own songs, and the bank they're measured on.

    python tools/gm_songs.py FOLDER --audioinstruments PATH

Writes ``bends.mid`` and ``resets.mid`` into FOLDER, each with a
``.json`` of what to measure where, and ``sine.sf2``: every GM program and
the standard kit play one looped sine at 441 Hz on key 69 (a 50-frame
period at 22050 Hz), so a note's pitch and level can be read off the WAV.
The bank is built with audioinstruments' ``tests/support/sf2_builder.py``.

The songs are written the way a sequencer writes a GM file: GM System On,
then per channel Reset All Controllers, Bank Select, Program Change,
Volume, Pan, the sends and the bend range by RPN 0 before the first note.

A probe is ``{"kind": "pitch", "t": [from, to], "hz": f}``, ``{"kind":
"level", "t": [...], "db": d}`` (relative to the song's reference note)
``{"kind": "silent", "t": [...]}`` or ``{"kind": "ringing", "t": [...]}``
(the reverb's tail, at least 50 dB under the reference but not silent).
"""

import json
import math
import os
import struct
import sys

TPQ = 480
TEMPO = 500000  # 120 bpm: 960 ticks a second
TICKS = 960
BASE_HZ = 441.0
GM_ON = b"\xf0\x7e\x7f\x09\x01\xf7"


def _varlen(n):
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


def write_mid(path, events):
    """``events``: (seconds, bytes) in any order; one track, format 0."""
    events = sorted(events, key=lambda e: e[0])
    body = b"\x00\xff\x51\x03" + struct.pack(">I", TEMPO)[1:]
    last = 0
    for t, msg in events:
        tick = int(round(t * TICKS))
        delta = tick - last
        last = tick
        if msg[0] == 0xF0:
            body += _varlen(delta) + b"\xf0" + _varlen(len(msg) - 1) + msg[1:]
            continue
        i = 0
        while i < len(msg):  # several messages in one: each its own event
            n = 2 if (msg[i] & 0xF0) in (0xC0, 0xD0) else 3
            body += _varlen(delta) + msg[i:i + n]
            delta = 0
            i += n
    body += b"\x00\xff\x2f\x00"
    with open(path, "wb") as f:
        f.write(b"MThd" + struct.pack(">IHHH", 6, 0, 1, TPQ))
        f.write(b"MTrk" + struct.pack(">I", len(body)) + body)


def cc(ch, n, v):
    return bytes((0xB0 | ch, n, v))


def rpn(ch, msb, lsb, data_msb, data_lsb=None):
    out = cc(ch, 101, msb) + cc(ch, 100, lsb) + cc(ch, 6, data_msb)
    if data_lsb is not None:
        out += cc(ch, 38, data_lsb)
    return out + cc(ch, 101, 127) + cc(ch, 100, 127)


def setup(ch, program=0, volume=100):
    """A sequencer's channel header; the sends at 0 so the levels are the dry note's."""
    return (cc(ch, 121, 0) + cc(ch, 0, 0) + cc(ch, 32, 0) + bytes((0xC0 | ch, program)) + cc(ch, 7, volume)
            + cc(ch, 10, 64) + cc(ch, 91, 0) + cc(ch, 93, 0) + rpn(ch, 0, 0, 2, 0))


def note(ch, t0, t1, key=69, vel=100):
    return [(t0, bytes((0x90 | ch, key, vel))), (t1, bytes((0x80 | ch, key, 0)))]


def bend(ch, value):
    return bytes((0xE0 | ch, value & 0x7F, value >> 7))


def hz(semitones):
    return BASE_HZ * 2 ** (semitones / 12.0)


def bends():
    ev = [(0.0, GM_ON)]
    for ch in range(4):
        ev.append((0.1, setup(ch)))
    probes = []
    # Channel 1: RPN 0 = 12, then the wheel full up, full down, centre.
    ev.append((0.2, rpn(0, 0, 0, 12, 0)))
    ev += note(0, 0.5, 4.5)
    ev += [(1.5, bend(0, 16383)), (2.5, bend(0, 0)), (3.5, bend(0, 8192))]
    probes += [{"kind": "pitch", "t": [0.7, 1.4], "hz": hz(0), "what": "ch1 wheel centred"},
               {"kind": "pitch", "t": [1.7, 2.4], "hz": hz(12 * 8191 / 8192.0), "what": "ch1 full up, range 12"},
               {"kind": "pitch", "t": [2.7, 3.4], "hz": hz(-12), "what": "ch1 full down, range 12"},
               {"kind": "pitch", "t": [3.7, 4.4], "hz": hz(0), "what": "ch1 centred again"}]
    # Channel 2: coarse +7 (RPN 2), fine +50 cents (RPN 1); then RPN null
    # and a stray Data Entry, which must change nothing.
    ev.append((4.8, rpn(1, 0, 2, 71) + rpn(1, 0, 1, 96, 0)))
    ev += note(1, 5.0, 6.0)
    ev.append((6.2, cc(1, 6, 0) + cc(1, 38, 0)))
    ev += note(1, 6.4, 7.2)
    probes += [{"kind": "pitch", "t": [5.2, 5.9], "hz": hz(7.5), "what": "ch2 tuned +7.5 by RPN 2 and 1"},
               {"kind": "pitch", "t": [6.6, 7.1], "hz": hz(7.5), "what": "ch2 after RPN null and a stray CC6"}]
    # Channel 3: RPN 0 raised by Increment, from 2 to 5, then full down.
    ev.append((7.4, cc(2, 101, 0) + cc(2, 100, 0) + cc(2, 96, 0) + cc(2, 96, 0) + cc(2, 96, 0)
               + cc(2, 101, 127) + cc(2, 100, 127) + bend(2, 0)))
    ev += note(2, 7.6, 8.4)
    probes.append({"kind": "pitch", "t": [7.8, 8.3], "hz": hz(-5), "what": "ch3 range 5 by Increment, full down"})
    # Channel 4: RPN 0 = 24, Reset All Controllers keeps it.
    ev.append((8.6, rpn(3, 0, 0, 24, 0) + cc(3, 121, 0) + bend(3, 0)))
    ev += note(3, 8.8, 9.6)
    probes.append({"kind": "pitch", "t": [9.0, 9.5], "hz": hz(-24), "what": "ch4 range 24 kept over CC121"})
    # A bend left up on channel 1 and the tuning on 2, then GM System On.
    ev.append((9.8, bend(0, 16383)))
    ev.append((10.0, GM_ON))
    ev.append((10.1, b"".join(cc(ch, 91, 0) for ch in range(4))))
    ev += note(0, 10.3, 11.1)
    ev += note(1, 11.3, 12.1)
    probes += [{"kind": "pitch", "t": [10.5, 11.0], "hz": hz(0), "what": "ch1 after GM On: wheel centred"},
               {"kind": "pitch", "t": [11.5, 12.0], "hz": hz(0), "what": "ch2 after GM On: tuning gone"},
               {"kind": "silent", "t": [12.4, 13.0], "what": "nothing left sounding"}]
    return ev, probes


def resets():
    ev = [(0.0, GM_ON), (0.1, setup(0))]
    probes = []
    ev += note(0, 0.3, 1.1)
    ref = [0.5, 1.0]
    ev.append((1.2, cc(0, 7, 50)))
    ev += note(0, 1.3, 2.1)
    probes.append({"kind": "level", "t": [1.5, 2.0], "db": 40 * math.log10(50 / 100.0), "what": "CC7 50"})
    ev.append((2.2, cc(0, 11, 64)))
    ev += note(0, 2.3, 3.1)
    probes.append({"kind": "level", "t": [2.5, 3.0], "db": 40 * math.log10(50 / 100.0 * 64 / 127.0),
                   "what": "CC7 50, CC11 64"})
    ev.append((3.2, cc(0, 121, 0)))
    ev += note(0, 3.3, 4.1)
    probes.append({"kind": "level", "t": [3.5, 4.0], "db": 40 * math.log10(50 / 100.0),
                   "what": "CC121: Expression back to 127, CC7 kept"})
    ev.append((4.2, GM_ON))
    ev.append((4.25, cc(0, 91, 0)))
    ev += note(0, 4.3, 5.1)
    probes.append({"kind": "level", "t": [4.5, 5.0], "db": 0.0, "what": "GM On: volume back to 100"})
    # All Sound Off on a held note, which never gets its note-off.
    ev.append((5.3, bytes((0x90, 69, 100))))
    ev.append((5.8, cc(0, 120, 0)))
    probes.append({"kind": "silent", "t": [5.9, 6.3], "what": "CC120 silences a held note"})
    # The pedal holds a released note; Reset All Controllers lifts it.
    ev += [(6.5, cc(0, 64, 127))] + note(0, 6.6, 6.8) + [(7.3, cc(0, 121, 0))]
    probes.append({"kind": "level", "t": [6.9, 7.2], "db": 0.0, "what": "the pedal holds the note"})
    probes.append({"kind": "silent", "t": [7.4, 7.8], "what": "CC121 lifts the pedal"})
    # All Notes Off on a held note.
    ev += [(8.0, bytes((0x90, 69, 100))), (8.5, cc(0, 123, 0))]
    probes.append({"kind": "silent", "t": [8.6, 9.0], "what": "CC123 releases a held note"})
    # The sends: channel 1 sends 127 to the reverb and channel 2, also in
    # use, sends 0. The master takes their mean, so the note rings on; a
    # module where the last send wins would leave it dry.
    ev += [(9.1, setup(1))] + note(1, 9.2, 9.3)
    ev.append((9.6, cc(0, 91, 127) + cc(1, 91, 0)))
    ev += note(0, 9.7, 10.0)
    probes.append({"kind": "ringing", "t": [10.15, 10.45], "what": "the reverb rings after a send of 127"})
    ev.append((11.0, cc(0, 91, 0)))
    return ev, probes, ref


def sine_bank(path, audioinstruments):
    sys.path.insert(0, os.path.join(audioinstruments, "lib"))
    sys.path.insert(0, os.path.join(audioinstruments, "tests", "support"))
    import audioinstruments._rack._sf2 as G
    import sf2_builder

    period = 50  # 441 Hz at sf2_builder's 22050 Hz
    frames = [int(round(16000 * math.sin(2 * math.pi * i / period))) for i in range(period * 40)]
    b = sf2_builder.Bank("Sine")
    s = b.sample("sine", frames, 0, len(frames), pitch=69)
    inst = b.instrument("Sine", [[(G.GEN_SAMPLE_MODES, 1), (G.GEN_SAMPLE_ID, s)]])
    for program in range(128):
        b.preset("Sine %d" % program, 0, program, [[(G.GEN_INSTRUMENT, inst)]])
    b.preset("Sine Kit", 128, 0, [[(G.GEN_INSTRUMENT, inst)]])
    b.write(path)


def main(argv):
    folder = argv[0]
    ac = argv[argv.index("--audioinstruments") + 1]
    os.makedirs(folder, exist_ok=True)
    sine_bank(os.path.join(folder, "sine.sf2"), ac)
    ev, probes = bends()
    write_mid(os.path.join(folder, "bends.mid"), ev)
    with open(os.path.join(folder, "bends.json"), "w") as f:
        json.dump({"probes": probes}, f, indent=1)
    ev, probes, ref = resets()
    write_mid(os.path.join(folder, "resets.mid"), ev)
    with open(os.path.join(folder, "resets.json"), "w") as f:
        json.dump({"probes": probes, "reference": ref}, f, indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
