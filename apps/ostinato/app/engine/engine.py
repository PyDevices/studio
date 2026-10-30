"""Ostinato's sound engine. Each engine worker runs one Engine.

Several engine workers render in parallel, each owning some of the tracks
(the UI assigns them). The AudioWorklet sums their streams. Everything that
must line up across workers is expressed in the audio context's frames:
a rendered frame k of this worker plays at context frame k + offset, and the
transport says "tick T at context frame F, at this tempo".

Live MIDI plays as soon as it arrives, at the start of the next 256-frame
block. Its timestamp is kept for recording, which places each note where it
was played, not where the engine got round to it.

The song's notes are sequenced ahead of the render, LOOKAHEAD frames early,
each stamped with the frame it plays at, and handed to its instrument when
that frame's block renders. A sequencer that pauses (a garbage collection on
a board) then costs nothing as long as the pause is shorter than the
lookahead: the notes are already waiting. Any edit throws away what was
sequenced and not yet played, so an edit is heard as soon as before.

A SoundFont track reads its samples when the song arrives and when its
program changes, every sample its notes reach, rather than when a note first
wants one, and says what that cost (``preloaded``).
"""

import _audiodsp
import audiocore
import audioeffects
import audioinstruments
import audiometer

import os

import model

DEBUG = bool(os.environ.get("OSTINATO_DEBUG"))
BLOCK = 256
BLOCK_BYTES = BLOCK * 4          # stereo int16
MAX_VOICES = 8
LOOKAHEAD = int(getattr(os, "environ", {}).get("OSTINATO_LOOKAHEAD", 8 * 256))   # frames sequenced ahead of the render
PAD_BASE = 36            # the DMK-25's pad 1 (bank A); banks B and C continue upward
_SILENCE = bytes(BLOCK_BYTES)
_gains = {}


_pairs = {}


def gain_pair(left, right):
    """A block of int16 gains for multiply_s16, a pair per frame: left, right."""
    ql = max(0, min(32767, int(left * 32767)))
    qr = max(0, min(32767, int(right * 32767)))
    buf = _pairs.get((ql, qr))
    if buf is None:
        buf = _pairs[(ql, qr)] = (ql.to_bytes(2, "little", signed=True) + qr.to_bytes(2, "little", signed=True)) * BLOCK
    return buf


def pan_gains(pan):
    """A balance control: centre leaves both sides at full level; turning it
    lowers the far side, to silence at the end."""
    p = max(0, min(127, pan)) / 127.0
    return min(1.0, 2.0 * (1.0 - p)), min(1.0, 2.0 * p)


def gain_buffer(level):
    """A block of constant int16 for multiply_s16: level 1.0 is 32767."""
    q = max(0, min(32767, int(level * 32767)))
    buf = _gains.get(q)
    if buf is None:
        buf = _gains[q] = q.to_bytes(2, "little", signed=True) * (BLOCK * 2)
    return buf


_LEFT = ((32767).to_bytes(2, "little") + bytes(2)) * BLOCK     # keeps one side of stereo
_RIGHT = (bytes(2) + (32767).to_bytes(2, "little")) * BLOCK


class StereoMeter:
    """Left and right peak levels of a stream, in audiometer's level bytes
    (2 * (dB + 100)), held at their highest until read.

    audiometer sums the channels, so each side is metered with the other
    zeroed (native multiply_s16): that reads 6 dB low, put back on reading.
    All of it is C; Python never touches a sample."""

    def __init__(self, rate):
        self.rate = rate
        self.left, self.right = audiometer.Meter(1), audiometer.Meter(1)
        self.peak = [0, 0]

    def feed(self, block):
        mul = _audiodsp.multiply_s16
        self.left.feed(mul(block, _LEFT, 1.0, 2), self.rate, 2)
        self.right.feed(mul(block, _RIGHT, 1.0, 2), self.rate, 2)
        pl = self.left.levels()[2]
        pr = self.right.levels()[2]
        if pl > self.peak[0]:
            self.peak[0] = pl
        if pr > self.peak[1]:
            self.peak[1] = pr

    def read(self):
        """[left, right] in dB since the last read (-100 for silence)."""
        out = [round(b / 2 - 100 + 6, 1) if b else -100 for b in self.peak]
        self.peak = [0, 0]
        return out


