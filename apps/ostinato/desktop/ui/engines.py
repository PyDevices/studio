"""The UI's side of the engines on the desktop: app/ui/engines.py's job, over
a runner (desktop/runners.py) instead of the page's MessagePorts.

The same interface as the browser version, so the shared UI runs unchanged.

With a linked runner (desktop/linkrunner.py) the engines and the song belong
to another process, an hmilink device, and this UI is one of its panels:
edits go to the device as JSON Patch ops, and another panel's edits arrive
already applied to the project, after which ``on_remote`` callbacks redraw.
"""

import time

import model


def to_pointer(project, op):
    from hmilink_model import to_pointer as convert     # only a linked UI needs hmilink

    return convert(project, op)

SR = 48000
START_MARGIN_S = 0.08
runner = None              # set before the UI starts (ostinato/__main__.py, desktop/app.py)
current = None             # the Engines the UI made (desktop/ui/page.py exports through it)


def now_ms():
    return time.perf_counter() * 1000.0


class Engines:
    def __init__(self, count, project):
        global current
        current = self
        self.count = count
        self.project = project
        self.status = [None] * count
        self.status_at = [0.0] * count
        self.listeners = []
        self.on_change = []
        self.assignment = {}
        self.playing = False
        self.recording = False
        self.master_status = None
        self.pending = None
        self.on_remote = []          # fn(msg) after another panel changed the song or the transport
        self.samples = None          # what the song's SoundFont samples cost (a linked device says)
        if getattr(runner, "linked", False):
            doc = runner.panel.doc if getattr(runner, "adopt", False) and runner.panel is not None else None
            if doc:
                # A window joining a device that already has a song (--connect)
                # takes that song, rather than putting its own last session over it.
                model.apply(project, {"op": "replace", "value": doc})
                model.migrate(project)
            runner.attach(project)
            if doc:
                self.assign()

    def drain(self):
        if self.pending and getattr(runner, "linked", False):
            # Our edits out before another panel's come in: the panel can
            # only reconcile ops it knows are in flight.
            self.flush()
        remote = []
        for msg in runner.poll():
            t = msg.get("t")
            if t == "mixer":
                self.master_status = msg
                continue
            if t == "remote":
                if "playing" in msg:
                    self.playing = bool(msg["playing"])
                    if not self.playing:
                        self.recording = False
                remote.append(msg)
                continue
            if t == "samples":
                self.samples = msg.get("cost")
                continue
            if t == "status":
                i = msg.get("engine", 0)
                self.status[i] = msg
                self.status_at[i] = now_ms()
            for fn in self.listeners:
                fn(msg)
        if remote:
            for fn in self.on_remote:
                fn(remote)

    def send(self, index, msg):
        self.flush()
        msg = dict(msg)
        runner.send(index, msg)

    def broadcast(self, msg, master=False):
        self.flush()
        self._broadcast(msg, master)

    def flush(self):
        if self.pending:
            ops = list(self.pending.values())
            self.pending.clear()
            if getattr(runner, "linked", False):
                for op in ops:
                    runner.send_op(*to_pointer(self.project, op))
                return
            self._broadcast({"t": "ops", "ops": ops}, True)

    def _broadcast(self, msg, master=False):
        runner.broadcast(msg)
        if master:
            runner.mixer(msg)

    def op(self, op):
        linked = getattr(runner, "linked", False)
        scalar = op["op"] == "set" and not isinstance(op.get("value"), (dict, list))
        wire = None
        if linked and not scalar and op["op"] != "replace":
            # Where the op lands, found before it moves anything (a remove).
            wire = to_pointer(self.project, op)
        model.apply(self.project, op)
        if scalar:
            if self.pending is None:
                self.pending = {}
            self.pending[repr(op["path"])] = op
        elif linked:
            self.flush()
            if wire is None:
                runner.load(self.project)
            else:
                runner.send_op(*wire)
        else:
            self.broadcast({"t": "op", "op": op}, master=True)
        for fn in self.on_change:
            fn()

    def load(self, project):
        model.migrate(project)
        self.op({"op": "replace", "value": project})
        self.assign()
        self.loaded_at = now_ms()

    def settled(self):
        at = getattr(self, "loaded_at", 0.0)
        for i in range(self.count):
            st = self.status[i]
            mine = sum(1 for e in self.assignment.values() if e == i)
            if not st or self.status_at[i] <= at or st.get("voices") != mine:
                return False
        return True

    def assign(self):
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
        import catalog

        pads = {str(t["id"]): catalog.pad_notes(t) for t in self.project["tracks"] if t["kind"] == "drums"}
        self.broadcast({"t": "pads", "pads": pads})

    def now_frame(self):
        """The context frame playing now, from the mixer's clock in engine 0's
        last report (frame, perf_counter_ns: one clock in every process), so
        a report that waited in a pipe while the UI was busy doesn't make the
        transport start late."""
        st = self.status[0]
        if not st:
            return None
        clock = st.get("clock")
        if clock and clock[1]:
            return int(clock[0] + (time.perf_counter_ns() - clock[1]) * SR // 1_000_000_000)
        return int(st["frame"] + (now_ms() - self.status_at[0]) / 1000.0 * SR)

    def now_tick(self):
        st = self.status[0]
        if not st or st.get("tick") is None:
            return None
        frames = self.now_frame() - st["frame"]
        return st["tick"] + frames / SR * self.project["tempo"] * model.PPQ / 60.0

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
        self.drain()
        self.broadcast({"t": "status"})
        runner.mixer({"t": "stats"})

    def meters(self):
        out = {}
        for st in self.status:
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
