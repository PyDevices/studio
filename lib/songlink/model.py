"""The song: plain data, shared by every panel and the engine that plays it.

Ostinato's UI and engines use it, and so does Vamp.

A project is dicts, lists, numbers and strings only, so it saves as JSON and
crosses between workers as a message. Neither side edits it in place behind
the other's back: every change is an op (:func:`apply`), applied locally and
sent to the other side, which applies the same op to its copy.

Times are in ticks, PPQ to a quarter note. A step on the grid is a
sixteenth, STEP ticks.
"""

VERSION = 1
PPQ = 96
STEP = PPQ // 4
BEATS_PER_BAR = 4
BAR = PPQ * BEATS_PER_BAR

_next_id = [1]


def new_id():
    n = _next_id[0]
    _next_id[0] += 1
    return n


def pattern(bars=1):
    return {"bars": bars, "notes": []}   # notes: [start, length, pitch, velocity]


def track(kind, instrument, name=None, patch=0):
    return {
        "id": new_id(),
        "name": name or instrument,
        "kind": kind,                  # "drums" or "melodic"
        "instrument": instrument,
        "patch": patch,
        "macros": {},                  # str(index) -> value, over the patch
        "volume": 100,                 # MIDI units, 100 = unity-ish
        "pan": 64,                     # 0 left, 64 centre, 127 right
        "mute": False,
        "solo": False,
        "rack": [],                    # [{"name": ..., "bypass": False, "macros": {}}]
        "patterns": [pattern()],
        # Which pattern plays in pattern mode: "current", from tick
        # "current_at" (it starts from its beginning there); "next" is a switch
        # waiting for the next bar line, {"pattern": i, "at": tick}, or None.
        "current": 0,
        "current_at": 0,
        "next": None,
    }


def new_project():
    return {
        "version": VERSION,
        "name": "Untitled",
        "tempo": 110.0,
        "swing": 0,                    # percent, applied to off-beat sixteenths
        "metronome": False,
        "quantise": True,              # recorded notes snap to sixteenths
        "overdub": True,               # takes add to a pattern; off, a take replaces it
        "loop_pattern": 0,             # old projects' one pattern for every track (see migrate)
        "song": [],                    # [{"patterns": {track_id: index or -1}, "repeat": n}]
        "song_mode": False,
        "loop_scene": None,            # a scene index to loop in song mode, or None
        "master": {"volume": 100, "rack": []},
        "tracks": [
            track("drums", "rhythm808", "Drums"),
            track("melodic", "acid303", "Bass"),
            track("melodic", "poly106", "Keys"),
            track("melodic", "mono_synth", "Lead"),
        ],
        "bindings": {},                # "ch:cc" -> target path, from MIDI-learn
    }


def swung(tick, swing):
    """Where a note at ``tick`` plays with ``swing``: sixteenths that fall
    off the beat's eighths are late by ``swing`` percent of a sixteenth."""
    return tick + swing * STEP / 100 if tick % (2 * STEP) == STEP else tick


def migrate(project):
    """Bring an older project up to date in place: each track's own pattern
    (they all played the one "loop_pattern" before) and no pending switch."""
    for t in project.get("tracks", []):
        if "current" not in t:
            t["current"] = min(project.get("loop_pattern", 0), len(t["patterns"]) - 1)
        t.setdefault("current_at", 0)
        t.setdefault("next", None)
    return project


def pattern_at(track, tick):
    """The pattern a track plays at ``tick`` in pattern mode, and the tick its
    start falls on: the pending switch once its bar line has passed."""
    n = track.get("next")
    if n and tick >= n["at"]:
        return n["pattern"], n["at"]
    return track.get("current", 0), track.get("current_at", 0)


