"""The engine process as an hmilink device: it holds the song and makes the sound.

    python -m ostinato --role device [--port 9100] [--song FILE]

It runs the engines and the mixer (desktop/runners.py) and holds the song
(songlink.link, in studio's lib/). Panels connect on one port: the desktop UI
(``python -m ostinato --connect 127.0.0.1:9100``, or ``--split``, which
starts this process for you) and any browser, which gets the song page
(songlink's page.html, in studio's lib/) at http://<this machine>:9100/.

Every edit is a JSON Patch op a panel sends. The device numbers it, applies
it to the song and to the engines, and sends it to every panel. The engines'
reports (where the playhead is, the meters, the mixer's figures) go out as
telemetry, 30 times a second; everything else they say (a recorded take, an
export's audio, an instrument that couldn't be built) goes to the panels that
asked for it (the ``listen`` command), as ``engine`` events.

What a panel can ask with a command, besides ``load``:

    engine  {"i": n, "msg": {...}}    a message to engine n, as the runner sends it
    engines {"msg": {...}, "master": bool}
                                      to every engine (and the mixer)
    mixer   {"msg": {...}}            to the mixer (taps, master ops)
    play    {"tick": t}               start the song, a moment from now
    stop
    listen  {"me": n}                 send this panel the engines' own messages
"""

import binascii
import os
import time

import hmilink
from hmilink import tcp

import songlink
from songlink import model as song
from songlink.link import SongModel

SR = 48000
START_MARGIN_S = 0.08
STATUS_S = 0.03            # how often the engines are asked where they are
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = songlink.page()


class Swapper:
    """A planted fault for the checks: this link sends two ops in the wrong
    order once. A panel must notice (hmilink's order check) and resync."""

    def __init__(self, link):
        self.link = link
        self.held = None
        self.armed = True

    def __getattr__(self, name):
        return getattr(self.link, name)

    def send(self, frame):
        if self.armed and frame.startswith(b'{"t": "op"'):
            if self.held is None:
                self.held = frame
                return
            self.armed = False
            self.link.send(frame)
            frame, self.held = self.held, None
        self.link.send(frame)


