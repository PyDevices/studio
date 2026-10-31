"""Three classic-rock sketches for Ostinato, written as song files.

    python songs/sketches/make_sketches.py

writes lowrey_field.json, low_sun.json and strange_days_out.json beside this
script. Open one from Ostinato's menu and press play: each is in song mode,
so the arrangement runs on its own.

Times are ticks, 96 to a quarter note. The drums carry small, seeded pushes
and pulls in time and velocity, so the groove breathes the same way on
every run.
"""

import json
import os
import random

Q = 96            # quarter
E = Q // 2        # eighth
S = Q // 4        # sixteenth
BAR = 4 * Q

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = json.load(open(os.path.join(HERE, "..", "..", "assets", "catalog.json")))

# The drum notes the sketches play, on rhythm909: it has the full set (kick,
# rim, snare, toms, hats, crash and two rides) that the acoustic kit gave them.
KICK, STICK, SNARE, FLOOR, HAT, PEDAL, LOWTOM, OPEN, MIDTOM, HITOM, CRASH, RIDE, BELL = (
    36, 37, 38, 41, 42, 44, 45, 46, 47, 48, 49, 51, 59)


def preset(effect, patch):
    """A rack entry with the knobs of one of the effect's own presets."""
    vals = CATALOG["effects"][effect]["patch_values"][str(patch)]
    return {"name": effect, "bypass": False, "macros": {str(i): v for i, v in enumerate(vals)}}


class Pat:
    def __init__(self, bars, seed=0):
        self.bars = bars
        self.notes = []
        self.rng = random.Random(seed)

    def n(self, start, length, pitch, vel, push=0, wobble=0):
        """A note; ``push`` moves it late (or early, negative) by a few ticks,
        ``wobble`` adds up to that many ticks and velocity units of drift."""
        if wobble:
            push += self.rng.randint(-wobble, wobble)
            vel += self.rng.randint(-2 * wobble, 2 * wobble)
        start = max(0, min(self.bars * BAR - 1, start + push))
        self.notes.append([start, length, pitch, max(1, min(127, vel))])
        return self

    def chord(self, start, length, pitches, vel):
        for p in pitches:
            self.n(start, length, p, vel)
        return self

    def tile(self, times):
        """This pattern repeated ``times`` over, as one longer pattern."""
        out = Pat(self.bars * times)
        for k in range(times):
            for s, l, p, v in self.notes:
                out.notes.append([s + k * self.bars * BAR, l, p, v])
        return out

    def data(self):
        return {"bars": self.bars, "notes": sorted(self.notes)}


def track(tid, name, kind, instrument, patterns, patch=0, macros=None, volume=100, pan=64, rack=()):
    return {
        "id": tid, "name": name, "kind": kind, "instrument": instrument, "patch": patch,
        "macros": {str(k): v for k, v in (macros or {}).items()},
        "volume": volume, "pan": pan, "mute": False, "solo": False, "rack": list(rack),
        "patterns": [p.data() for p in patterns],
        "current": 0, "current_at": 0, "next": None,
    }


def song(name, tempo, swing, tracks, scenes):
    """``scenes`` is [(drums, bass, keys, lead, repeat)], pattern indexes, -1 for silence."""
    return {
        "version": 1, "name": name, "tempo": tempo, "swing": swing, "metronome": False,
        "quantise": True, "overdub": True, "loop_pattern": 0,
        "song": [{"patterns": {str(t["id"]): i for t, i in zip(tracks, sc[:4])}, "repeat": sc[4]}
                 for sc in scenes],
        "song_mode": True, "loop_scene": None,
        "master": {"volume": 100, "rack": []},
        "tracks": tracks, "bindings": {},
    }


# ---------------------------------------------------------------------------
# 1. Lowrey Field: a Who-style drive. An organ figure in 3+3+3+3+2+2 sixteenths
# over an F pedal, Riley's trick, that the band lands on top of.

