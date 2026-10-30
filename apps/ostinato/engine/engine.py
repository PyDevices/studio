"""Ostinato's sound engine: runs in a worker, owns everything you hear.

The page relays MIDI bytes here (``feed``); the AudioWorklet asks for audio
(``render``). Between the two, an audioinstruments voice plays what arrives.
Every 256-frame block starts by draining the MIDI that came in since the
last one, so a note lands at most one block after it arrived.

P0 spike: one voice, two ways to read MIDI (``parse="adafruit"`` through
``adafruit_midi.MIDI`` over a midiports port, or ``parse="raw"`` straight
from the same port's bytes), so the cost of each can be measured.
"""

import audiocore
import audioinstruments
import adafruit_midi
from adafruit_midi.control_change import ControlChange
from adafruit_midi.note_off import NoteOff
from adafruit_midi.note_on import NoteOn
from adafruit_midi.pitch_bend import PitchBend
from midiports import PortIn

BLOCK = 256  # frames per synthio buffer (SYNTHIO_MAX_DUR)


class RelayIn(PortIn):
    """MIDI bytes the page relayed, as a midiports input port.

    ``feed`` is called from the worker's message handler, which runs between
    renders on the same thread, so no locking is needed.
    """

    def __init__(self, size=4096):
        super().__init__(64)
        self._buf = bytearray(size)
        self._start = 0
        self._end = 0

    def feed(self, data):
        n = len(data)
        if self._end + n > len(self._buf):
            live = self._end - self._start
            self._buf[:live] = self._buf[self._start : self._end]
            self._start, self._end = 0, live
            if live + n > len(self._buf):
                raise OverflowError("MIDI relay overflow")
        self._buf[self._end : self._end + n] = data
        self._end += n

    def any(self):
        return self._end - self._start

    def readinto(self, buf, nbytes=None):
        n = min(self._end - self._start, len(buf) if nbytes is None else nbytes)
        if n:
            buf[:n] = self._buf[self._start : self._start + n]
            self._start += n
            if self._start == self._end:
                self._start = self._end = 0
        return n


class Engine:
    def __init__(self, sample_rate, voice="minimoog", parse="adafruit", plant="", lead_ms=0):
        self.sample_rate = sample_rate
        self.voice = voice
        self.inst = audioinstruments.create(voice, sample_rate=sample_rate, channel_count=1)
        self.port = RelayIn()
        self.parse = parse
        self.midi = adafruit_midi.MIDI(midi_in=self.port, in_buf_size=64)
        self._raw = bytearray(64)
        self.frame = 0           # frames rendered so far
        self.applied = []        # (frame, status, data1, data2) for each message
        self.messages = 0
        self._out = bytearray(BLOCK * 2 * 64)
        self._view = memoryview(self._out)
        # Faults planted on purpose, to prove tools/tap_check.py catches them.
        self.plant = plant
        self._late = []          # "delay": note-ons held back six blocks
        self._ons = 0
        # Scheduling. With lead_ms > 0 every message plays exactly lead_ms
        # after the key was pressed (its context time, from the page), at the
        # sample, instead of at the start of the next block: jitter becomes a
        # constant delay. offset maps rendered frames to context frames: a
        # rendered frame k is heard at context frame k + offset (the worklet
        # reports it at start and after every underrun).
        self.lead = int(lead_ms * sample_rate / 1000)
        self.offset = 0
        self._stamps = []        # context time of each fed message, in order
        self._pending = []       # (due rendered frame, status, d1, d2)
        self.late = 0            # messages that arrived after their due frame

    def feed(self, data, at=None):
        self.port.feed(data)
        self._stamps.append(at)

    def set_offset(self, offset):
        self.offset = offset

    def _queue(self, status, d1, d2):
        at = self._stamps.pop(0) if self._stamps else None
        due = self.frame
        if self.lead and at is not None:
            due = int(at * self.sample_rate) + self.lead - self.offset
            if due < self.frame:
                self.late += 1
                due = self.frame
        if status is not None:
            self._pending.append((due, status, d1, d2))

    # -- MIDI ---------------------------------------------------------------

    def _apply(self, status, d1, d2, pos=0):
        kind = status & 0xF0
        inst = self.inst
        if kind == 0x90 and d2 and self.plant:
            self._ons += 1
            if self.plant == "lose-note" and self._ons % 10 == 0:
                return
            if self.plant == "detune":
                d1 += 1
            if self.plant == "delay":
                self._late.append((self.frame + 6 * BLOCK, d1, d2))
                return
        if kind == 0x80 and self.plant == "detune":
            d1 += 1
        if kind == 0x90 and d2:
            inst.note_on(d1, d2, sample_position=pos)
        elif kind == 0x80 or kind == 0x90:
            inst.note_off(d1, sample_position=pos)
        elif kind == 0xB0:
            inst.control_change(d1, d2, sample_position=pos)
        elif kind == 0xE0:
            inst.pitch_bend(d1 | (d2 << 7), sample_position=pos)
        self.messages += 1
        self.applied.append((self.frame + pos, status, d1, d2))

    def _drain_adafruit(self):
        midi = self.midi
        while True:
            msg = midi.receive()
            if msg is None:
                return
            # One fed message, one parsed message (Web MIDI hands over whole
            # messages), so each one takes the next timestamp, kept or not.
            if isinstance(msg, NoteOn):
                self._queue(0x90 | msg.channel, msg.note, msg.velocity)
            elif isinstance(msg, NoteOff):
                self._queue(0x80 | msg.channel, msg.note, msg.velocity)
            elif isinstance(msg, ControlChange):
                self._queue(0xB0 | msg.channel, msg.control, msg.value)
            elif isinstance(msg, PitchBend):
                self._queue(0xE0 | msg.channel, msg.pitch_bend & 0x7F, msg.pitch_bend >> 7)
            else:
                self._queue(None, 0, 0)

    def _drain_raw(self):
        # Web MIDI hands over whole messages, so the stream is a run of
        # status-led messages; realtime bytes (0xF8 and up) stand alone.
        port, raw = self.port, self._raw
        while port.any():
            status = port._buf[port._start]
            if status >= 0xF8:
                need = 1
            elif status & 0xF0 in (0xC0, 0xD0):
                need = 2
            else:
                need = 3
            if port.any() < need:
                return
            port.readinto(raw, need)
            if need == 3:
                self._queue(raw[0], raw[1], raw[2])
            else:
                self._queue(None, 0, 0)

    # -- audio --------------------------------------------------------------

    def render(self, blocks):
        """Render ``blocks`` x 256 frames of int16 mono; returns a memoryview."""
        out = self._view
        at = 0
        drain = self._drain_adafruit if self.parse == "adafruit" else self._drain_raw
        get = audiocore.get_buffer
        output = self.inst.output
        for _ in range(blocks):
            if self.port.any():
                drain()
            if self._pending:
                end = self.frame + BLOCK
                keep = []
                for item in self._pending:
                    if item[0] < end:
                        self._apply(item[1], item[2], item[3], item[0] - self.frame)
                    else:
                        keep.append(item)
                self._pending = keep
            while self._late and self._late[0][0] <= self.frame:
                _, p, v = self._late.pop(0)
                self.inst.note_on(p, v)
            _, buf = get(output)
            n = len(buf)
            out[at : at + n] = buf
            at += n
            self.frame += n // 2
        return out[:at]

    def take_applied(self):
        got, self.applied = self.applied, []
        return got
