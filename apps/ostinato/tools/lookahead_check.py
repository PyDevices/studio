"""The engine sequences notes ahead of the render, so a paused sequencer
doesn't make a note late.

    ~/gh/pydevices/.toolbox/bin/python tools/lookahead_check.py [--plant]

One engine renders four bars of drums and a bass line offline, block by
block, three ways: sequencing each block as it renders (no lookahead, the
reference); with the lookahead and the sequencer paused for six blocks in
every ten (32 ms of every 53, a board's garbage collection); and with the
lookahead and an edit in the middle, which must be heard from the next
block. The paused run must match the reference to the sample. ``--plant``
pauses the sequencer with no lookahead, the fault the lookahead exists for:
the check must fail.

It also reports what a SoundFont track's preload read, when a bank is found
($SOUNDFONT or ~/soundfonts).

Runs on CPython with the PyDevices audio stack (the toolbox's Python, or the
desktop app's venv).
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in ("app/engine", "app/shared"):
    sys.path.insert(0, os.path.join(ROOT, p))

import engine as eng  # noqa: E402
import model  # noqa: E402

SR = 48000
BARS = 4


def project():
    p = model.new_project()
    p["tempo"] = 120.0
    p["swing"] = 30
    drums, bass = p["tracks"][0], p["tracks"][1]
    p["tracks"] = [drums, bass]
    s = model.STEP
    for i in range(16):
        if i % 4 == 0:
            drums["patterns"][0]["notes"].append([i * s, s, 36, 110])
        if i % 2 == 1:
            drums["patterns"][0]["notes"].append([i * s, s, 42, 80])
    for i, pitch in enumerate((36, 36, 43, 41, 36, 48, 46, 43)):
        bass["patterns"][0]["notes"].append([i * 2 * s, s, pitch, 100])
    return p


def render(lookahead, paused=None, edit_at=None):
    posted = []
    e = eng.Engine(SR, 1, posted.append)
    e.lookahead = lookahead
    p = project()
    e.apply({"op": "replace", "value": p})
    e.assign([t["id"] for t in p["tracks"]])
    e.offset = 0
    e.play(eng.BLOCK * 4, 0.0)
    blocks = int(BARS * 4 * 60 / p["tempo"] * SR) // eng.BLOCK
    out = []
    for n in range(blocks):
        e.sequencing = paused is None or not paused(n)
        if edit_at is not None and n == edit_at:
            # Mute the hats and move the bass up an octave, mid-bar.
            path = model.track_path(p["tracks"][1]["id"], "patterns", 0, "notes")
            notes = [[a, b, c + 12, d] for a, b, c, d in p["tracks"][1]["patterns"][0]["notes"]]
            e.apply({"op": "set", "path": path, "value": notes})
        out.append(bytes(e.render(1)))
    return b"".join(out), e


def first_difference(a, b):
    for i in range(0, min(len(a), len(b)), 4):
        if a[i:i + 4] != b[i:i + 4]:
            return i // 4
    return None


def main():
    plant = "--plant" in sys.argv
    gc_pause = lambda n: n % 10 < 6      # noqa: E731
    ref, _ = render(0)
    loud = max(abs(int.from_bytes(ref[i:i + 2], "little", signed=True)) for i in range(0, len(ref), 2))
    results = []
    check = lambda ok, line: results.append((bool(ok), line))  # noqa: E731
    check(loud > 1000, "the reference plays (peak %d)" % loud)

    ahead, e = render(0 if plant else eng.LOOKAHEAD, paused=gc_pause)
    diff = first_difference(ref, ahead)
    check(diff is None, "a sequencer paused 6 blocks in 10, %d frames ahead: every note on its sample%s"
          % (0 if plant else eng.LOOKAHEAD, "" if diff is None else " (first difference at frame %d)" % diff))
    check(not e.queue or e.queue[0][0] >= e.frame + e.offset, "nothing left in the queue that was due")

    # An edit in the middle is heard from the next block, lookahead or not.
    edit_block = 330             # 544 frames before a bass note (step 14, frame 85024): inside the lookahead
    ref_edit, _ = render(0, edit_at=edit_block)
    ahead_edit, _ = render(0 if plant else eng.LOOKAHEAD, paused=None, edit_at=edit_block)
    diff = first_difference(ref_edit, ahead_edit)
    check(diff is None, "an edit mid-song is heard as soon as without the lookahead%s"
          % ("" if diff is None else " (first difference at frame %d)" % diff))
    check(first_difference(ref, ref_edit) is not None, "and the edit changed the sound")

    preload_report()
    bad = [line for ok, line in results if not ok]
    for ok, line in results:
        print(("ok    " if ok else "FAIL  ") + line)
    print("lookahead check: %s" % ("FAILED" if bad else "passed"))
    return 1 if bad else 0


def preload_report():
    """A SoundFont song's preload, when this machine has a bank."""
    try:
        from audioinstruments import soundfont

        soundfont.find_bank(None)
    except Exception as e:  # noqa: BLE001
        print("preload: no SoundFont bank here (%s); skipped" % e)
        return
    import json

    with open(os.path.join(ROOT, "songs", "sketches", "soundfont_check.json")) as f:
        song = json.load(f)
    posted = []
    e = eng.Engine(SR, 1, posted.append)
    e.apply({"op": "replace", "value": song})
    e.assign([t["id"] for t in song["tracks"]])
    pre = [m for m in posted if m.get("t") == "preloaded"]
    for m in pre:
        print("preload: track %s, %d samples, %.1f MB, %d read in %.0f ms (arena %.0f MB)"
              % (m["track"], m["samples"], m["bytes"] / 1048576, m["read"], m["ms"], m["arena"] / 1048576))
    if len(pre) != len(song["tracks"]):
        raise SystemExit("preload: %d of %d SoundFont tracks reported" % (len(pre), len(song["tracks"])))
    # A program change reads the new program's samples then, not at its first note.
    posted.clear()
    path = model.track_path(song["tracks"][1]["id"], "patch")
    e.apply({"op": "set", "path": path, "value": 34})
    pre = [m for m in posted if m.get("t") == "preloaded"]
    if not (pre and pre[0]["track"] == song["tracks"][1]["id"]):
        raise SystemExit("preload: a program change didn't preload")
    print("preload: program change to 34 read %d samples in %.0f ms" % (pre[0]["read"], pre[0]["ms"]))


if __name__ == "__main__":
    sys.exit(main())
