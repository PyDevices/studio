# SPDX-FileCopyrightText: 2026 PyDevices
#
# SPDX-License-Identifier: MIT
"""Writes a small General MIDI bank for Vamp's checks, so they run without a
real SoundFont.

    python tests/vamp_bank.py OUT.sf2

A kit (bank 128) with a kick, a snare, a closed hi-hat and an open one, the
two hats in one exclusive class, and a looped sawtooth on program 0. The
samples are noise and a saw from a fixed seed, so every run writes the same
file. It uses audioinstruments' SoundFont writer (``tests/support/sf2_builder.py``
in an audioinstruments checkout: ``$AUDIOINSTRUMENTS_DIR/tests/support``),
which needs ``audioinstruments`` importable.
"""

import math
import os
import sys


def _support():
    here = os.path.dirname(os.path.abspath(__file__))
    explicit = os.environ.get("AUDIOINSTRUMENTS_DIR")
    candidates = [os.path.join(explicit, "tests", "support")] if explicit else []
    parent = here
    for _ in range(4):
        parent = os.path.dirname(parent)
        candidates.append(os.path.join(parent, "audioinstruments", "tests", "support"))
    for c in candidates:
        if os.path.isfile(os.path.join(c, "sf2_builder.py")):
            return os.path.abspath(c)
    raise SystemExit("no audioinstruments checkout with tests/support/sf2_builder.py (set AUDIOINSTRUMENTS_DIR)")


def _noise(seconds, decay, seed, rate):
    n = int(seconds * rate)
    x = seed
    out = []
    for i in range(n):
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF
        v = (x >> 15) / 65536.0 * 2.0 - 1.0
        out.append(int(20000 * v * math.exp(-i / (decay * rate))))
    return out


def _thump(seconds, rate):
    n = int(seconds * rate)
    return [int(24000 * math.sin(2 * math.pi * 55 * i / rate) * math.exp(-i / (0.08 * rate))) for i in range(n)]


def bank():
    sys.path.insert(0, _support())
    import sf2_builder as sb

    G = sb.sf2
    rate = sb.SAMPLE_RATE
    b = sb.Bank("Vamp Check Bank")
    kick = b.sample("kick", _thump(0.25, rate), pitch=36)
    snare = b.sample("snare", _noise(0.2, 0.05, 7, rate), pitch=38)
    closed = b.sample("hat_closed", _noise(0.08, 0.02, 11, rate), pitch=42)
    # The open hat loops its last tenth of a second at full level: it rings
    # until something stops it, which is what the choke check needs.
    ring = _noise(0.5, 10.0, 13, rate)
    opened = b.sample("hat_open", ring, len(ring) - rate // 10, len(ring), pitch=46)
    period = 84
    saw = [int((i % period) * 600 - 25000) for i in range(period * 40)]
    lead = b.sample("saw", saw, 0, len(saw), pitch=60)

    kit = b.instrument("Kit", [
        [(G.GEN_KEY_RANGE, (36, 36)), (G.GEN_SAMPLE_ID, kick)],
        [(G.GEN_KEY_RANGE, (38, 38)), (G.GEN_SAMPLE_ID, snare)],
        [(G.GEN_KEY_RANGE, (42, 42)), (G.GEN_EXCLUSIVE_CLASS, 1), (G.GEN_SAMPLE_ID, closed)],
        [(G.GEN_KEY_RANGE, (46, 46)), (G.GEN_EXCLUSIVE_CLASS, 1), (G.GEN_SAMPLE_MODES, 1),
         (G.GEN_SAMPLE_ID, opened)],
    ])
    synth = b.instrument("Saw", [
        [(G.GEN_RELEASE_VOL_ENV, -2400), (G.GEN_SAMPLE_MODES, 1), (G.GEN_SAMPLE_ID, lead)],
    ])
    b.preset("Saw", 0, 0, [[(G.GEN_INSTRUMENT, synth)]])
    b.preset("Standard Kit", 128, 0, [[(G.GEN_INSTRUMENT, kit)]])
    return b


def write(path):
    return bank().write(path)


if __name__ == "__main__":
    print(write(sys.argv[1]))