class SongDevice:
    def __init__(self, runner, count, project=None, port=9100, bind="0.0.0.0", plant=None, log=print):
        self.runner = runner
        self.count = count
        self.log = log
        self.plant = plant
        self.model = SongModel(project, forward=self.forward, host=self)
        song.migrate(self.model.doc)
        self.device = hmilink.Device(self.model, telemetry_hz=30)
        self.listener = self.device.add(tcp.serve(port, bind=bind, page=self._page))
        self.port = port
        self.status = [None] * count
        self.mixer_status = None
        self.fresh = False           # a report came since the last telemetry
        self.playing = False
        self.listening = set()       # link numbers that get the engines' messages
        self.assignment = {}
        self._asked = 0.0
        self._links = 0
        self.stats = {"forwarded": 0, "events": 0}

    def _page(self):
        with open(PAGE, "rb") as f:
            return f.read()

    def start(self):
        """Give the engines the song and their tracks."""
        self.runner.broadcast({"t": "op", "op": {"op": "replace", "value": self.model.doc}})
        self.runner.mixer({"t": "op", "op": {"op": "replace", "value": self.model.doc}})
        self.assign()

    # -- the song to the engines --

    def forward(self, op):
        msg = {"t": "op", "op": op}
        self.runner.broadcast(msg)
        self.runner.mixer(msg)
        self.stats["forwarded"] += 1
        if op["op"] == "replace":
            self.assign()

    def assign(self):
        """Each track on the engine it adds least to: the UI's rule, so both agree."""
        load = [0.0] * self.count
        self.assignment = {}
        for t in self.model.doc["tracks"]:
            i = min(range(self.count), key=lambda k: load[k])
            self.assignment[t["id"]] = i
            load[i] += 1.0
        for i in range(self.count):
            mine = [tid for tid, e in self.assignment.items() if e == i]
            self.runner.send(i, {"t": "assign", "tracks": mine})

    # -- the loop --

    def poll(self):
        """Read the panels, then the engines; never blocks."""
        if self.plant in ("swap", "swap-page"):
            # "swap" plants it on the first window's link, "swap-page" on the first browser's.
            ws = self.plant == "swap-page"
            for i, link in enumerate(self.device.links):
                if not isinstance(link, Swapper) and getattr(link, "ws", None) is ws:
                    self.device.links[i] = Swapper(link)
                    self.plant = None
                    self.log("device: planted a swapped op on link %d" % link.hmi_id)
                    break
        self.device.poll()
        if len(self.device.links) != self._links:
            self._links = len(self.device.links)
            self.log("device: %d panel%s connected" % (self._links, "" if self._links == 1 else "s"))
        now = time.monotonic()
        if now - self._asked >= STATUS_S:
            self._asked = now
            self.runner.broadcast({"t": "status"})
            self.runner.mixer({"t": "stats"})
        for msg in self.runner.poll():
            self.route(msg)

    def route(self, msg):
        t = msg.get("t")
        if t == "status":
            self.status[msg.get("engine", 0)] = msg
            self.fresh = True
            return
        if t == "mixer":
            self.mixer_status = msg
            self.fresh = True
            return
        if t == "preloaded":
            self.model.samples[msg["track"]] = msg
            self.device.event("samples", cost=self.model.sample_cost())
        if t == "pcm":
            msg = dict(msg)
            msg["pcm64"] = binascii.b2a_base64(msg.pop("pcm")).decode().strip()
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

    # -- what the song asks of the host (songlink.link.SongModel) --

    def telemetry(self):
        if not self.fresh:
            return None
        self.fresh = False
        st0 = self.status[0]
        tick = None
        if st0 and st0.get("tick") is not None:
            tick = st0["tick"]
        return {"playing": self.playing, "tick": tick, "bar": None if tick is None else int(tick // song.BAR),
                "engines": self.status, "mixer": self.mixer_status}

    def now_frame(self):
        st = self.status[0]
        if not st:
            return None
        clock = st.get("clock")
        if clock and clock[1]:
            return int(clock[0] + (time.perf_counter_ns() - clock[1]) * SR // 1_000_000_000)
        return st.get("frame")

    def command(self, name, args):
        if name == "engine":
            self._watch(args["msg"])
            self.runner.send(int(args["i"]), args["msg"])
            return None
        if name == "engines":
            msg = args["msg"]
            self._watch(msg)
            self.runner.broadcast(msg)
            if args.get("master"):
                self.runner.mixer(msg)
            if msg.get("t") == "transport":
                return {"name": "transport", "playing": bool(msg.get("play")), "tick": msg.get("tick", 0.0),
                        "by": args.get("by")}
            return None
        if name == "mixer":
            self.runner.mixer(args["msg"])
            return None
        if name == "listen":
            self.listening.add(int(args["me"]))
            return None
        if name == "play":
            now = self.now_frame()
            if now is None:
                raise ValueError("the engines haven't reported yet")
            tick = float(args.get("tick", 0.0))
            msg = {"t": "transport", "play": True, "at": now + int(START_MARGIN_S * SR), "tick": tick,
                   "record": False}
            self.runner.broadcast(msg)
            self.playing = True
            return {"name": "transport", "playing": True, "tick": tick, "by": args.get("by")}
        if name == "stop":
            self.runner.broadcast({"t": "transport", "play": False})
            self.playing = False
            return {"name": "transport", "playing": False, "by": args.get("by")}
        raise ValueError("unknown command %r" % name)

    def _watch(self, msg):
        if isinstance(msg, dict) and msg.get("t") == "transport":
            self.playing = bool(msg.get("play"))

    def close(self):
        self.device.close()


def run(runner, count, port=9100, bind="0.0.0.0", song_path=None, plant=None, stop=None, parent=False):
    """Serve the song until ``stop()`` says so (or forever). ``parent``: the
    first panel is the window that started this process (--split), so quit
    when it goes, or when none has come in a minute."""
    import json

    project = None
    if song_path:
        with open(song_path) as f:
            project = json.load(f)
    dev = SongDevice(runner, count, project, port=port, bind=bind, plant=plant)
    dev.start()
    print("Ostinato device: the song on port %d (a browser opens http://127.0.0.1:%d/)" % (port, port), flush=True)
    first = None
    since = time.monotonic()
    try:
        while stop is None or not stop():
            dev.poll()
            if parent:
                ids = [link.hmi_id for link in dev.device.links]
                if first is None and ids:
                    first = ids[0]
                if (first is not None and first not in ids) or (first is None and time.monotonic() - since > 60):
                    print("Ostinato device: the window went; stopping", flush=True)
                    break
            time.sleep(0.002)
    finally:
        dev.close()
    return dev
