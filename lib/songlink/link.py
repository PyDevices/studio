"""Ostinato's song as an hmilink model: any screen as the groovebox's front panel.

The project is already plain data changed by ops (:mod:`model`), so this is
a thin coat. The device holds the song; every panel (the desktop UI, the
browser page the device serves, later the LCD-7) sends JSON Patch ops with
JSON Pointer paths, and the device numbers them and sends them to every
panel, so two screens never disagree about the order.

    import hmilink
    from hmilink import tcp
    from songlink.link import SongModel

    song = SongModel(project, forward=engines_apply, host=the_host)
    device = hmilink.Device(song)
    device.add(tcp.serve(9100, page="page.html"))
    while True:
        ...                     # the sequencer's own loop
        device.poll()

Ops a panel sends, as pointers into the project:

- ``replace /tempo 120``, ``replace /tracks/0/volume 90``, ``replace /tracks/1/mute true``
- ``add /tracks/0/patterns/0/notes/- [0, 24, 60, 100]`` adds a note (``-`` appends)
- ``remove /tracks/0/patterns/0/notes/3``

Ostinato's own ops find a track by id (``["track", 7]``), so they survive a
reorder; a pointer finds it by index. :func:`to_pointer` turns one into the
other on the panel that sends it, against that panel's copy.

Imports on CPython and MicroPython: nothing here touches a file, a thread or
the audio.
"""

import hmilink

from songlink import model as song

RANGES = {"tempo": [40, 240], "swing": [0, 100], "volume": [0, 127], "pan": [0, 127]}


def to_pointer(project, op):
    """An Ostinato op as ``(kind, pointer, value)`` for ``Panel.send``.

    ``set`` is ``replace`` (``add`` when the key is new), ``insert`` is
    ``add`` (index -1 is ``-``), ``remove`` is ``remove``. A ``replace`` of
    the whole project isn't an op on the wire: it's the ``load`` command.
    """
    keys = []
    node = project
    path = op["path"]
    for i, key in enumerate(path):
        if isinstance(key, (list, tuple)) and key[0] == "track":
            tracks = project["tracks"]
            index = tracks.index(song.find_track(project, key[1]))
            keys += ["tracks", index]
            node = tracks[index]
            continue
        last = i == len(path) - 1
        if last and op["op"] == "insert" and key == -1:
            keys.append("-")
            break
        keys.append(key)
        if not last:
            node = node[key]
    pointer = hmilink.join_pointer(keys)
    kind = op["op"]
    if kind == "insert":
        return "add", pointer, op["value"]
    if kind == "remove":
        return "remove", pointer, None
    if kind == "set":
        last = keys[-1]
        exists = isinstance(node, list) or last in node
        return ("replace" if exists else "add"), pointer, op["value"]
    raise ValueError("no pointer form for %r" % kind)


def from_pointer(op):
    """A JSON Patch op as the Ostinato op :func:`model.apply` takes."""
    keys = hmilink.split_pointer(op["path"])
    if not keys:
        raise ValueError("replace the whole song with the load command")
    path = []
    for k in keys:
        if k == "-":
            path.append(-1)
        elif k.isdigit():
            path.append(int(k))
        else:
            path.append(k)
    if op["op"] == "remove":
        return {"op": "remove", "path": path}
    if op["op"] == "add" and isinstance(path[-1], int):
        return {"op": "insert", "path": path, "value": op["value"]}
    return {"op": "set", "path": path, "value": op["value"]}


class SongModel(hmilink.Model):
    """The song a device holds.

    ``forward(op)`` gets every Ostinato op the song accepts, to apply to the
    sound (the engines). ``host`` answers what the song alone can't:
    ``host.command(name, args)`` for the transport and the engines,
    ``host.telemetry()`` for the playhead and meters.
    """

    def __init__(self, project=None, forward=None, host=None):
        super().__init__(project if project is not None else song.new_project())
        self.forward = forward
        self.host = host
        #: What the song's SoundFont tracks cost: track id -> what the engine
        #: preloaded, and the totals (see describe).
        self.samples = {}
        #: The panel and number of the load the document came from, so a
        #: panel can tell its own load's echo from someone else's.
        self.loaded = None

    def describe(self):
        tracks = [{"id": t["id"], "name": t["name"], "kind": t["kind"],
                   "instrument": t["instrument"], "patch": t.get("patch", 0)} for t in self.doc["tracks"]]
        return {
            "kind": "ostinato",
            "version": song.VERSION,
            "ppq": song.PPQ,
            "step": song.STEP,
            "bar": song.BAR,
            "tracks": tracks,
            "ranges": RANGES,
            "note": ["start", "length", "pitch", "velocity"],
            "samples": self.sample_cost(),
            "loaded": self.loaded,
        }

    def sample_cost(self):
        """The preloaded samples, per SoundFont track and in all, against the arena."""
        tracks = {}
        total = 0
        arena = 0
        for tid, s in self.samples.items():
            tracks[str(tid)] = {"samples": s.get("samples", 0), "bytes": s.get("bytes", 0)}
            total += s.get("bytes", 0)
            arena = max(arena, s.get("arena", 0))
        return {"tracks": tracks, "bytes": total, "arena": arena}

    def apply(self, op):
        ours = from_pointer(op)
        if ours["path"] == ["tempo"]:
            ours["value"] = max(40.0, min(240.0, float(ours["value"])))
        kind = op["op"]
        if ours["op"] == "set":
            # Say what happened, so a panel's own JSON Patch apply agrees:
            # a new key is an add, an existing one a replace.
            node, key = song.resolve(self.doc, ours["path"])
            kind = "replace" if isinstance(node, list) or key in node else "add"
        song.apply(self.doc, ours)
        if self.forward is not None:
            self.forward(ours)
        out = {"op": kind, "path": op["path"]}
        if op["op"] != "remove":
            out["value"] = ours["value"]
        return out

    def load(self, project, by=None):
        """Replace the whole song (a panel's New or Open) and tell every panel."""
        song.migrate(project)
        song.apply(self.doc, {"op": "replace", "value": project})
        song.migrate(self.doc)
        self.samples = {}
        self.loaded = by
        if self.forward is not None:
            self.forward({"op": "replace", "value": self.doc})
        device = getattr(self, "device", None)
        if device is not None:
            device.reset_doc()

    def telemetry(self):
        return self.host.telemetry() if self.host is not None else None

    def command(self, name, args):
        if name == "load":
            self.load(args["project"], args.get("by"))
            return {"name": "loaded", "by": args.get("by")}
        if self.host is not None:
            return self.host.command(name, args)
        raise ValueError("unknown command %r" % name)
