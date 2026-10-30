"""The UI's side of the engine workers.

The UI owns the project. Every edit is an op, applied here and sent to every
engine and to the master worker, so each copy stays the same. Tracks are spread over the
engines by their measured cost; each engine renders only its own.
"""

from js import Object, performance, ports
from pyodide.ffi import create_proxy, to_js

from songlink import model

SR = 48000
START_MARGIN_S = 0.08      # transport commands take effect this far ahead


class Engines:
    def __init__(self, count, project):
        self.count = count
        self.project = project
        self.status = [None] * count
        self.status_at = [0.0] * count
        self.listeners = []           # fn(msg) for every message from an engine
        self.on_change = []           # fn() after every op (autosave)
        self.assignment = {}          # track id -> engine index
        self.playing = False
        self.recording = False
        self._proxies = []
        self.ports = [getattr(ports, "engine%d" % i) for i in range(count)]
        for i, port in enumerate(self.ports):
            proxy = create_proxy(self._handler(i))
            self._proxies.append(proxy)
            port.onmessage = proxy
        self.master_status = None
        self.master_port = getattr(ports, "master", None)
        if self.master_port is not None:
            proxy = create_proxy(self._on_master)
            self._proxies.append(proxy)
            self.master_port.onmessage = proxy

    def _on_master(self, event):
        msg = event.data.to_py()
        if msg.get("t") == "master":
            self.master_status = msg

    def _handler(self, index):
        def on_message(event):
            msg = event.data.to_py()
            if msg.get("t") == "status":
                self.status[index] = msg
                self.status_at[index] = float(performance.now())
            for fn in self.listeners:
                fn(msg)
        return on_message

    def send(self, index, msg):
        self.flush()
        self.ports[index].postMessage(to_js(msg, dict_converter=Object.fromEntries))

    def broadcast(self, msg, master=False):
        self.flush()
        self._broadcast(msg, master)

    def flush(self):
        """Send the coalesced settings: each one once, at its latest value."""
        pending = getattr(self, "pending", None)
        if pending:
            ops = list(pending.values())
            pending.clear()
            self._broadcast({"t": "ops", "ops": ops}, True)

    def _broadcast(self, msg, master=False):
        js_msg = to_js(msg, dict_converter=Object.fromEntries)
        for port in self.ports:
            port.postMessage(js_msg)
        if master and self.master_port is not None:
            self.master_port.postMessage(js_msg)

    # -- the project ------------------------------------------------------------

    def op(self, op):
        model.apply(self.project, op)
        if op["op"] == "set" and not isinstance(op.get("value"), (dict, list)):
            # A knob or slider sends a setting many times a second: the engines
            # get each one once a frame (flush), at its latest value, instead of
            # rebuilding their graphs for every step of the turn.
            if getattr(self, "pending", None) is None:
                self.pending = {}
            self.pending[repr(op["path"])] = op
        else:
            self.broadcast({"t": "op", "op": op}, master=True)
        for fn in self.on_change:
            fn()

    def load(self, project):
        model.migrate(project)
        self.op({"op": "replace", "value": project})
        self.assign()
        self.loaded_at = float(performance.now())

    def settled(self):
        """For tests: every engine has reported since the last load, with
        all its tracks built (an engine building an instrument can't report)."""
        at = getattr(self, "loaded_at", 0.0)
        for i in range(self.count):
            st = self.status[i]
            mine = sum(1 for e in self.assignment.values() if e == i)
            if not st or self.status_at[i] <= at or st.get("voices") != mine:
                return False
        return True

    def assign(self):
        """Spread tracks over the engines: each track goes where it adds least."""
        load = [0.0] * self.count
        self.assignment = {}
        for t in self.project["tracks"]:
            i = min(range(self.count), key=lambda k: load[k])
            self.assignment[t["id"]] = i
            load[i] += 1.0
        for i in range(self.count):
            mine = [tid for tid, e in self.assignment.items() if e == i]
            self.send(i, {"t": "assign", "tracks": mine})
        self.send_pads()

    def send_pads(self):
        """Each drum track's sounds in grid order, so the keyboard's pads play
        the grid's rows (pad 1 the first) on any kit."""
        import catalog

        pads = {str(t["id"]): catalog.pad_notes(t) for t in self.project["tracks"] if t["kind"] == "drums"}
        self.broadcast({"t": "pads", "pads": pads})

    # -- time ---------------------------------------------------------------------

    def now_frame(self):
        """The audio context frame playing about now, from engine 0's reports."""
        st = self.status[0]
        if not st:
            return None
        elapsed = (float(performance.now()) - self.status_at[0]) / 1000.0
        return int(st["frame"] + elapsed * SR)

    def now_tick(self):
        st = self.status[0]
        if not st or st.get("tick") is None:
            return None
        elapsed = (float(performance.now()) - self.status_at[0]) / 1000.0
        return st["tick"] + elapsed * self.project["tempo"] * model.PPQ / 60.0

    def play(self, record=False, tick=0.0):
        now = self.now_frame()
        if now is None:
            return False
        at = now + int(START_MARGIN_S * SR)
        self.broadcast({"t": "transport", "play": True, "at": at, "tick": tick, "record": record})
        self.playing = True
        self.recording = record
        return True

    def stop(self):
        self.broadcast({"t": "transport", "play": False})
        self.playing = False
        self.recording = False

    def poll(self):
        self.broadcast({"t": "status"}, master=True)

    def meters(self):
        """Each track's and the master's peak levels since the last poll; a
        report is used once, so a slow one doesn't hold a meter up."""
        out = {}
        for i, st in enumerate(self.status):
            if st and st.get("meters") and not st.get("_metered"):
                for tid, lv in st["meters"].items():
                    out[int(tid)] = lv
                st["_metered"] = True
        m = self.master_status
        if m and m.get("meter") and not m.get("_metered"):
            out["master"] = m["meter"]
            m["_metered"] = True
        return out

    def master_underruns(self):
        return (self.master_status or {}).get("underruns", 0)

    def loads(self):
        return [s["load"] if s else None for s in self.status]

    def select(self, track_id, armed=None):
        self.broadcast({"t": "select", "track": track_id, "armed": armed})

    def audition(self, track_id, pitch, velocity, seconds=None):
        e = self.assignment.get(track_id)
        if e is not None:
            self.send(e, {"t": "audition", "track": track_id, "pitch": pitch, "velocity": velocity,
                          "seconds": seconds})
