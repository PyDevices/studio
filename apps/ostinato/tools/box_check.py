"""Checks the box engine (app/box/boxengine.py) offline, on MicroPython.

Pulls the engine's output itself, so it owns the clock, and listens to what
comes out: notes start on their frame however rarely the sequencer runs, an
edit stops the notes it removed, mute silences, the master rack is in the
path, and the keyboard plays at once.

    micropython -X heapsize=96M tools/box_check.py BANK.sf2 [--plant FAULT]

MICROPYPATH needs the audio libraries (audioinstruments, audioeffects),
app/box and app/shared. ``--plant`` breaks the engine one way (nodelay,
nocancel, nomute, norack, nolive, nochoke) and the check must then fail.
"""

import sys
import time

import audiocore

import boxengine as be
import model

SR = be.SR
TEMPO = 120.0                      # a quarter is 24,000 frames
HAT = 42                           # GM closed hi-hat: short, a clean onset
QUARTER = model.PPQ
START = 4800                       # play starts here, as the box starts it a margin ahead


def probe_song(beats=8):
    p = model.new_project()
    p["tempo"] = TEMPO
    t = model.track("drums", "soundfont_drums", "Probe")
    t["patterns"][0]["bars"] = beats // 4
    t["patterns"][0]["notes"] = [[i * QUARTER, QUARTER // 4, HAT, 110] for i in range(beats)]
    p["tracks"] = [t]
    return p


class Rig:
    """The engine and a buffer of everything it played."""

    def __init__(self, bank, lookahead=SR // 2):
        self.frames = 0
        self.out = []              # left channel, int
        self.eng = be.BoxEngine(bank, None, self.clock, lookahead=lookahead)
        self.pull(be.BLOCK * 8)        # the output has been running a while, as on the box
        self.out = []
        self.frames = 0

    def clock(self):
        return self.frames

    def pull(self, frames, every=1, at=None):
        """Render ``frames``; the sequencer runs every ``every`` blocks.
        ``at`` is {frame: fn} run when the clock passes the frame."""
        end = self.frames + frames
        n = 0
        while self.frames < end:
            if at:
                for f in list(at):
                    if self.frames >= f:
                        at.pop(f)()
            if n % every == 0:
                self.eng.sequence()
            n += 1
            _r, b = audiocore.get_buffer(self.eng.output)
            mv = memoryview(b)
            count = len(mv) // 4
            for i in range(count):
                v = mv[4 * i] | (mv[4 * i + 1] << 8)
                self.out.append(v - 65536 if v > 32767 else v)
            self.frames += count

    def onsets(self, threshold=300, quiet=2400):
        """Frames where sound starts after at least ``quiet`` frames of silence."""
        found = []
        last_loud = -quiet - 1
        for i, v in enumerate(self.out):
            if v > threshold or v < -threshold:
                if i - last_loud > quiet:
                    found.append(i)
                last_loud = i
        return found

    def energy(self, a, b):
        s = 0
        for v in self.out[a:b]:
            s += v * v
        return s / max(1, b - a)


FAILS = []


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        FAILS.append(what)


def plant(name):
    if name == "nodelay":
        def resolve(self, kw, at, now):
            self.on_time += 1
        be.TimedVoices.resolve = resolve
    elif name == "nocancel":
        be.BoxEngine.resequence = lambda self: None
    elif name == "nomute":
        be.BoxEngine.level_of = lambda self, track: min(1.0, track.get("volume", 100) / 127.0)
    elif name == "norack":
        be.BoxEngine.sync_master = lambda self: None
    elif name == "nolive":
        be.BoxEngine.live = lambda self, s, d1, d2: None
    elif name == "nochoke":
        be.Track.defer_chokes = lambda self: None
    elif name:
        raise SystemExit("no such fault: " + name)


def onset_offsets(rig, beats):
    want = [START + i * SR * 60 // int(TEMPO) for i in range(beats)]
    got = rig.onsets()
    offs = []
    for w in want:
        near = [g - w for g in got if -SR // 4 < g - w < SR // 4]
        offs.append(near[0] if near else None)
    return offs, got


def main(argv):
    bank = be.sf.open_bank(argv[1])     # once: every rig shares it
    fault = argv[argv.index("--plant") + 1] if "--plant" in argv else None
    plant(fault)
    beats = 8

    # 1. On time, with the sequencer running every block and every 40 (213 ms,
    # a garbage collection's worth): the same frames either way.
    results = {}
    for every in (1, 40):
        rig = Rig(bank)
        rig.eng.apply({"op": "replace", "path": [], "value": probe_song(beats)})
        rig.eng.play(START)
        rig.pull(beats * SR // 2 + SR // 4, every=every)
        offs, got = onset_offsets(rig, beats)
        results[every] = offs
        late = rig.eng.pool.late
        steady = None not in offs and max(offs) - min(offs) <= 2 and 0 <= min(offs) < 256
        check(steady and late == 0,
              "every hit lands on its frame, sequencer every %d blocks (offsets %s, late %d)" % (every, offs, late))
    check(results[1] == results[40], "the stalled run starts every note on the same frame")

    # 2. A stall longer than the lookahead is counted, not hidden.
    rig = Rig(bank, lookahead=SR // 8)
    rig.eng.apply({"op": "replace", "path": [], "value": probe_song(beats)})
    rig.eng.play(START)
    rig.pull(beats * SR // 2, every=60)
    check(rig.eng.pool.late > 0, "a 320 ms stall past a 125 ms lookahead shows as late notes (%d)" % rig.eng.pool.late)

    # 3. An edit: at 1.1 s the pattern empties; nothing sounds after the guard.
    rig = Rig(bank)
    song = probe_song(beats)
    rig.eng.apply({"op": "replace", "path": [], "value": song})
    rig.eng.play(START)
    edit_at = START + SR + SR // 10
    tid = song["tracks"][0]["id"]

    def empty():
        rig.eng.apply({"op": "set", "path": model.track_path(tid, "patterns", 0, "notes"), "value": []})

    rig.pull(beats * SR // 2, at={edit_at: empty})
    got = rig.onsets()
    before = [g for g in got if g < edit_at]
    after = [g for g in got if g >= edit_at + be.GUARD + be.BLOCK]
    check(len(before) == 3 and not after,
          "an edit at %d stops the notes it removed (before %s, after %s)" % (edit_at, before, after))

    # 4. Mute: at 1.1 s the track mutes, and the next hit is silent.
    rig = Rig(bank)
    song = probe_song(beats)
    rig.eng.apply({"op": "replace", "path": [], "value": song})
    rig.eng.play(START)

    def mute():
        rig.eng.apply({"op": "set", "path": model.track_path(tid2, "mute"), "value": True})

    tid2 = song["tracks"][0]["id"]
    rig.pull(beats * SR // 2, at={edit_at: mute})
    loud = rig.energy(START, START + SR // 8)
    quiet = rig.energy(START + 3 * SR // 2, START + 3 * SR // 2 + SR // 8)
    check(loud > 1000 and quiet < loud / 1000, "mute silences the track (energy %d, then %d)" % (loud, quiet))

    # 5. The master rack is in the path: a reverb fills the gaps between hits.
    gaps = {}
    for rack in ([], [{"name": "Reverb", "bypass": False, "macros": {}}]):
        rig = Rig(bank)
        song = probe_song(beats)
        song["master"]["rack"] = rack
        rig.eng.apply({"op": "replace", "path": [], "value": song})
        rig.eng.play(START)
        rig.pull(SR * 2)
        gaps[len(rack)] = rig.energy(START + SR // 4, START + SR // 2)
    check(gaps[1] > 10 * gaps[0] + 100, "a master reverb fills the gap after a hit (%d dry, %d wet)" % (gaps[0], gaps[1]))

    # 6. The keyboard: a pad hit sounds in the next block, the song stopped.
    rig = Rig(bank)
    rig.eng.apply({"op": "replace", "path": [], "value": probe_song(beats)})
    rig.pull(be.BLOCK * 4)
    hit = rig.frames
    t = time.ticks_us()
    rig.eng.live(0x99, be.PAD_BASE, 110)
    us = time.ticks_diff(time.ticks_us(), t)
    rig.pull(SR // 4)
    got = [g - hit for g in rig.onsets() if g >= hit]
    check(got and got[0] < be.BLOCK * 2, "a pad hit sounds within two blocks (%s frames, %d us in Python)" % (got[:1], us))

    # 7. A closed hat chokes the open one when it plays, not when it's
    # sequenced half a second ahead.
    rig = Rig(bank)
    song = probe_song(4)
    song["tracks"][0]["patterns"][0]["notes"] = [[0, QUARTER, 46, 110], [QUARTER // 2, QUARTER // 4, 42, 110]]
    rig.eng.apply({"op": "replace", "path": [], "value": song})
    rig.eng.play(START)
    rig.pull(SR)
    closed = START + SR // 4
    ringing = rig.energy(closed - SR // 20, closed)
    after = rig.energy(closed + SR // 10, closed + SR // 5)
    check(ringing > 1000 and after < ringing / 20,
          "the open hat rings until the closed hat's frame, then stops (energy %d, then %d)" % (ringing, after))

    print("box check:", "FAILED " + ", ".join(FAILS) if FAILS else "all passed", "(plant: %s)" % fault)
    if FAILS:
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv)