def segments(project, t0, t1):
    """What plays between ticks t0 and t1, as (from, to, base, patterns):
    patterns maps track id -> pattern index (or -1 for silence), and a
    pattern's tick 0 falls at ``base``. Pattern mode is one endless segment
    per pending switch: each track plays its own pattern, and a switch splits
    the range at its bar line, its value (pattern, the tick it starts on).
    Song mode walks the scenes, looping the song, or loops the one scene
    ``loop_scene`` names."""
    p = project
    song = p.get("song") or []
    loop = p.get("loop_scene")
    if loop is not None and 0 <= loop < len(song):
        song = [song[loop]]
    if not (p.get("song_mode") and song):
        cuts = sorted({t["next"]["at"] for t in p["tracks"] if t.get("next") and t0 < t["next"]["at"] < t1})
        edges = [t0] + cuts + [t1]
        out = []
        for i in range(len(edges) - 1):
            a = edges[i]
            out.append((a, edges[i + 1], 0, {t["id"]: pattern_at(t, a) for t in p["tracks"]}))
        return out
    lengths = []
    for scene in song:
        bars = 1
        for t in p["tracks"]:
            i = scene["patterns"].get(str(t["id"]), -1)
            if 0 <= i < len(t["patterns"]):
                bars = max(bars, t["patterns"][i]["bars"])
        lengths.append(bars * BAR * max(1, scene.get("repeat", 1)))
    total = sum(lengths)
    out = []
    loop = int(max(t0, 0) // total)
    while True:
        start = loop * total
        for k in range(len(song)):
            scene, length = song[k], lengths[k]
            a, b = start, start + length
            start = b
            if b <= t0:
                continue
            if a >= t1:
                return out
            pats = {t["id"]: scene["patterns"].get(str(t["id"]), -1) for t in p["tracks"]}
            out.append((max(a, t0), min(b, t1), a, pats))
        loop += 1


def shown_pattern(track):
    """The pattern to show and edit for a track: where it's going, if switching."""
    n = track.get("next")
    return n["pattern"] if n else track.get("current", 0)


def find_track(project, track_id):
    for t in project["tracks"]:
        if t["id"] == track_id:
            return t
    return None


def resolve(project, path):
    """The container and key a path ends in. A path step ``("track", id)``
    finds a track by id, so ops survive tracks being reordered."""
    node = project
    for i, key in enumerate(path[:-1]):
        if isinstance(key, list) and key[0] == "track":
            node = find_track(project, key[1])
        else:
            node = node[key]
        if node is None:
            raise KeyError(path)
    last = path[-1]
    if isinstance(last, list) and last[0] == "track":
        tracks = project["tracks"]
        return tracks, tracks.index(find_track(project, last[1]))
    return node, last


def apply(project, op):
    """Apply one op to ``project`` and return it. Ops:

    ``{"op": "set", "path": [...], "value": v}``
    ``{"op": "insert", "path": [..., index], "value": v}``   (index -1 appends)
    ``{"op": "remove", "path": [..., index]}``
    ``{"op": "replace", "value": project}``                  (load a whole project)
    """
    kind = op["op"]
    if kind == "replace":
        value = op["value"]
        if value is project:
            _bump_ids(project)
            return project
        value = _copy(value)
        project.clear()
        project.update(value)
        _bump_ids(project)
        return project
    node, key = resolve(project, op["path"])
    if kind == "set":
        node[key] = op["value"]
    elif kind == "insert":
        if key == -1 or key >= len(node):
            node.append(op["value"])
        else:
            node.insert(key, op["value"])
    elif kind == "remove":
        del node[key]
    else:
        raise ValueError("unknown op %r" % kind)
    return project


def _copy(value):
    """A deep copy of plain data (dicts, lists, scalars)."""
    if isinstance(value, dict):
        return {k: _copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_copy(v) for v in value]
    return value


def _bump_ids(project):
    top = max([t["id"] for t in project.get("tracks", [])] + [0])
    if top >= _next_id[0]:
        _next_id[0] = top + 1


def track_path(track_id, *rest):
    return [["track", track_id]] + list(rest)
