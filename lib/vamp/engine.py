"""Vamp's sound: every SoundFont track on one sampler, sequenced ahead.

This is a groovebox's engine on a board (an ESP32-P4) and on desktop
MicroPython, one process with no workers. Ostinato's engine
(apps/ostinato/app/engine/engine.py) renders each track in Python, a block at
a time; here nothing in Python touches the audio. The tracks share one ``soundfont.Voices``
pool, the pool goes through the master rack and a level, and the audio pump
renders all of it on the other core.

Timing. The pump's frame counter (``audiopump.now()``) is the clock. The song's
notes are sequenced ``lookahead`` frames ahead and handed to the sampler at
once, each with an envelope delay that holds it silent until its frame, so a
note lands on its sample however late Python gets round to the next pass: a
garbage collection, an edit, a file read. Each pass's notes leave together,
the pump parked at a block boundary for them, and the delay is worked out
there, from the frame the pump will render next. An edit cancels the notes not
yet started (a release in the delay stage silences a voice at once) and
sequences them again. Note-offs are Python's: they go out when their frame
comes, so a pause makes a note ring a little long, never start late.

Mixing. A track's volume, mute and solo are its instrument's Volume macro,
its pan the Pan macro: the pool has no per-track bus. Effects are on the
master only.

Runs on MicroPython (unix, Windows, ESP32-P4) and CPython. ``clock`` and
``park`` are given, so a check can pull blocks itself and own the clock.
"""

import gc
import time

import audioeffects       # here, not at the first rack: its first import takes a second on the P4
import audioinstruments
import audioinstruments._rack.soundfont as sf
import audiomixer

from songlink import model

SR = 48000
BLOCK = 256
VOICES = 64
GAIN = 0.35               # the pool's gain: four parts summed with headroom
GUARD = SR // 10          # an edit keeps the notes due within 100 ms: an edit pass takes up to that on the P4
NOTE_IDS = 1 << 20
DELAYS = ("delay", "mod_delay", "vibrato_delay", "lfo_delay")
PAD_BASE = 36             # the DMK-25's pad 1
GM_PADS = [36, 38, 42, 46, 39, 45, 49, 51]


def ticks_ms():
    try:
        return time.ticks_ms()
    except AttributeError:
        return int(time.monotonic() * 1000)


def ticks_diff(a, b):
    try:
        return time.ticks_diff(a, b)
    except AttributeError:
        return a - b


def ticks_us():
    try:
        return time.ticks_us()
    except AttributeError:
        return int(time.perf_counter() * 1000000)


class _Timed(sf._Queued):
    """A pool voice whose ``play`` lands on the frame the engine names
    (``pool.at``): the envelope's delays grow by the frames still to go."""

    def play(self, buffer, **kw):
        pool = self._pool
        if pool.at is not None:
            if pool._queue is None:
                pool.resolve(kw, pool.at, pool.clock())
            else:
                kw["_at"] = pool.at
        sf._Queued.play(self, buffer, **kw)


class TimedVoices(sf.Voices):
    """``soundfont.Voices`` whose notes can start on a frame ahead of now."""

    def __init__(self, sample_rate, channel_count, voice_count, clock, park=None, unpark=None, gain=GAIN):
        sf.Voices.__init__(self, sample_rate, channel_count, voice_count=voice_count, gain=gain)
        self.voice = [_Timed(self, i) for i in range(self.sampler.voice_count)]
        self.clock = clock
        self._park_fn = park
        self._unpark_fn = unpark
        self.at = None
        self.late = 0             # notes that reached the sampler after their frame
        self.late_max = 0         # frames, the worst of them
        self.on_time = 0

    def resolve(self, kw, at, now):
        ahead = at - now
        if ahead < 0:
            self.late += 1
            if -ahead > self.late_max:
                self.late_max = -ahead
            return
        self.on_time += 1
        s = ahead / self.sample_rate
        kw["delay"] = (kw.get("delay") or 0.0) + s
        for k in DELAYS[1:]:
            if k in kw:
                kw[k] = (kw[k] or 0.0) + s

    def flush(self):
        queue = self._queue
        if not queue:
            return
        self._queue = []
        parked = self._park_fn is not None and self._park_fn()
        first = None
        try:
            now = self.clock()
            for fn, args, kw in queue:
                if kw and "_at" in kw:
                    self.resolve(kw, kw.pop("_at"), now)
                try:
                    if kw:
                        fn(*args, **kw)
                    else:
                        fn(*args)
                except Exception as e:  # noqa: BLE001  (the rest still go, then it raises)
                    if first is None:
                        first = e
        finally:
            if parked:
                self._unpark_fn()
            self._pending.clear()
        if first is not None:
            raise first


