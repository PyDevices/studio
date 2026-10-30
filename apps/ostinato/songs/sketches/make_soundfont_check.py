"""A short song on the SoundFont instruments, written as a song file.

    python songs/sketches/make_soundfont_check.py

writes soundfont_check.json beside this script: eight bars in A minor with
the Standard kit on Drums, Fingered Bass on Bass, the piano on Keys and a
string ensemble on Lead, every one of them a General MIDI program from the
bank. Open it from Ostinato's menu and press play; it's in song mode. With
no `.sf2` on the machine the four tracks stay silent and the status line
says so.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from make_sketches import BAR, CRASH, E, HAT, KICK, OPEN, Q, SNARE, Pat, song, track  # noqa: E402

PIANO, FINGERED_BASS, STRINGS = 0, 33, 48    # General MIDI programs
STANDARD_KIT = 0                             # the bank's first kit

# Am, F, C, G: one chord a bar.
CHORDS = [(57, 60, 64), (53, 57, 60), (48, 52, 55), (55, 59, 62)]
ROOTS = [45, 41, 36, 43]


def drums():
    p = Pat(4, seed=7)
    for bar in range(4):
        at = bar * BAR
        p.n(at, E, KICK, 110).n(at + 2 * Q, E, KICK, 100)
        p.n(at + Q, E, SNARE, 104, wobble=2).n(at + 3 * Q, E, SNARE, 108, wobble=2)
        for k in range(8):
            p.n(at + k * E, E // 2, OPEN if k == 7 else HAT, 70 if k % 2 else 84, wobble=1)
    p.n(0, Q, CRASH, 100)
    return p


def bass():
    p = Pat(4)
    for bar, root in enumerate(ROOTS):
        at = bar * BAR
        for k in range(8):
            pitch = root + (7 if k in (3, 7) else 0)
            p.n(at + k * E, E - 8, pitch, 96 if k % 2 == 0 else 80)
    return p


def keys():
    p = Pat(4)
    for bar, chord in enumerate(CHORDS):
        at = bar * BAR
        p.chord(at, Q + E, chord, 84)
        p.chord(at + 2 * Q, Q + E, chord, 72)
        p.chord(at + 3 * Q + E, E, chord, 64)
    return p


def strings():
    p = Pat(4)
    for bar, chord in enumerate(CHORDS):
        p.chord(bar * BAR, BAR - 4, [n + 12 for n in chord], 76)
    return p


def soundfont_check():
    tracks = [
        track(1, "Drums", "drums", "soundfont_drums", [drums()], patch=STANDARD_KIT, volume=96),
        track(2, "Bass", "melodic", "soundfont", [bass()], patch=FINGERED_BASS, volume=92),
        track(3, "Keys", "melodic", "soundfont", [keys()], patch=PIANO, volume=84),
        track(4, "Lead", "melodic", "soundfont", [strings()], patch=STRINGS, volume=72),
    ]
    return song("SoundFont Check", 100.0, 0, tracks, [(0, 0, 0, 0, 2)])


if __name__ == "__main__":
    s = soundfont_check()
    with open(os.path.join(HERE, "soundfont_check.json"), "w") as f:
        json.dump(s, f, separators=(",", ":"))
    print("soundfont_check.json", sum(len(p["notes"]) for t in s["tracks"] for p in t["patterns"]), "notes")