class Puller:
    """Whole 256-frame stereo blocks from a source whose buffers vary in size."""

    def __init__(self, source):
        self.source = source
        self.spill = b""

    def block(self):
        out = self.spill
        get = audiocore.get_buffer
        while len(out) < BLOCK_BYTES:
            _, buf = get(self.source)
            if not len(buf):
                out += bytes(BLOCK_BYTES - len(out))
                break
            out += bytes(buf)
        self.spill = out[BLOCK_BYTES:]
        return out[:BLOCK_BYTES]


class SilentInstrument:
    """Stands in for an instrument that couldn't be built: a synth with no notes,
    so the track's graph (rack, meter, mixer voice) is whole and renders silence."""

    def __init__(self, sample_rate):
        import synthio

        self.output = synthio.Synthesizer(sample_rate=sample_rate, channel_count=2)

    def program_change(self, *args, **kw):
        pass

    def set_macro(self, *args, **kw):
        pass

    def note_on(self, *args, **kw):
        pass

    def note_off(self, *args, **kw):
        pass

    def all_notes_off(self, *args, **kw):
        pass

    def control_change(self, *args, **kw):
        pass

    def pitch_bend(self, *args, **kw):
        pass

    def deinit(self):
        self.output.deinit()


class TrackVoice:
    """One track's live graph in this worker: instrument -> rack -> mixer voice."""

    def __init__(self, engine, track, slot):
        self.engine = engine
        self.id = track["id"]
        self.slot = slot
        self.instrument_name = None
        self.inst = None
        self.rack = None
        self.rack_spec = None
        self.fx_macros = {}
        self.held = {}            # pitch -> count, for live notes
        self.meter = StereoMeter(engine.sample_rate)
        self.sync(track)

    def sync(self, track):
        spec = [e for e in track.get("rack", []) if not e.get("bypass")]
        heavy = (track["instrument"] != self.instrument_name or
                 repr([e["name"] for e in spec]) != self.rack_spec)
        if heavy:
            # Building blocks this worker for longer than the audio queue
            # lasts: tell the worklet first, so it plays this stream as
            # silence instead of counting dropouts.
            self.engine.busy(True)
            try:
                self._sync(track)
            finally:
                self.engine.busy(False)
        else:
            self._sync(track)

    def _sync(self, track):
        sr = self.engine.sample_rate
        if track["instrument"] != self.instrument_name:
            old = self.inst
            try:
                self.inst = audioinstruments.create(track["instrument"], sr, channel_count=2)
            except Exception as e:  # noqa: BLE001  (a SoundFont with no bank, a name this build lacks)
                # The track stays, silent, and the UI says why: an instrument
                # that can't be built must never take the engine down with it.
                self.inst = SilentInstrument(sr)
                why = "%s can't play %s: %s" % (track.get("name", "A track"), track["instrument"], e)
                print("engine %d: %s" % (self.engine.index, why))      # the log keeps the reason too
                self.engine.post({"t": "failed", "track": self.id, "why": why})
            self.instrument_name = track["instrument"]
            self.patch = None
            self.rack_spec = None   # the rack wraps the old instrument; rebuild it
            if old is not None:
                old.deinit()
        if track.get("patch") != getattr(self, "patch", None):
            self.inst.program_change(int(track.get("patch") or 0))
            self.patch = track.get("patch")
            self.macros = {}
            self.preloaded = None
        if self.engine.notes_changed or getattr(self, "preloaded", None) is None:
            self.preload(track)
        for k, v in track.get("macros", {}).items():
            if self.macros.get(k) != v:
                self.inst.set_macro(int(k), v)
                self.macros[k] = v
        spec = [e for e in track.get("rack", []) if not e.get("bypass")]
        key = repr([e["name"] for e in spec])
        if key != self.rack_spec:
            old = self.rack
            if spec:
                chain = tuple((e["name"], {}) for e in spec)
                self.rack = audioeffects.create("Rack", self.inst.output, sr, chain=chain)
            else:
                self.rack = None
            self.rack_spec = key
            self.fx_macros = {}
            self.engine.plug(self)
            if old is not None:
                old.deinit()
        if self.rack is not None:
            seen = self.fx_macros
            for i, e in enumerate(spec):
                child = self.rack.effects[i] if i < len(self.rack.effects) else None
                if child is not None:
                    for k, v in e.get("macros", {}).items():
                        if seen.get((i, k)) != v:
                            child.set_macro(int(k), v)
                            seen[(i, k)] = v
        self.level = self.engine.level_of(track)
        self.pan = pan_gains(track.get("pan", 64))

    @property
    def output(self):
        return self.rack.output if self.rack is not None else self.inst.output

    def preload(self, track):
        """Read every sample this track's notes reach into the bank's arena now,
        not at the note, and tell the UI what they cost. Only a SoundFont
        instrument has samples to read; others return at once."""
        player = getattr(self.inst, "_player", None)
        bank = getattr(player, "bank", None)
        if bank is None or not hasattr(bank, "buffer"):
            self.preloaded = {}
            return
        import time

        wanted = set()
        for pat in track.get("patterns", []):
            for _start, _len, pitch, vel in pat["notes"]:
                wanted.add((int(pitch), int(vel)))
        if self.preloaded is not None and wanted <= self.preloaded.get("keys", set()):
            return
        t0 = time.perf_counter()
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
        self.preloaded = {"keys": wanted}
        msg = {"t": "preloaded", "track": self.id, "samples": len(sizes), "bytes": sum(sizes.values()),
               "read": bank.reads - reads, "ms": round((time.perf_counter() - t0) * 1000, 1),
               "arena": bank.arena_bytes, "cached": bank.cached, "cache": bank.cache_bytes}
        self.engine.post(msg)

    def note_on(self, pitch, velocity, pos=0):
        self.inst.note_on(pitch, velocity, sample_position=pos)

    def note_off(self, pitch, pos=0):
        self.inst.note_off(pitch, sample_position=pos)

    def close(self):
        if self.rack is not None:
            self.rack.deinit()
        self.inst.deinit()