class Track:
    """One track's instrument on the shared pool."""

    def __init__(self, box, track):
        self.box = box
        self.id = track["id"]
        self.name = None
        self.inst = None
        self.patch = None
        self.macros = {}
        self.keys = None          # (pitch, velocity) pairs preloaded
        self.failed = None
        self.sync(track)

    def sync(self, track):
        box = self.box
        if track["instrument"] != self.name:
            if self.inst is not None:
                self.inst.deinit()
            self.inst = None
            self.failed = None
            self.name = track["instrument"]
            self.patch = None
            self.macros = {}
            self.keys = None
            if self.name in ("soundfont", "soundfont_drums"):
                try:
                    self.inst = audioinstruments.create(self.name, SR, bank=box.bank, voice_pool=box.pool)
                except Exception as e:  # noqa: BLE001
                    self.failed = "%s can't play %s: %s" % (track.get("name", "A track"), self.name, e)
            else:
                self.failed = "%s: the box plays SoundFont tracks only, not %s" % (track.get("name", "A track"),
                                                                                  self.name)
            if self.inst is not None:
                self.defer_chokes()
            if self.failed:
                print("box:", self.failed)
                box.post({"t": "failed", "track": self.id, "why": self.failed})
        if self.inst is None:
            return
        patch = int(track.get("patch") or 0)
        if patch != self.patch:
            self.inst.program_change(patch)
            self.patch = patch
            self.keys = None
        if box.notes_changed or self.keys is None:
            self.preload(track)
        # The song's macros, then volume and pan over them: the pool has no
        # per-track bus, so a track's level is its instrument's Volume.
        want = {}
        for k, v in track.get("macros", {}).items():
            want[int(k)] = v
        user = want.get(0, sf.DEFAULT_MACROS[0])
        want[0] = max(0.0, min(127.0, user * box.level_of(track)))
        want[sf.PAN_MACRO] = max(0, min(127, track.get("pan", 64)))
        for k, v in want.items():
            if self.macros.get(k) != v:
                self.inst.set_macro(k, v)
                self.macros[k] = v

    def defer_chokes(self):
        """A note in an exclusive class (the closed hat) stops the others in
        its class (the open hat). Sequenced ahead, it must stop them when it
        starts, not when it's handed over: the engine does that at its frame."""
        player = self.inst._player
        box = self.box
        now = player._choke

        def choke(exclusive_class):
            if box.pool.at is None:
                now(exclusive_class)
            else:
                box.chokes.append((box.pool.at, player, exclusive_class, box.pool.serial))

        player._choke = choke

    def preload(self, track):
        """Read every sample this track's notes reach into the arena now, not
        at the note, and say what that cost."""
        player = self.inst._player
        bank = player.bank
        wanted = set()
        for pat in track.get("patterns", []):
            for _start, _len, pitch, vel in pat["notes"]:
                wanted.add((int(pitch), int(vel)))
        if self.keys is not None and not (wanted - self.keys):
            return
        t0 = ticks_ms()
        reads = bank.reads
        sizes = {}
        font = bank.font
        for pitch, vel in sorted(wanted):
            try:
                regions = font.regions(player.bank_number, player.program, pitch, vel)
            except KeyError:
                continue
            for region in regions:
                sample = region.sample
                sizes[sample.index] = (sample.end - sample.start) * 2
                bank.buffer(region)
        self.keys = wanted
        self.box.post({"t": "preloaded", "track": self.id, "samples": len(sizes), "bytes": sum(sizes.values()),
                       "read": bank.reads - reads, "ms": ticks_diff(ticks_ms(), t0),
                       "arena": bank.arena_bytes, "cached": bank.cached, "cache": bank.cache_bytes})

    def close(self):
        if self.inst is not None:
            self.inst.deinit()
            self.inst = None


