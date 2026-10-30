"""Turn a SoundHelix song (Export.java's JSON) into an Ostinato project.

SoundHelix writes seven parts; Ostinato has four tracks, so parts share a
track (PARTS). The song is cut into four-bar chunks. Each track's distinct
chunks become its patterns, and runs of identical chunks across all tracks
become scenes with a repeat count. A track holds eight patterns (A to H): if
a track has more distinct chunks, the rarest are replaced by the kept
pattern closest to them, and the script says how many bars that changed.

SoundHelix's groove (a long-short pair of sixteenths) becomes the project's
swing.

    python3 scripts/soundhelix_project.py SONG.json PROJECT.json
"""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "app" / "shared"))
import model  # noqa: E402

CHUNK_BARS = 4
MAX_PATTERNS = 8
MAX_REPEAT = 16

# Ostinato track -> (instrument, volume, SoundHelix parts). The instruments
# have the voices their parts need at once: accomp and pad reach six notes,
# and the arpeggio and the melody overlap. The volumes keep the loudest
# moment of the mix under full scale, with the lead a little forward.
PARTS = [
    ("Drums", "drums", "tr808", 65, ["percussion"]),
    ("Bass", "melodic", "virus", 58, ["bass", "randombass"]),
    ("Keys", "melodic", "juno106", 60, ["accomp", "pad"]),
    ("Lead", "melodic", "dx7", 78, ["arpeggio", "melody"]),
]


def chunks(song, parts, scale, chunk_ticks):
    """Chunk index -> sorted note tuple, in Ostinato ticks from the chunk's start."""
    by_part = {t["instrument"]: t["notes"] for t in song["tracks"]}
    out = {}
    for part in parts:
        for start, length, pitch, velocity in by_part.get(part, []):
            k, at = divmod(start, chunk_ticks)
            out.setdefault(k, set()).add((at * scale, length * scale, pitch, velocity))
    return {k: tuple(sorted(v)) for k, v in out.items()}


def similarity(a, b):
    a, b = set(a), set(b)
    return len(a & b) / max(1, len(a | b))


def fit_patterns(seq, name):
    """Map each chunk to a pattern index (-1 for silence), eight at most."""
    counts, first = {}, {}
    for i, c in enumerate(seq):
        if c:
            counts[c] = counts.get(c, 0) + 1
            first.setdefault(c, i)
    keep = sorted(counts, key=lambda c: (-counts[c], first[c]))[:MAX_PATTERNS]
    keep.sort(key=lambda c: first[c])
    index = {c: i for i, c in enumerate(keep)}
    out, changed = [], 0
    for c in seq:
        if not c:
            out.append(-1)
        elif c in index:
            out.append(index[c])
        else:
            out.append(max(range(len(keep)), key=lambda i: similarity(c, keep[i])))
            changed += CHUNK_BARS
    if changed:
        print("%s: %d distinct chunks, %d bars use their nearest pattern" % (name, len(counts), changed),
              file=sys.stderr)
    return keep, out


def convert(song):
    tpb = song["ticks_per_beat"]
    scale = model.PPQ // tpb
    chunk_ticks = song["ticks_per_bar"] * CHUNK_BARS
    n_chunks = -(-song["bars"] // CHUNK_BARS)
    project = model.new_project()
    project["name"] = "%s (SoundHelix)" % song["name"]
    project["tempo"] = song["milli_bpm"] / 1000.0
    groove = song.get("groove") or [1000, 1000]
    if len(groove) == 2 and groove[0] + groove[1] == 2000:
        project["swing"] = round((groove[0] - 1000) / 10)    # percent of a sixteenth
    else:
        print("groove %r isn't a swing pair; left straight" % groove, file=sys.stderr)
    tracks, columns = [], []
    for name, kind, instrument, volume, parts in PARTS:
        by_chunk = chunks(song, parts, scale, chunk_ticks)
        seq = [by_chunk.get(k, ()) for k in range(n_chunks)]
        keep, column = fit_patterns(seq, name)
        t = model.track(kind, instrument, name)
        t["volume"] = volume
        t["patterns"] = [{"bars": CHUNK_BARS, "notes": [list(n) for n in c]} for c in keep] or [model.pattern()]
        tracks.append(t)
        columns.append(column)
    project["tracks"] = tracks
    song_rows = []
    for k in range(n_chunks):
        pats = {str(t["id"]): col[k] for t, col in zip(tracks, columns)}
        if song_rows and song_rows[-1]["patterns"] == pats and song_rows[-1]["repeat"] < MAX_REPEAT:
            song_rows[-1]["repeat"] += 1
        else:
            song_rows.append({"patterns": pats, "repeat": 1})
    project["song"] = song_rows
    project["song_mode"] = True
    return project


def main():
    song = json.loads(pathlib.Path(sys.argv[1]).read_text())
    project = convert(song)
    pathlib.Path(sys.argv[2]).write_text(json.dumps(project, separators=(",", ":")) + "\n")
    print("%s: %d BPM, swing %d%%, %d scenes, patterns %s" % (
        project["name"], round(project["tempo"]), project["swing"], len(project["song"]),
        " ".join("%s %d" % (t["name"], len(t["patterns"])) for t in project["tracks"])))


if __name__ == "__main__":
    main()
