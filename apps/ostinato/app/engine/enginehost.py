"""One engine, whatever hosts it: a browser worker, a process, or a thread.

A host gives an ``EngineHost`` three things and asks one:

- ``handle(msg)``: a message from the UI (an op, the transport, a selection...),
- ``live(status, d1, d2, key_frame)``: a MIDI message, with the audio frame its
  key went down at,
- ``render(blocks, midi_drain)``: the next ``blocks`` x 256 stereo int16 frames.

Everything it says back goes through ``post(dict)`` (to the UI) and
``pause(on)`` (to whoever plays its stream). It imports nothing from a
browser, so the same code runs in Pyodide workers and on CPython.
"""

import time

from engine import BLOCK, Engine


class EngineHost:
    def __init__(self, sample_rate, index, post, pause=None, on_assign=None):
        self.sample_rate = sample_rate
        self.index = index
        self.post = post
        self.engine = Engine(sample_rate, index, post, pause)
        self.on_assign = on_assign        # the first assign: the host may start streaming
        self.render_ms = 0.0
        self.render_blocks = 0

    # -- audio --------------------------------------------------------------------

    def render(self, blocks, midi_drain=None, offset=None):
        e = self.engine
        if offset is not None:
            e.offset = offset
        t = time.perf_counter()
        pcm = e.render(blocks, midi_drain)
        self.render_ms += (time.perf_counter() - t) * 1000
        self.render_blocks += blocks
        return pcm

    def load(self):
        """The share of real time spent rendering since the last call."""
        blocks = self.render_blocks or 1
        load = self.render_ms / (blocks * BLOCK * 1000.0 / self.sample_rate)
        self.render_ms = 0.0
        self.render_blocks = 0
        return load

    # -- MIDI ---------------------------------------------------------------------

    def live(self, status, d1, d2, key_frame):
        self.engine.live(status, d1, d2, key_frame)
        if self.index == 0:
            self.post({"t": "midi", "d": [status, d1, d2]})

    # -- the UI's messages --------------------------------------------------------

    def status(self, **extra):
        e = self.engine
        frame = e.asked
        msg = {"t": "status", "frame": frame, "tick": e.tick_at(frame) if e.transport["playing"] else None,
               "load": self.load(), "voices": len(e.voices), "offset": e.offset,
               "meters": {str(tid): v.meter.read() for tid, v in e.voices.items()}}
        msg.update(extra)
        self.post(msg)

    def handle(self, msg, status_extra=None):
        e = self.engine
        sr = self.sample_rate
        t = msg.get("t")
        if t == "op":
            e.apply(msg["op"])
            if msg["op"].get("path") == ["tempo"]:
                e.retempo(e.asked)
        elif t == "ops":
            e.apply_many(msg["ops"])
            if any(op.get("path") == ["tempo"] for op in msg["ops"]):
                e.retempo(e.asked)
        elif t == "pads":
            e.pads = {int(k): v for k, v in msg["pads"].items()}
        elif t == "assign":
            e.assign(msg["tracks"])
            if self.on_assign is not None:
                self.on_assign()
        elif t == "transport":
            if msg.get("play"):
                e.play(msg["at"], msg.get("tick", 0.0))
                e.recording = bool(msg.get("record"))
            else:
                e.stop()
        elif t == "record":
            e.recording = bool(msg.get("on")) and e.transport["playing"]
        elif t == "select":
            e.selected = msg.get("track")
            e.armed = msg.get("armed", e.armed)
        elif t == "audition":
            v = e.voices.get(msg["track"])
            if v is not None:
                if msg.get("velocity"):
                    v.note_on(msg["pitch"], msg["velocity"])
                    if msg.get("seconds"):
                        e.pending.append((e.frame + int(msg["seconds"] * sr), msg["track"], msg["pitch"]))
                else:
                    v.note_off(msg["pitch"])
        elif t == "status":
            self.status(**(status_extra() if status_extra is not None else {}))


def drain_midi(midi, midi_in, host, key_frame):
    """Play every whole MIDI message adafruit_midi holds, with its timestamp.

    Read until the parser has no whole message left, not until the port is
    empty: receive() takes up to in_buf_size bytes at once, so after a late
    render the port can be empty with messages still in its buffer, and
    they'd wait for the next byte in (a pad playing the previous pad's note).
    ``key_frame(stamp)`` turns the port's timestamp into an audio frame.
    """
    if not midi_in.any() and not midi._in_buf:
        return
    while True:
        msg = midi.receive()
        if msg is None:
            return
        stamp = midi_in.take_timestamp()
        if getattr(msg, "_STATUS", None) is None:
            continue
        data = bytes(msg)
        if len(data) < 2 or data[0] >= 0xF0:
            continue
        d2 = data[2] if len(data) > 2 else 0
        host.live(data[0], data[1], d2, key_frame(stamp))