class BoxEngine:
    """The song, the sequencer and the sound.

    ``out`` plays a sample (``audio_out.play(sample, loop=True)``). ``clock()``
    is the frame the pump renders next; ``park()``/``unpark()`` hold it at a
    block boundary (audiopump's, or None). ``post(dict)`` is what the engine
    says back: preloads, failures, MIDI seen.
    """

    def __init__(self, bank, out, clock, park=None, unpark=None, post=None, lookahead=None,
                 voices=VOICES, cache_bytes=None):

        self.post = post or (lambda msg: None)
        self.out = out
        self.clock = clock
        self.bank = sf.open_bank(bank) if isinstance(bank, str) else bank
        if cache_bytes:
            self.bank.cache_bytes = cache_bytes
        self.pool = TimedVoices(SR, 2, voices, clock, park, unpark)
        self.lookahead = int(lookahead if lookahead is not None else SR // 2)
        self.project = model.new_project()
        self.project["tracks"] = []
        self.tracks = {}
        self.transport = {"playing": False, "anchor_frame": 0, "anchor_tick": 0.0}
        self.queue = []           # (frame, track id, pitch, velocity, length in ticks), sorted
        self.sequenced_to = None
        self.started = []         # (on frame, off frame, track id, pitch, note id): handed to the sampler
        self.chokes = []          # (frame, player, exclusive class, serial): stops still to come
        self.notes_changed = True
        self.selected = None      # the track live MIDI plays
        self.pads = {}            # drum track id -> its notes in grid order
        self.held = {}            # (track id, pitch) -> note id, live notes
        self._nid = 0
        self.stats = {"notes": 0, "cancelled": 0, "offs": 0, "passes": 0, "pass_us_max": 0,
                      "gc": 0, "gc_ms_max": 0, "live": 0, "live_us_max": 0, "dropped_frames": 0}
        # The master: pool -> rack -> a mixer voice, whose level is the master volume.
        self.mixer = audiomixer.Mixer(voice_count=1, sample_rate=SR, channel_count=2,
                                      bits_per_sample=16, samples_signed=True, buffer_size=BLOCK * 8)
        self.rack = None
        self.rack_key = repr([])  # the pool straight to the level, as below
        self.retired = []         # racks swapped out, deinited a while later
        self.master_level = 1.0
        self.mixer.voice[0].level = 1.0
        self.mixer.voice[0].play(self.pool.output, loop=True)
        if out is not None:
            out.play(self.mixer, loop=True)

    @property
    def output(self):
        return self.mixer

    # -- the song --------------------------------------------------------------

    def level_of(self, track):
        tracks = self.project["tracks"]
        solo = False
        for t in tracks:
            if t.get("solo"):
                solo = True
                break
        if track.get("mute") or (solo and not track.get("solo")):
            return 0.0
        return min(1.0, track.get("volume", 100) / 127.0)

    def apply(self, op, applied=False):
        self.apply_many([op], applied)

    def apply_many(self, ops, applied=False):
        """React to ops. ``applied``: the project already has them (the
        device's song model holds the same project and applied them first)."""
        notes = False
        tempo = False
        for op in ops:
            if not applied or op["op"] == "replace":
                model.apply(self.project, op)
            if op["op"] == "replace":
                model.migrate(self.project)
                notes = True
            path = op.get("path", ())
            if "patterns" in path or "song" in path or "song_mode" in path or "loop_scene" in path \
                    or "swing" in path or "next" in path or "current" in path:
                notes = True
            if path == ["tempo"]:
                tempo = True
        self.notes_changed = notes
        if tempo:
            self.retempo()
        self._tempo = self.project["tempo"]
        self.sync()
        self.notes_changed = False
        sound = False
        for op in ops:
            path = op.get("path", ())
            if op["op"] != "set" or "instrument" in path or "patch" in path:
                sound = True
        if notes or tempo or sound:
            self.resequence()
        self.sequence()

    def sync(self):
        wanted = {}
        for t in self.project["tracks"]:
            wanted[t["id"]] = t
        for tid in list(self.tracks):
            if tid not in wanted:
                self.tracks.pop(tid).close()
        for tid, t in wanted.items():
            tr = self.tracks.get(tid)
            if tr is None:
                self.tracks[tid] = Track(self, t)
            else:
                tr.sync(t)
        if self.selected not in wanted:
            self.selected = None
            for t in self.project["tracks"]:
                if t["kind"] != "drums":
                    self.selected = t["id"]
                    break
        self.sync_master()

    def sync_master(self):
        m = self.project.get("master") or {}
        level = max(0.0, min(1.5, m.get("volume", 100) / 100.0))
        if level != self.master_level:
            self.master_level = level
            self.mixer.voice[0].level = min(1.0, level)
        spec = [e for e in m.get("rack", []) if not e.get("bypass")]
        key = repr([e["name"] for e in spec])
        if key != self.rack_key:
            old = self.rack
            self.rack = None
            if spec:
                chain = tuple((e["name"], {}) for e in spec)
                try:
                    self.rack = audioeffects.create("Rack", self.pool.output, SR, chain=chain)
                except Exception as e:  # noqa: BLE001
                    self.post({"t": "failed", "track": None, "why": "the master rack: %s" % e})
            self.mixer.voice[0].play(self.rack.output if self.rack is not None else self.pool.output, loop=True)
            self.rack_key = key
            if old is not None:
                self.retired.append((ticks_ms(), old))
        if self.rack is not None:
            for i, e in enumerate(spec):
                child = self.rack.effects[i] if i < len(self.rack.effects) else None
                if child is not None:
                    for k, v in e.get("macros", {}).items():
                        child.set_macro(int(k), v)

    # -- time -------------------------------------------------------------------

    def ticks_per_frame(self):
        return self.project["tempo"] * model.PPQ / (60.0 * SR)

    def tick_at(self, frame):
        tr = self.transport
        return tr["anchor_tick"] + (frame - tr["anchor_frame"]) * self.ticks_per_frame()

    def frame_at(self, tick):
        tr = self.transport
        return tr["anchor_frame"] + (tick - tr["anchor_tick"]) / self.ticks_per_frame()

    def play(self, at_frame, tick=0.0):
        self.stop_notes()
        self.transport = {"playing": True, "anchor_frame": at_frame, "anchor_tick": tick}
        self.queue = []
        self.sequenced_to = None
        self.sequence()

    def stop(self):
        self.transport["playing"] = False
        self.stop_notes()

    def stop_notes(self):
        with self.pool.deferred():
            for tr in self.tracks.values():
                if tr.inst is not None:
                    tr.inst.all_notes_off()
        self.started = []
        self.chokes = []
        self.queue = []
        self.sequenced_to = None

    def retempo(self):
        """Keep the playhead where it is when the tempo changes. The op has
        already changed the tempo, so the anchor moves at the old one first."""
        if not self.transport["playing"]:
            return
        now = self.clock() + GUARD
        old = getattr(self, "_tempo", None)
        tr = self.transport
        if old:
            tick = tr["anchor_tick"] + (now - tr["anchor_frame"]) * old * model.PPQ / (60.0 * SR)
        else:
            tick = self.tick_at(now)
        tr["anchor_frame"] = now
        tr["anchor_tick"] = tick

    # -- what plays ---------------------------------------------------------------

    def segments(self, t0, t1):
        """What plays between ticks t0 and t1: :func:`songlink.model.segments`."""
        return model.segments(self.project, t0, t1)

    def _schedule(self, f0, f1):
        """The note-ons whose time falls in frames [f0, f1)."""
        t0, t1 = self.tick_at(f0), self.tick_at(f1)
        if t1 <= 0:
            return []
        found = []
        swing = self.project.get("swing", 0)
        for a, b, base0, pats in self.segments(max(t0, 0), t1):
            for track in self.project["tracks"]:
                tid = track["id"]
                tr = self.tracks.get(tid)
                if tr is None or tr.inst is None:
                    continue
                idx = pats.get(tid, -1)
                start_at = base0
                if isinstance(idx, tuple):
                    idx, start_at = idx
                if not 0 <= idx < len(track["patterns"]):
                    continue
                pat = track["patterns"][idx]
                if not pat["notes"]:
                    continue
                length = pat["bars"] * model.BAR
                k0 = int((a - start_at) // length)
                k1 = int((b - start_at) // length)
                for k in range(k0, k1 + 1):
                    base = start_at + k * length
                    for start, dur, pitch, vel in pat["notes"]:
                        tick = base + model.swung(start, swing)
                        if a <= tick < b:
                            found.append((int(self.frame_at(tick)), tid, pitch, vel, dur))
        found.sort(key=lambda e: e[0])
        return found

    def resequence(self):
        """An edit: cancel what's sequenced and not yet started (past the
        guard), and sequence it again from the song as it is now."""
        if not self.transport["playing"]:
            return
        cut = self.clock() + GUARD
        keep = []
        cancel = []
        for e in self.started:
            if e[0] >= cut:
                cancel.append(e)
            else:
                keep.append(e)
        if cancel:
            with self.pool.deferred():
                for on, off, tid, pitch, nid in cancel:
                    tr = self.tracks.get(tid)
                    if tr is not None and tr.inst is not None:
                        tr.inst.note_off(pitch, note_id=nid)
            self.stats["cancelled"] += len(cancel)
        self.started = keep
        self.chokes = [c for c in self.chokes if c[0] < cut]
        if self.sequenced_to is not None and self.sequenced_to > cut:
            self.sequenced_to = cut

    def sequence(self):
        """Hand the sampler every note due before now + lookahead, and send
        the note-offs whose frame has come. One pass of the loop."""
        t = ticks_us()
        now = self.clock()
        offs = []
        if self.started:
            keep = []
            for e in self.started:
                if e[1] <= now:
                    offs.append(e)
                else:
                    keep.append(e)
            self.started = keep
        chokes = []
        if self.chokes:
            keep = []
            for c in self.chokes:
                if c[0] <= now:
                    chokes.append(c)
                else:
                    keep.append(c)
            self.chokes = keep
        ons = []
        if self.transport["playing"]:
            # After a stall the notes it passed still go, late (the pool counts
            # them), unless they're more than a lookahead late: those are dropped.
            start = now if self.sequenced_to is None else max(self.sequenced_to, now - self.lookahead)
            if self.sequenced_to is not None and self.sequenced_to < start:
                self.stats["dropped_frames"] += start - self.sequenced_to
            until = now + self.lookahead
            if until > start:
                ons = self._schedule(start, until)
                self.sequenced_to = until
        if not (ons or offs or chokes):
            return
        tpf = self.ticks_per_frame()
        pool = self.pool
        with pool.deferred():
            for _f, player, cls, serial in chokes:
                for i, slot in enumerate(player.slots):
                    if slot is not None and slot[7] is player and slot[3] == cls and slot[1] < serial:
                        pool.voice[i].stop()
                        player._free(i)
            for on, off, tid, pitch, nid in offs:
                tr = self.tracks.get(tid)
                if tr is not None and tr.inst is not None:
                    if nid < 0:
                        tr.inst.note_off(pitch)     # an audition's
                    else:
                        tr.inst.note_off(pitch, note_id=nid)
            for frame, tid, pitch, vel, dur in ons:
                tr = self.tracks[tid]
                self._nid = (self._nid + 1) % NOTE_IDS
                nid = self._nid
                pool.at = frame
                try:
                    tr.inst.note_on(pitch, vel, note_id=nid)
                finally:
                    pool.at = None
                self.started.append((frame, frame + max(1, int(dur / tpf)), tid, pitch, nid))
        self.stats["notes"] += len(ons)
        self.stats["offs"] += len(offs)
        self.stats["passes"] += 1
        us = ticks_diff(ticks_us(), t)
        if us > self.stats["pass_us_max"]:
            self.stats["pass_us_max"] = us

    def horizon(self):
        """Frames of sequenced song still ahead of the pump (0 when stopped)."""
        if not self.transport["playing"] or self.sequenced_to is None:
            return 0
        return self.sequenced_to - self.clock()

    def collect(self, need_frames):
        """A garbage collection, only when the notes already handed over cover
        ``need_frames`` (or nothing is playing). Returns the ms it took, or None."""
        if self.transport["playing"] and self.horizon() < need_frames:
            return None
        t = ticks_ms()
        gc.collect()
        ms = ticks_diff(ticks_ms(), t)
        self.stats["gc"] += 1
        if ms > self.stats["gc_ms_max"]:
            self.stats["gc_ms_max"] = ms
        # Racks swapped out a while ago are past the pump's last look at them.
        if self.retired:
            keep = []
            for at, rack in self.retired:
                if ticks_diff(ticks_ms(), at) > 1000:
                    rack.deinit()
                else:
                    keep.append((at, rack))
            self.retired = keep
        return ms

    # -- live MIDI --------------------------------------------------------------

    def live(self, status, d1, d2):
        """A message from the keyboard, played now."""
        t = ticks_us()
        kind, chan = status & 0xF0, status & 0x0F
        target = self.selected
        if kind in (0x80, 0x90) and chan == 9:
            target = None
            for tr in self.project["tracks"]:
                if tr["kind"] == "drums":
                    target = tr["id"]
                    break
            if target is not None:
                pads = self.pads.get(target) or self.default_pads(target)
                if not 0 <= d1 - PAD_BASE < len(pads):
                    return
                d1 = pads[d1 - PAD_BASE]
        tr = self.tracks.get(target)
        if tr is None or tr.inst is None:
            return
        inst = tr.inst
        if kind == 0x90 and d2:
            inst.note_on(d1, d2)
        elif kind in (0x80, 0x90):
            inst.note_off(d1)
        elif kind == 0xE0:
            inst.pitch_bend(d1 | (d2 << 7))
        elif kind == 0xB0:
            inst.control_change(d1, d2)
        self.stats["live"] += 1
        us = ticks_diff(ticks_us(), t)
        if us > self.stats["live_us_max"]:
            self.stats["live_us_max"] = us

    def default_pads(self, tid):
        track = model.find_track(self.project, tid)
        seen = []
        for pat in (track or {}).get("patterns", []):
            for n in pat["notes"]:
                if n[2] not in seen:
                    seen.append(n[2])
        seen.sort()
        for p in GM_PADS:
            if p not in seen:
                seen.append(p)
        return seen

    def audition(self, tid, pitch, velocity, seconds=None):
        tr = self.tracks.get(tid)
        if tr is None or tr.inst is None:
            return
        if velocity:
            tr.inst.note_on(pitch, velocity)
            if seconds:
                now = self.clock()
                self.started.append((now, now + int(seconds * SR), tid, pitch, -1))
        else:
            tr.inst.note_off(pitch)

    def sounding(self):
        return self.pool.sounding()
