"""The box as an hmilink device: it holds the song and makes the sound, in one
MicroPython process.

The desktop device (desktop/device.py) runs engines in processes of their
own and passes messages; this one has one engine (boxengine.py) and calls it.
To a panel the two look the same: the desktop window (``python -m ostinato
--connect BOX:9100``) and the browser page the box serves at
``http://BOX:9100/`` edit the song with JSON Patch ops, the box numbers and
applies them, and its telemetry says where the playhead is.

The window was written for four engines, so the box answers as four: each
"engine" reports the tracks the window's own rule puts on it, and every
engine message, whichever engine it's for, goes to the one engine.

Two things differ from the desktop device. A window's ``transport`` carries
the frame to start on, worked out from its own clock; across two machines
that number means nothing, so the box starts a margin after its own now.
And the box's status says ``clock: None``, so the window counts from the
report's arrival instead.

Runs on MicroPython and CPython.
"""

import struct
import time

import hmilink
from hmilink import tcp

import boxengine as be
import model as song
from hmilink_model import SongModel

START_MARGIN = be.SR * 3 // 20    # 150 ms: the transport starts this long after it's asked
STATUS_MS = 33


def _ms():
    return be.ticks_ms()


class BoxDevice:
    def __init__(self, engine, project=None, count=4, port=9100, bind="0.0.0.0", page=None, pump=None, log=print,
                 plant=None):
        self.engine = engine
        self.plant = plant                     # "nomute": a planted fault, mutes are echoed and never applied
        self.count = count
        self.log = log
        self.pump = pump                       # audiodev.pump.owner(), for its figures
        self.model = SongModel(project, forward=self.forward, host=self)
        song.migrate(self.model.doc)
        if plant == "nomute":
            apply = self.model.apply

            def dropped(op):
                if op["path"].endswith("/mute"):
                    return {"op": "replace", "path": op["path"], "value": op.get("value")}
                return apply(op)

            self.model.apply = dropped
        engine.project = self.model.doc
        engine.post = self.from_engine
        self.device = hmilink.Device(self.model, telemetry_hz=30)
        self.listener = self.device.add(tcp.serve(port, bind=bind, page=page))
        self.port = port
        self.playing = False
        self.listening = set()
        self.assignment = {}
        self._links = 0
        self._status_at = _ms()
        self.fresh = False
        self.stats = {"ops": 0, "commands": 0, "events": 0}
        self.edit_us_max = 0
        engine.apply({"op": "replace", "value": self.model.doc}, applied=True)
        self.assign()

    # -- the song to the engine --

    def forward(self, op):
        t = be.ticks_us()
        self.engine.apply(op, applied=True)
        us = be.ticks_diff(be.ticks_us(), t)
        if us > self.edit_us_max:
            self.edit_us_max = us
        self.stats["ops"] += 1
        if op["op"] == "replace":
            self.assign()
            self.model.samples = {}

    def assign(self):
        """The window's rule (desktop/ui/engines.py), so its engine count agrees."""
        load = [0.0] * self.count
        self.assignment = {}
        for t in self.model.doc["tracks"]:
            i = 0
            for k in range(1, self.count):
                if load[k] < load[i]:
                    i = k
            self.assignment[t["id"]] = i
            load[i] += 1.0

    def from_engine(self, msg):
        t = msg.get("t")
        if t == "preloaded":
            self.model.samples[msg["track"]] = msg
            self.device.event("samples", cost=self.model.sample_cost())
        self.to_listeners({"t": "ev", "name": "engine", "msg": msg})

    def to_listeners(self, msg):
        if not self.listening:
            return
        frame = self.device.codec.encode(msg)
        for link in self.device.links:
            if link.hmi_id in self.listening:
                try:
                    link.send(frame)
                except OSError:
                    link.close()
        self.stats["events"] += 1

    # -- the loop --

    def poll(self):
        self.device.poll()
        n = len(self.device.links)
        if n != self._links:
            self._links = n
            self.log("box: %d panel%s connected" % (n, "" if n == 1 else "s"))
        if be.ticks_diff(_ms(), self._status_at) >= STATUS_MS:
            self._status_at = _ms()
            self.fresh = True

    def midi(self, status, d1, d2):
        """A message from a keyboard on the box: it plays here, and the panels see it."""
        kind = status & 0xF0
        if kind in (0x80, 0x90, 0xE0) or (kind == 0xB0 and d1 in (1, 64)):
            self.engine.live(status, d1, d2)
        self.to_listeners({"t": "ev", "name": "engine", "msg": {"t": "midi", "d": [status, d1, d2]}})

    # -- what the song asks of the host (SongModel) --

    def pump_figures(self):
        """The pump's own numbers: blocks, mean and worst block time, underruns."""
        out = {"blocks": 0, "pull_us": 0, "pull_us_max": 0, "underruns": 0}
        p = self.pump
        if p is None:
            return out
        try:
            import audiopump

            w = struct.unpack("<%dQ" % audiopump.STATUS_WORDS, p.status())
            out["blocks"] = w[0]
            out["pull_us"] = w[12] // w[0] if w[0] else 0
            out["pull_us_max"] = w[20]
            d = p._driver
            starved = getattr(d, "starved", None)
            if starved is not None:
                out["underruns"] = starved()
            else:
                out["underruns"] = p.health().get("dropped", 0)
        except Exception:  # noqa: BLE001  (figures never stop the music)
            pass
        return out

    def telemetry(self):
        if not self.fresh:
            return None
        self.fresh = False
        eng = self.engine
        now = eng.clock()
        tick = eng.tick_at(now) if eng.transport["playing"] else None
        if tick is not None and tick < 0:
            tick = 0.0
        figures = self.pump_figures()
        load = figures["pull_us"] / (be.BLOCK * 1000000.0 / be.SR)
        engines = []
        for i in range(self.count):
            mine = 0
            for e in self.assignment.values():
                if e == i:
                    mine += 1
            engines.append({"t": "status", "engine": i, "voices": mine, "tick": tick, "frame": now,
                            "clock": None, "load": load, "meters": {}})
        st = eng.stats
        mixer = {"t": "mixer", "underruns": figures["underruns"], "meter": None, "box": {
            "pump": figures, "late": eng.pool.late, "late_max": eng.pool.late_max, "notes": st["notes"],
            "sounding": eng.sounding(), "gc": st["gc"], "gc_ms_max": st["gc_ms_max"],
            "pass_us_max": st["pass_us_max"], "edit_us_max": self.edit_us_max, "live": st["live"],
            "live_us_max": st["live_us_max"], "dropped_frames": st["dropped_frames"]}}
        return {"playing": self.playing, "tick": tick, "bar": None if tick is None else int(tick // song.BAR),
                "engines": engines, "mixer": mixer}

    def command(self, name, args):
        self.stats["commands"] += 1
        if name in ("engine", "engines"):
            reply = self.engine_message(args["msg"], args.get("by"))
            return reply
        if name == "mixer":
            return None                     # taps and meters: the box has none yet
        if name == "listen":
            self.listening.add(int(args["me"]))
            return None
        if name == "play":
            return self.transport(True, float(args.get("tick", 0.0)), args.get("by"))
        if name == "stop":
            return self.transport(False, 0.0, args.get("by"))
        if name == "figures":
            return {"name": "figures", "figures": self.telemetry_now()}
        raise ValueError("unknown command %r" % name)

    def telemetry_now(self):
        self.fresh = True
        return self.telemetry()

    def transport(self, play, tick, by):
        eng = self.engine
        if play:
            eng.play(eng.clock() + START_MARGIN, tick)
            eng.sequence()                 # the first notes now, not after this pass's other messages
        else:
            eng.stop()
        self.playing = play
        return {"name": "transport", "playing": play, "tick": tick, "by": by}

    def engine_message(self, msg, by=None):
        t = msg.get("t")
        eng = self.engine
        if t == "transport":
            if msg.get("record"):
                self.from_engine({"t": "failed", "why": "The box doesn't record yet; playing instead"})
            return self.transport(bool(msg.get("play")), float(msg.get("tick", 0.0)), by)
        if t == "pads":
            eng.pads = {}
            for k, v in msg.get("pads", {}).items():
                eng.pads[int(k)] = v
        elif t == "select":
            if msg.get("track") is not None:
                eng.selected = msg["track"]
        elif t == "audition":
            eng.audition(msg["track"], msg["pitch"], msg["velocity"], msg.get("seconds"))
        elif t == "render":
            self.from_engine({"t": "failed", "why": "Export from the box isn't there yet"})
        # assign, status, ops: the box has its own; the rest has no meaning here.
        return None

    def close(self):
        self.device.close()