class Click:
    """The metronome: a short sine blip, higher on the downbeat."""

    def __init__(self, sample_rate):
        import synthio

        self.synth = synthio.Synthesizer(sample_rate=sample_rate, channel_count=2)
        env = synthio.Envelope(attack_time=0.001, decay_time=0.04, release_time=0.02,
                               attack_level=0.6, sustain_level=0.0)
        self.notes = {
            True: synthio.Note(frequency=1760, envelope=env),
            False: synthio.Note(frequency=1320, envelope=env),
        }
        self.puller = Puller(self.synth)
        self.ringing = 0              # blocks still worth rendering after a click

    def click(self, downbeat):
        note = self.notes[downbeat]
        self.synth.release(note)
        self.synth.press(note)
        self.ringing = 20

    def block(self):
        """The click's next block, or None when there's nothing to hear (the
        synth isn't rendered at all then: a silent synthio still costs)."""
        if not self.ringing:
            return None
        self.ringing -= 1
        return self.puller.block()


class Engine:
    def __init__(self, sample_rate, index, post, pause=None):
        self.sample_rate = sample_rate
        self.index = index            # which engine worker this is
        self.post = post              # send a dict to the UI
        self._pause = pause           # tell the worklet to wait for this stream
        self.streaming = False        # the worklet is playing this stream
        self.offline = False          # rendering a file, not playing
        self.project = model.new_project()
        self.mine = []                # track ids this worker renders
        self.voices = {}              # track id -> TrackVoice
        self.pads = {}                # drum track id -> its notes in grid order (see live)
        self.click = Click(sample_rate) if index == 0 else None
        self.frame = 0                # rendered frames
        self.offset = None            # context frame of rendered frame 0
        self.asked = 0                # context frame the worklet asked at, latest
        self.transport = {"playing": False, "anchor_frame": 0, "anchor_tick": 0.0}
        self.pending = []             # (rendered frame, track id, pitch) note-offs
        self.lookahead = LOOKAHEAD    # frames the sequencer runs ahead of the render
        self.queue = []               # (context frame, track id, pitch, velocity, length in ticks): sequenced, not played
        self.sequenced_to = None      # the context frame the queue covers up to
        self.notes_changed = True     # an op may have touched notes: SoundFont tracks preload again
        self.selected = None          # track id live MIDI plays
        self.armed = None             # track id that records
        self.recording = False
        self.rec_open = {}            # pitch -> (tick, velocity)
        self.render_ms = 0.0
        self.render_blocks = 0
        self.sequencing = True        # False stands for a sequencer that's paused (the checks plant one)
        self._out = bytearray(BLOCK * 4 * 64)
        self._view = memoryview(self._out)
        self._spill = b""

    # -- project ------------------------------------------------------------

    def busy(self, on):
        if self.streaming and self._pause is not None:
            self._pause(on)

    def level_of(self, track):
        tracks = self.project["tracks"]
        solo = any(t.get("solo") for t in tracks)
        if track.get("mute") or (solo and not track.get("solo")):
            return 0.0
        return min(1.5, track.get("volume", 100) / 127.0) * 0.8

    def apply(self, op):
        self.apply_many([op])

    def apply_many(self, ops):
        for op in ops:
            model.apply(self.project, op)
            if op["op"] == "replace":
                model.migrate(self.project)
        self.notes_changed = any(op["op"] == "replace" or "patterns" in op.get("path", ()) for op in ops)
        self.resequence()
        self.sync()
        self.notes_changed = False
        self.sequence_ahead()

    def resequence(self):
        """Forget what was sequenced and not yet played: the song or the
        transport changed. :meth:`sequence_ahead` sequences it again."""
        self.queue = []
        self.sequenced_to = None

    def sequence_ahead(self):
        """Sequence the lookahead now, not at the next block: a pause just
        after an edit or a play mustn't make the first notes late."""
        if self.sequencing and self.offset is not None:
            self._sequence(self.frame + self.offset + BLOCK + self.lookahead)

    def assign(self, ids):
        self.mine = list(ids)
        self.resequence()
        self.sync()
        self.sequence_ahead()

    def sync(self):
        """Bring the live graph in line with the project for this worker's tracks."""
        wanted = {t["id"]: t for t in self.project["tracks"] if t["id"] in self.mine}
        for tid in list(self.voices):
            if tid not in wanted:
                self.voices.pop(tid).close()
        used = {v.slot for v in self.voices.values()}
        for tid, t in wanted.items():
            v = self.voices.get(tid)
            if v is None:
                slot = next(s for s in range(MAX_VOICES - 1) if s not in used)
                used.add(slot)
                self.voices[tid] = TrackVoice(self, t, slot)
            else:
                v.sync(t)

    def plug(self, voice):
        voice.puller = Puller(voice.output)

    # -- time ---------------------------------------------------------------

    def ticks_per_frame(self):
        return self.project["tempo"] * model.PPQ / (60.0 * self.sample_rate)

    def tick_at(self, ctx_frame):
        tr = self.transport
        return tr["anchor_tick"] + (ctx_frame - tr["anchor_frame"]) * self.ticks_per_frame()

    def frame_at(self, tick):
        tr = self.transport
        return tr["anchor_frame"] + (tick - tr["anchor_tick"]) / self.ticks_per_frame()

    def play(self, at_frame, tick=0.0):
        self.transport = {"playing": True, "anchor_frame": at_frame, "anchor_tick": tick}
        self.resequence()
        self.sequence_ahead()
        if DEBUG:
            print("play", self.index, "at", at_frame, "now", self.frame + (self.offset or 0), "mine", self.mine,
                  "song_mode", self.project.get("song_mode"), "song", self.project.get("song"))

    def stop(self):
        self.transport["playing"] = False
        for v in self.voices.values():
            v.inst.all_notes_off()
        self.pending = []
        self.resequence()
        self.recording = False
        self.rec_open = {}

    def retempo(self, at_frame):
        """Keep the playhead where it is when the tempo changes."""
        if self.transport["playing"]:
            tick = self.tick_at(at_frame)
            self.transport["anchor_frame"] = at_frame
            self.transport["anchor_tick"] = tick
            self.resequence()
            self.sequence_ahead()

    # -- what plays ---------------------------------------------------------

    def segments(self, t0, t1):
        """What plays between ticks t0 and t1, as (from, to, base, patterns):
        patterns maps track id -> pattern index (or -1 for silence), and a
        pattern's tick 0 falls at ``base``. Pattern mode is one endless
        segment; song mode walks the scenes, looping the song, or loops the
        one scene ``loop_scene`` names."""
        p = self.project
        song = p.get("song") or []
        loop = p.get("loop_scene")
        if loop is not None and 0 <= loop < len(song):
            song = [song[loop]]
        if not (p.get("song_mode") and song):
            # Each track plays its own pattern; a pending switch splits the
            # range at its bar line. Values are (pattern, the tick it starts on).
            cuts = sorted({t["next"]["at"] for t in p["tracks"] if t.get("next") and t0 < t["next"]["at"] < t1})
            edges = [t0] + cuts + [t1]
            return [(a, b, 0, {t["id"]: model.pattern_at(t, a) for t in p["tracks"]})
                    for a, b in zip(edges, edges[1:])]
        lengths = []
        for scene in song:
            bars = 1
            for t in p["tracks"]:
                i = scene["patterns"].get(str(t["id"]), -1)
                if 0 <= i < len(t["patterns"]):
                    bars = max(bars, t["patterns"][i]["bars"])
            lengths.append(bars * model.BAR * max(1, scene.get("repeat", 1)))
        total = sum(lengths)
        out = []
        loop = int(max(t0, 0) // total)
        while True:
            start = loop * total
            for scene, length in zip(song, lengths):
                a, b = start, start + length
                start = b
                if b <= t0:
                    continue
                if a >= t1:
                    return out
                pats = {t["id"]: scene["patterns"].get(str(t["id"]), -1) for t in p["tracks"]}
                out.append((max(a, t0), min(b, t1), a, pats))
            loop += 1

    def _sequence(self, until):
        """Sequence the song's notes up to context frame ``until`` into the queue."""
        if not self.transport["playing"] or self.offset is None:
            return
        start = self.frame + self.offset
        if self.sequenced_to is not None and self.sequenced_to > start:
            start = self.sequenced_to
        if until <= start:
            return
        self._schedule(start, until)
        self.sequenced_to = until

    def _play_queued(self, ctx0, ctx1):
        """Hand each sequenced note whose frame falls before ``ctx1`` to its
        instrument, at its sample in this block; and the metronome's clicks."""
        if not self.transport["playing"]:
            return
        self._clicks(self.tick_at(ctx0), self.tick_at(ctx1))
        q = self.queue
        if not q or q[0][0] >= ctx1:
            return
        n = 0
        while n < len(q) and q[n][0] < ctx1:
            n += 1
        due, self.queue = q[:n], q[n:]
        tpf = self.ticks_per_frame()
        for frame, tid, pitch, vel, dur in due:
            voice = self.voices.get(tid)
            if voice is None:
                continue
            pos = frame - ctx0
            pos = 0 if pos < 0 else (BLOCK - 1 if pos >= BLOCK else pos)
            voice.note_on(pitch, vel, pos)
            self.pending.append((self.frame + pos + int(dur / tpf), tid, pitch))

    def _schedule(self, ctx0, ctx1):
        """Queue the note-ons whose time falls in [ctx0, ctx1), each with its frame."""
        t0, t1 = self.tick_at(ctx0), self.tick_at(ctx1)
        if t1 <= 0:
            return
        found = []
        swing = self.project.get("swing", 0)
        for a, b, base0, pats in self.segments(max(t0, 0), t1):
            for tid, voice in self.voices.items():
                track = model.find_track(self.project, tid)
                if track is None:
                    continue
                idx = pats.get(tid, -1)
                start_at = base0
                if isinstance(idx, tuple):            # pattern mode: (pattern, where it starts)
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
                            frame = int(self.frame_at(tick) - ctx0) + ctx0
                            found.append((frame, tid, pitch, vel, dur))
        if found:
            found.sort(key=lambda e: e[0])
            if self.queue and self.queue[-1][0] > found[0][0]:
                found = sorted(self.queue + found, key=lambda e: e[0])
                self.queue = found
            else:
                self.queue.extend(found)

    def _clicks(self, t0, t1):
        """The metronome's clicks in [t0, t1); a count-in (ticks before 0)
        clicks whether or not the metronome is on."""
        if self.click is None or self.offline or not (self.project.get("metronome") or t0 < 0):
            return
        beat = model.PPQ
        for b in range(int(t0 // beat), int(t1 // beat) + 1):
            tick = b * beat
            if t0 <= tick < t1 and (tick < 0 or self.project.get("metronome")):
                self.click.click(b % model.BEATS_PER_BAR == 0)

    def _release(self, end):
        if not self.pending:
            return
        keep = []
        for off, tid, pitch in self.pending:
            if off < end:
                v = self.voices.get(tid)
                if v is not None:
                    v.note_off(pitch, max(0, off - self.frame))
            else:
                keep.append((off, tid, pitch))
        self.pending = keep

    # -- live MIDI ------------------------------------------------------------

    def live(self, status, d1, d2, key_frame):
        """One incoming MIDI message, played now. ``key_frame`` is the context
        frame the key went down at, for recording."""
        kind, chan = status & 0xF0, status & 0x0F
        target = None
        if kind in (0x80, 0x90) and chan == 9:
            drums = [t for t in self.project["tracks"] if t["kind"] == "drums"]
            target = drums[0]["id"] if drums else None
        else:
            target = self.selected
        voice = self.voices.get(target)
        if kind in (0x80, 0x90) and chan == 9 and self.pads.get(target):
            # The keyboard's pads send fixed notes from 36 up; play the grid's
            # rows instead, pad 1 the first, so every pad sounds on every kit.
            pads = self.pads[target]
            if not 0 <= d1 - PAD_BASE < len(pads):
                return
            d1 = pads[d1 - PAD_BASE]
        if DEBUG:
            print("live", self.index, hex(status), d1, d2, "target", target, "mine", voice is not None)
        if voice is None:
            return
        if kind == 0x90 and d2:
            voice.note_on(d1, d2)
            self._record_on(target, d1, d2, key_frame)
        elif kind in (0x80, 0x90):
            voice.note_off(d1)
            self._record_off(target, d1, key_frame)
        elif kind == 0xE0:
            voice.inst.pitch_bend(d1 | (d2 << 7))
        elif kind == 0xB0 and d1 in (1, 64):
            voice.inst.control_change(d1, d2)

    def _record_on(self, tid, pitch, vel, key_frame):
        if DEBUG:
            print("record_on?", self.index, "recording", self.recording, "armed", self.armed, "tid", tid,
                  "playing", self.transport["playing"])
        if not (self.recording and tid == self.armed and self.transport["playing"]):
            return
        self.rec_open[pitch] = (self.tick_at(key_frame), vel)

    def _record_off(self, tid, pitch, key_frame):
        if pitch not in self.rec_open:
            return
        start, vel = self.rec_open.pop(pitch)
        end = self.tick_at(key_frame)
        self.post({"t": "recorded", "track": tid, "start": start, "end": end, "pitch": pitch, "velocity": vel})

    # -- audio ----------------------------------------------------------------

    def render(self, blocks, midi_drain=None):
        """``blocks`` x 256 stereo frames of int16; a memoryview.

        Tracks are summed with audiodsp's native kernels (multiply_s16 for each
        track's level, mix_s16 to add them): a Python mixer loop costs most of
        a core in a browser.
        """
        if blocks * BLOCK_BYTES > len(self._out):
            # An export renders thousands of frames at a time; grow to fit.
            self._out = bytearray(blocks * BLOCK_BYTES)
            self._view = memoryview(self._out)
        out = self._view
        at = 0
        mul, mix = _audiodsp.multiply_s16, _audiodsp.mix_s16
        for _ in range(blocks):
            if midi_drain is not None:
                midi_drain()
            if self.offset is not None:
                ctx0 = self.frame + self.offset
                if self.sequencing:
                    self._sequence(ctx0 + BLOCK + self.lookahead)
                self._play_queued(ctx0, ctx0 + BLOCK)
            self._release(self.frame + BLOCK)
            acc = None
            for v in self.voices.values():
                buf = v.puller.block()
                if v.level <= 0.0:
                    continue
                gl, gr = v.pan
                if gl < 0.9999 or gr < 0.9999:
                    buf = mul(buf, gain_pair(v.level * gl, v.level * gr), 1.0, 2)
                elif v.level < 0.9999:
                    buf = mul(buf, gain_buffer(v.level), 1.0, 2)
                if not self.offline:
                    v.meter.feed(buf)
                acc = buf if acc is None else mix(acc, buf)
            if self.click is not None:
                c = self.click.block()
                if c is not None:
                    c = mul(c, gain_buffer(0.5), 1.0, 2)
                    acc = c if acc is None else mix(acc, c)
            out[at : at + BLOCK_BYTES] = acc if acc is not None else _SILENCE
            at += BLOCK_BYTES
            self.frame += BLOCK
        return out[:at]

    def render_offline(self, frames, chunk, send):
        """Render ``frames`` of the song from the top into ``send(seq, pcm)``
        chunks, as fast as the worker can go, then put live playing back.
        The worklet plays this stream as silence meanwhile."""
        self.busy(True)
        saved = (self.frame, self.offset, dict(self.transport), list(self.pending), self.recording)
        self.resequence()
        self.offline = True
        try:
            for v in self.voices.values():
                v.inst.all_notes_off()
            self.frame, self.offset, self.pending, self.recording = 0, 0, [], False
            self.transport = {"playing": True, "anchor_frame": 0, "anchor_tick": 0.0}
            done, seq = 0, 0
            per = max(BLOCK, chunk // BLOCK * BLOCK)
            while done < frames:
                n = min(per, frames - done)
                blocks = (n + BLOCK - 1) // BLOCK
                send(seq, bytes(self.render(blocks)))
                done += blocks * BLOCK
                seq += 1
            return seq
        finally:
            for v in self.voices.values():
                v.inst.all_notes_off()
            self.offline = False
            self.frame, self.offset, self.transport, self.pending, self.recording = saved
            self.resequence()
            self.busy(False)