def lowrey_field():
    pedal = 53  # F3
    bars = [(69, 72, 77), (67, 70, 75), (70, 74, 77), (69, 72, 77)]     # F, Eb/F, Bb/F, F
    chorus = [(70, 74, 77), (69, 72, 77), (67, 72, 76), (69, 72, 77)]   # Bb, F, C, F
    groups = (0, 3, 6, 9, 12, 14)

    def figure(chords, seed):
        p = Pat(len(chords), seed)
        for b, tones in enumerate(chords):
            k = 0
            for step in range(16):
                t = b * BAR + step * S
                if step in groups:
                    p.n(t, 18, pedal, 108, wobble=1)
                else:
                    p.n(t, 16, tones[k % 3], 74 + (8 if step % 4 == 2 else 0), wobble=1)
                    k += 1
        return p

    k_verse = figure(bars, 1).tile(2)            # 8 bars
    k_chorus = figure(chorus, 2).tile(2)         # 8 bars
    k_intro = figure(bars, 3)                    # 4 bars
    k_end = Pat(1).chord(0, BAR - 12, (53, 60, 65, 69, 72, 77), 104)

    def bass(roots, seed):
        p = Pat(len(roots), seed)
        for b, r in enumerate(roots):
            for i in range(8):
                p.n(b * BAR + i * E, 40, r + (12 if i == 7 and b % 2 else 0), 104 if i % 2 == 0 else 86, wobble=1)
        return p

    b_pedal = Pat(4)
    for b in range(4):
        b_pedal.n(b * BAR, BAR - 8, 41, 96)
    b_verse = bass([41, 39, 34, 41], 4).tile(2)
    b_chorus = bass([34, 41, 36, 41], 5).tile(2)
    b_end = Pat(1).n(0, BAR - 12, 29, 116)

    def groove(nbars, seed, chorus=False):
        p = Pat(nbars, seed)
        for b in range(nbars):
            o = b * BAR
            for i in range(8):
                if chorus and i == 7:
                    p.n(o + i * E, E, OPEN, 92, wobble=2)
                else:
                    p.n(o + i * E, 20, HAT, 96 if i % 2 == 0 else 70, wobble=2)
            p.n(o, 30, KICK, 112, wobble=1).n(o + 2 * Q + 0, 30, KICK, 104, wobble=1)
            p.n(o + Q + E, 30, KICK, 92, wobble=1)
            if chorus:
                p.n(o + 2 * Q + E, 30, KICK, 90, wobble=1)
            p.n(o + Q, 30, SNARE, 118, push=2, wobble=1).n(o + 3 * Q, 30, SNARE, 120, push=2, wobble=1)
            p.n(o + 3 * Q + 3 * S, 20, SNARE, 38, wobble=1)                       # the ghost before 1
            if b % 4 == 3:  # a fill into the next phrase, on beat 4
                p.notes = [x for x in p.notes if not (o + 3 * Q <= x[0] < o + BAR and x[2] in (HAT, OPEN, SNARE))]
                for i, drum in enumerate((SNARE, SNARE, MIDTOM, FLOOR)):
                    p.n(o + 3 * Q + i * S, 30, drum, 100 + 6 * i, wobble=1)
        p.n(0, Q, CRASH, 110)
        if chorus:
            p.n(4 * BAR, Q, CRASH, 104)
        return p

    d_in = Pat(4, 6)
    for i in range(8):   # snare and toms gather over the last bar
        d_in.n(3 * BAR + i * E // 2 + Q * 2, 30, (SNARE, SNARE, SNARE, SNARE, HITOM, MIDTOM, FLOOR, FLOOR)[i], 70 + 7 * i, wobble=1)
    d_verse = groove(8, 7)
    d_chorus = groove(8, 8, chorus=True)
    d_break = Pat(4, 9)
    for b in range(4):
        for i in range(16):
            d_break.n(b * BAR + i * S, 16, HAT, 40 + b * 12 + (14 if i % 4 == 0 else 0), wobble=2)
        for i in range(4):
            d_break.n(b * BAR + i * Q, 30, KICK, 100, wobble=1)
    for i in range(8):   # and a roll up into the last chorus
        d_break.n(3 * BAR + 2 * Q + i * S, 20, SNARE, 60 + 8 * i, wobble=1)
    d_end = Pat(1).n(0, BAR, CRASH, 120).n(0, 40, KICK, 120).n(0, 40, SNARE, 110)

    # The lead: an anthem you could sing from the cheap seats.
    l_hook = Pat(8, 10)
    for start, length, pitch in [
        (0, Q, 77), (Q, E, 74), (Q + E, E, 72), (2 * Q, 2 * Q, 74),
        (BAR, Q + E, 72), (BAR + Q + E, E, 69), (BAR + 2 * Q, 2 * Q, 72),
        (2 * BAR, E, 72), (2 * BAR + E, E, 74), (2 * BAR + Q, Q, 76), (2 * BAR + 2 * Q, 2 * Q, 79),
        (3 * BAR, BAR - 12, 77),
        (4 * BAR, Q, 77), (4 * BAR + Q, Q, 79), (4 * BAR + 2 * Q, Q, 77), (4 * BAR + 3 * Q, Q, 74),
        (5 * BAR, 2 * Q, 72), (5 * BAR + 2 * Q, Q, 69), (5 * BAR + 3 * Q, Q, 72),
        (6 * BAR, Q, 67), (6 * BAR + Q, Q, 72), (6 * BAR + 2 * Q, Q, 76), (6 * BAR + 3 * Q, Q, 74),
        (7 * BAR, BAR - 12, 77),
    ]:
        l_hook.n(start, length - 4, pitch, 100)
    l_break = Pat(4, 11)
    for b, p in enumerate((84, 82, 81, 79)):
        l_break.n(b * BAR, BAR - 12, p, 92)

    tracks = [
        track(1, "Drums", "drums", "rhythm909", [d_in, d_verse, d_chorus, d_break, d_end], patch=3, volume=70),
        track(2, "Bass", "melodic", "mono_synth", [b_pedal, b_verse, b_chorus, b_end], patch=1, volume=112),
        track(3, "Keys", "melodic", "tonewheel3", [k_intro, k_verse, k_chorus, k_end],
              macros={0: 70, 1: 127, 2: 96, 3: 44, 7: 26, 8: 127}, volume=110, pan=52,
              rack=[preset("Reverb", 5)]),
        track(4, "Lead", "melodic", "mono_synth", [l_hook, l_break], patch=4, macros={9: 18},
              volume=112, pan=76, rack=[preset("Overdrive", 4), preset("AnalogDelay", 0), preset("Reverb", 4)]),
    ]
    scenes = [
        (-1, -1, 0, -1, 2),   # the organ alone
        (0, 0, 0, -1, 1),     # the pedal, and the drums gathering
        (1, 1, 1, -1, 1),     # verse
        (2, 2, 2, 0, 1),      # chorus
        (1, 1, 1, -1, 1),     # verse
        (2, 2, 2, 0, 1),      # chorus
        (3, -1, 0, 1, 1),     # the break: four on the floor, the lead up high
        (2, 2, 2, 0, 2),      # last chorus, twice
        (4, 3, 3, -1, 1),     # the hit
    ]
    return song("Lowrey Field", 118.0, 0, tracks, scenes)


# ---------------------------------------------------------------------------
# 2. Low Sun: a Floyd slow burn in D Dorian. Strings, a patient kit, a lead
# that leaves room for its own echoes.

def low_sun():
    dm9 = (50, 57, 60, 64, 65)
    g_d = (50, 55, 59, 62)
    k_a = Pat(4).chord(0, 2 * BAR - 8, dm9, 84).chord(2 * BAR, 2 * BAR - 8, g_d, 84)
    k_b = Pat(4)
    for b, ch in enumerate([(46, 53, 57, 62), (48, 55, 60, 64), (50, 57, 62, 65), (45, 52, 55, 61)]):
        k_b.chord(b * BAR, BAR - 6, ch, 90)
    k_out = Pat(4).chord(0, 4 * BAR - 8, dm9, 70)

    b_a = Pat(4)
    for s, l, p in [(0, 3 * Q, 38), (3 * Q, Q, 33), (BAR, 2 * Q, 38), (BAR + 2 * Q, Q, 41), (BAR + 3 * Q, Q, 45),
                    (2 * BAR, 3 * Q, 43), (2 * BAR + 3 * Q, Q, 38), (3 * BAR, 2 * Q, 43), (3 * BAR + 2 * Q, Q, 45),
                    (3 * BAR + 3 * Q, Q, 48)]:
        b_a.n(s, l - 6, p, 100)
    b_b = Pat(4)
    for b, p in enumerate((34, 36, 38, 33)):
        b_b.n(b * BAR, BAR - 8, p, 104)
    b_out = Pat(4).n(0, 4 * BAR - 8, 38, 90)

    def kit(nbars, seed, ride=False, enter=False):
        p = Pat(nbars, seed)
        cym = RIDE if ride else HAT
        for b in range(nbars):
            o = b * BAR
            if enter and b < nbars - 1:
                for i in range(4):
                    p.n(o + i * Q, 20, cym, 52 + (8 if i == 0 else 0), wobble=2)
                continue
            for i in range(8):
                p.n(o + i * E, 20, cym, (86 if i % 2 == 0 else 58) - (6 if ride else 0), wobble=2)
            p.n(o, 40, KICK, 108, wobble=1).n(o + Q + E, 30, KICK, 76, wobble=1).n(o + 2 * Q, 40, KICK, 100, wobble=1)
            p.n(o + Q, 40, SNARE, 112, push=2, wobble=1).n(o + 3 * Q, 40, SNARE, 116, push=2, wobble=1)
            if b % 4 == 3:  # Mason's fill: slow, round, downhill
                p.notes = [x for x in p.notes if not (o + 2 * Q <= x[0] < o + BAR)]
                for t, drum, v in ((2 * Q, HITOM, 92), (2 * Q + E, MIDTOM, 96), (3 * Q, LOWTOM, 100),
                                   (3 * Q + E, FLOOR, 106), (3 * Q + 3 * S, FLOOR, 98)):
                    p.n(o + t, 40, drum, v, wobble=1)
        if ride:
            p.n(0, Q, CRASH, 104)
        return p

    d_enter = kit(8, 20, enter=True)
    d_a = kit(8, 21)
    d_b = kit(4, 22, ride=True)
    d_out = Pat(4, 23).n(0, 2 * BAR, CRASH, 96).n(0, 40, KICK, 104)

    l_a = Pat(8, 24)
    for s, l, p in [
        (Q, E, 69), (Q + E, E, 72), (2 * Q, 3 * Q, 74),
        (BAR + Q, E, 76), (BAR + Q + E, E, 74), (BAR + 2 * Q, 2 * Q, 72),
        (2 * BAR + Q, Q, 71), (2 * BAR + 2 * Q, Q, 74), (2 * BAR + 3 * Q, Q, 79),
        (3 * BAR, 3 * Q, 77), (3 * BAR + 3 * Q, Q, 76),
        (4 * BAR, BAR, 74),
        (5 * BAR + 2 * Q, E, 69), (5 * BAR + 2 * Q + E, E, 72), (5 * BAR + 3 * Q, Q, 74),
        (6 * BAR, 2 * Q, 76), (6 * BAR + 2 * Q, Q, 74), (6 * BAR + 3 * Q, Q, 71),
        (7 * BAR, BAR - 24, 67),
    ]:
        l_a.n(s, l - 6, p, 96)
    l_b = Pat(4, 25)
    for s, l, p in [(0, Q, 77), (Q, 3 * Q, 81), (BAR, 2 * Q, 79), (BAR + 2 * Q, 2 * Q, 76),
                    (2 * BAR, Q, 77), (2 * BAR + Q, Q, 76), (2 * BAR + 2 * Q, 2 * Q, 74), (3 * BAR, BAR - 24, 73)]:
        l_b.n(s, l - 6, p, 104)
    l_out = Pat(4, 26).n(Q, 3 * BAR, 74, 88)

    tracks = [
        track(1, "Drums", "drums", "rhythm909", [d_enter, d_a, d_b, d_out], patch=1, volume=72),
        track(2, "Bass", "melodic", "bass_pedals", [b_a, b_b, b_out], macros={9: 22}, volume=100),
        track(3, "Keys", "melodic", "string_ensemble", [k_a, k_b, k_out], macros={0: 40}, volume=80,
              rack=[preset("Chorus", 3), preset("Reverb", 6)]),
        track(4, "Lead", "melodic", "mono_synth", [l_a, l_b, l_out], patch=4, macros={9: 34},
              volume=112, pan=70, rack=[preset("TapeDelay", 1), preset("Reverb", 9)]),
    ]
    scenes = [
        (-1, -1, 0, -1, 1),   # strings alone
        (0, 0, 0, 0, 1),      # bass and lead in, the kit counting time
        (1, 0, 0, 0, 1),      # the full band
        (2, 1, 1, 1, 2),      # the lift
        (1, 0, 0, 0, 1),      # home
        (3, 2, 2, 2, 1),      # the sun goes down
    ]
    return song("Low Sun", 64.0, 0, tracks, scenes)


# ---------------------------------------------------------------------------
# 3. Strange Days Out: the Doors' piano bass and Vox organ, a Copeland kit.
# Am, G, F, E; a one-drop verse, a rock chorus, a bossa bridge.

def strange_days_out():
    roots = (45, 43, 41, 40)
    fifths = (52, 50, 48, 47)
    approach = (44, 42, 39, 44)

    def riff(seed):
        p = Pat(4, seed)
        for b in range(4):
            o, r = b * BAR, roots[b]
            p.n(o, 64, r, 110, wobble=1).n(o + 3 * S, 18, r, 78, wobble=1)
            p.n(o + Q + E, 40, fifths[b], 94, wobble=1).n(o + 2 * Q, 88, r + 12, 104, wobble=1)
            p.n(o + 3 * Q + E, 40, approach[b], 88, wobble=1)
        return p

    b_riff = riff(30).tile(2)
    b_intro = riff(39)
    b_end = Pat(1).n(0, BAR - 12, 33, 112).n(0, BAR - 12, 45, 100)

    chords = [(57, 60, 64), (55, 59, 62), (53, 57, 60), (52, 56, 59)]
    k_skank = Pat(4, 31)
    for b, ch in enumerate(chords):
        for i in range(4):
            k_skank.chord(b * BAR + i * Q + E, 20, ch, 84 + (6 if i % 2 else 0))
    k_skank = k_skank.tile(2)
    k_hold = Pat(4, 32)
    for b, ch in enumerate(chords):
        k_hold.chord(b * BAR, (2 * Q if b == 3 else BAR) - 6, ch, 88)
    for i, p in enumerate((76, 74, 72, 71, 69, 68, 65, 64)):   # Manzarek's run down
        k_hold.n(3 * BAR + 2 * Q + i * S, S - 2, p, 96)
    k_hold = k_hold.tile(2)

    def hats(p, o, open_lift):
        for i in range(16):
            if open_lift and i == 14:
                p.n(o + i * S, E, OPEN, 86, wobble=1)
                continue
            if open_lift and i == 15:
                continue
            v = 96 if i % 4 == 0 else 74 if i % 2 == 0 else 48
            p.n(o + i * S, 14, HAT, v, push=-1 if i % 2 else 0, wobble=2)

    d_intro = Pat(4, 33)
    for b in range(4):
        hats(d_intro, b * BAR, b == 3)
    d_drop = Pat(8, 34)       # the one-drop verse
    for b in range(8):
        o = b * BAR
        hats(d_drop, o, b % 2 == 1)
        d_drop.n(o, 30, KICK, 72, wobble=1)
        d_drop.n(o + 2 * Q, 30, KICK, 110, wobble=1).n(o + 2 * Q, 30, STICK, 108, wobble=1)
        if b % 4 == 3:
            d_drop.n(o + 3 * Q + E, 20, SNARE, 92, wobble=1).n(o + 3 * Q + 3 * S, 20, SNARE, 104, wobble=1)
    d_rock = Pat(8, 35)
    for b in range(8):
        o = b * BAR
        hats(d_rock, o, b % 2 == 1)
        d_rock.n(o, 30, KICK, 112, wobble=1).n(o + Q + E, 30, KICK, 96, wobble=1).n(o + 2 * Q, 30, KICK, 108, wobble=1)
        d_rock.n(o + Q, 30, SNARE, 122, push=-1, wobble=1).n(o + 3 * Q, 30, SNARE, 122, push=-1, wobble=1)
        d_rock.n(o + Q + 3 * S, 16, SNARE, 36, wobble=1)
        if b % 4 == 3:   # snare and hat answering each other, then the floor tom
            d_rock.notes = [x for x in d_rock.notes if not (o + 3 * Q <= x[0] < o + BAR)]
            for i, drum in enumerate((SNARE, HAT, SNARE, FLOOR)):
                d_rock.n(o + 3 * Q + i * S, 20, drum, (110, 70, 116, 112)[i], wobble=1)
    d_rock.n(0, Q, CRASH, 112).n(4 * BAR, Q, CRASH, 104)
    d_bossa = Pat(8, 36)      # Densmore's bossa nova under "Break On Through"
    for b in range(8):
        o = b * BAR
        for i in range(8):
            d_bossa.n(o + i * E, 20, BELL if i == 0 and b % 2 == 0 else RIDE, 84 if i % 2 == 0 else 62, wobble=2)
        for step in (0, 3, 6, 10, 13):
            d_bossa.n(o + step * S, 20, STICK, 96 if step in (0, 6) else 84, wobble=1)
        d_bossa.n(o, 30, KICK, 100, wobble=1).n(o + Q + 3 * S, 20, KICK, 80, wobble=1)
        d_bossa.n(o + 2 * Q, 30, KICK, 100, wobble=1).n(o + 3 * Q + 3 * S, 20, KICK, 80, wobble=1)
    d_end = Pat(1).n(0, BAR, CRASH, 120).n(0, 40, KICK, 120).n(0, 40, SNARE, 116)

    l_hook = Pat(4, 37)
    for s, l, p in [(0, E, 69), (E, E, 72), (Q, Q, 76), (2 * Q, E, 74), (2 * Q + E, E, 72), (3 * Q, Q, 71),
                    (BAR, 2 * Q, 74), (BAR + 3 * Q, E, 71), (BAR + 3 * Q + E, E, 74),
                    (2 * BAR, Q, 72), (2 * BAR + Q, Q, 69), (2 * BAR + 2 * Q, 2 * Q, 77),
                    (3 * BAR, 3 * Q, 76), (3 * BAR + 3 * Q, E, 68), (3 * BAR + 3 * Q + E, E, 71)]:
        l_hook.n(s, l - 6, p, 100)
    l_hook = l_hook.tile(2)
    l_bridge = Pat(8, 38)
    for s, l, p in [(0, 2 * BAR, 76), (2 * BAR, BAR, 77), (3 * BAR, BAR, 76),
                    (4 * BAR, Q, 81), (4 * BAR + Q, Q, 79), (4 * BAR + 2 * Q, 2 * Q, 77),
                    (5 * BAR, BAR, 76), (6 * BAR, 2 * Q, 74), (6 * BAR + 2 * Q, 2 * Q, 72), (7 * BAR, BAR - 24, 68)]:
        l_bridge.n(s, l - 6, p, 92)

    tracks = [
        track(1, "Drums", "drums", "rhythm909", [d_intro, d_drop, d_rock, d_bossa, d_end], patch=0, volume=72),
        track(2, "Bass", "melodic", "electric_piano", [b_riff, b_intro, b_end], macros={6: 0}, volume=127, pan=60),
        track(3, "Keys", "melodic", "combo_organ", [k_skank, k_hold], volume=120, pan=44,
              rack=[preset("Reverb", 4)]),
        track(4, "Lead", "melodic", "duo20", [l_hook, l_bridge], volume=120, pan=84,
              rack=[preset("TapeDelay", 2), preset("Reverb", 5)]),
    ]
    scenes = [
        (0, 1, -1, -1, 1),    # piano bass and hats
        (1, 0, 0, -1, 1),     # one-drop verse
        (2, 0, 1, 0, 1),      # chorus
        (1, 0, 0, -1, 1),     # verse
        (2, 0, 1, 0, 1),      # chorus
        (3, 0, 1, 1, 1),      # the bossa bridge
        (2, 0, 1, 0, 1),      # chorus
        (4, 2, -1, -1, 1),    # the hit
    ]
    return song("Strange Days Out", 124.0, 0, tracks, scenes)


if __name__ == "__main__":
    for fn in (lowrey_field, low_sun, strange_days_out):
        s = fn()
        name = s["name"].lower().replace(" ", "_") + ".json"
        with open(os.path.join(HERE, name), "w") as f:
            json.dump(s, f, separators=(",", ":"))
        print(name, sum(len(p["notes"]) for t in s["tracks"] for p in t["patterns"]), "notes")
