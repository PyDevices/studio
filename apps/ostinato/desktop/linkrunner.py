"""The UI's runner when the engines are another process's: an hmilink panel.

desktop/runners.py's runners host the engines and the mixer in this process
(or its children). This one has the same face, but the engines belong to an
hmilink device (desktop/device.py) the UI reaches over TCP, so the UI is one
panel of several: a browser on the device's page is another.

    runner = LinkRunner("127.0.0.1", 9100)
    runner.start()                    connected, the song arrived
    runner.send_op(kind, ptr, value)  an edit, as a JSON Patch op the device numbers
    runner.load(project)              a whole new song (the ``load`` command)
    runner.send(i, msg) / broadcast(msg) / mixer(msg)
                                      the engine messages the UI sends today
    runner.poll()                     what came back: the engines' reports and
                                      messages, and {"t": "remote"} after another
                                      panel's edit
    runner.stop()

The UI keeps editing its own project (``model.apply``) and the panel mirrors
each op (``Panel.send(..., local=False)``); another panel's op is applied to
the same project by the panel, in the device's order.
"""

import binascii
import time

import hmilink
from hmilink import tcp

import model
from hmilink_model import to_pointer


class LinkRunner:
    linked = True
    adopt = False                     # the UI takes the device's song (--connect) instead of loading its own

    def __init__(self, host, port, proc=None, connect_s=60.0):
        self.host = host
        self.port = port
        self.proc = proc              # the device process, when this UI started it (--split)
        self.connect_s = connect_s
        self.panel = None
        self.project = None
        self.out = []                 # messages for the UI's next poll
        self.tokens = []              # this panel's loads not yet echoed
        self._token = 0
        self.stats = {"remote_ops": 0, "remote_docs": 0, "order": 0}

    # -- the runner's face --

    def start(self):
        end = time.monotonic() + self.connect_s
        while True:
            try:
                link = tcp.connect(self.host, self.port)
                break
            except OSError:
                if time.monotonic() > end:
                    raise
                if self.proc is not None and self.proc.poll() is not None:
                    raise RuntimeError("the device process ended (exit %s)" % self.proc.returncode)
                time.sleep(0.2)
        p = hmilink.Panel(link)
        p.on_doc = self._on_doc
        p.on_op = self._on_op
        p.on_tele = self._on_tele
        p.on_event = self._on_event
        p.on_error = self._on_error
        self.panel = p
        while not p.ready:
            p.poll()
            if p.closed:
                raise RuntimeError("the device closed the link")
            if time.monotonic() > end:
                raise TimeoutError("no song from the device")
            time.sleep(0.005)
        p.command("listen", me=p.me)

    def attach(self, project):
        """The UI's project: what the panel keeps in step from now on."""
        self.project = project
        if self.panel is not None and not self.tokens:
            self.panel.doc = project

    def send_op(self, kind, pointer, value=None):
        """One of the UI's edits, already applied to its project, to the device."""
        self.panel.send(kind, pointer, value, local=False)

    def load(self, project):
        self._token += 1
        self.tokens.append(self._token)
        self.panel.command("load", project=project, by=[self.panel.me, self._token])

    def send(self, i, msg):
        self.panel.command("engine", i=i, msg=msg)

    def broadcast(self, msg):
        if msg.get("t") == "status":
            return                    # the device asks for reports itself and sends them on
        self.panel.command("engines", msg=msg, by=self.panel.me)

    def mixer(self, msg):
        t = msg.get("t")
        if t == "stats":
            return
        if t in ("op", "ops"):
            return                    # the device gives the mixer every op it accepts
        self.panel.command("mixer", msg=msg)

    def midi(self, status, d1, d2, ns):
        # The device's mixer process reads MIDI itself; nothing comes this way.
        pass

    def poll(self):
        p = self.panel
        p.poll()
        out, self.out = self.out, []
        return out

    def stop(self):
        if self.panel is not None:
            self.panel.close()
        if self.proc is not None:
            # The device stops its engines and quits when our link closes
            # (--parent); end it only if it doesn't.
            try:
                self.proc.wait(8)
            except Exception:  # noqa: BLE001
                self.proc.kill()

    # -- what the device says --

    def _on_doc(self, doc, desc):
        by = (desc or {}).get("loaded")
        mine = by is not None and by[0] == self.panel.me and by[1] in self.tokens
        if mine:
            n = by[1]
            self.tokens = [t for t in self.tokens if t > n]
        project = self.project
        if project is None:
            return                    # before the UI has a project: it loads its own next
        if mine or self.tokens:
            # Our own load coming back, or someone's from before ours, which
            # ours replaces on the device too: the UI's project stands.
            self.panel.doc = project
            return
        # Another panel's song, or the device's after a resync: take it.
        model.apply(project, {"op": "replace", "value": doc})
        model.migrate(project)
        self.panel.doc = project
        self.stats["remote_docs"] += 1
        self.out.append({"t": "remote", "doc": True})

    def _on_op(self, msg):
        if msg.get("from") == self.panel.me and not msg.get("restored"):
            return
        self.stats["remote_ops"] += 1
        self.out.append({"t": "remote", "path": msg.get("path")})

    def _on_tele(self, v):
        for st in v.get("engines") or []:
            if st:
                self.out.append(st)
        if v.get("mixer"):
            self.out.append(v["mixer"])

    def _on_event(self, ev):
        name = ev.get("name")
        if name == "engine":
            msg = ev["msg"]
            if "pcm64" in msg:
                msg["pcm"] = binascii.a2b_base64(msg.pop("pcm64"))
            self.out.append(msg)
        elif name == "transport" and ev.get("by") != self.panel.me:
            self.out.append({"t": "remote", "playing": ev.get("playing"), "tick": ev.get("tick", 0.0)})
        elif name == "samples":
            self.out.append({"t": "samples", "cost": ev.get("cost")})

    def _on_error(self, msg):
        if msg.get("t") == "order":
            self.stats["order"] += 1
            print("link: op %s arrived after %s; taking the song again" % (msg.get("seq"), msg.get("after")),
                  flush=True)
        else:
            print("link: the device refused:", msg.get("msg"), flush=True)
