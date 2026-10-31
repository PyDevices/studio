"""Render every track of a song to see that each one makes sound.

    .venv/bin/python tools/sketch_render.py [SONG.json ...] [--seconds 20] [--wav-dir DIR]

With no song it takes the demo song and every sketch in songs/sketches. For
each track it plays the patterns the song's scenes use, through the track's
instrument with its patch and knobs set, and prints the peak and the loudest
second in dBFS. A track that is silent, or a song whose mix clips, exits 1.
It needs the audioinstruments and audiodsp packages on the path (the
venv's, or ``PYTHONPATH`` pointing at their checkouts' ``lib`` folders).
"""
import argparse
import array
import json
import pathlib
import sys

import audiocore
import audioinstruments
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parents[1] / "lib"))
from songlink import model  # noqa: E402

SR = 48000
BLOCK = 256


def render_track(track, tempo, seconds):
    """The track's patterns, laid end to end, as float stereo at the track's level."""
    inst = audioinstruments.create(track["instrument"], SR, channel_count=2)
    inst.program_change(int(track.get("patch") or 0))
    for k, v in track.get("macros", {}).items():
        inst.set_macro(int(k), v)
    ticks_per_sec = model.PPQ * tempo / 60.0
    events = []
    frames = int(seconds * SR)
    # Lay the patterns end to end, one after another, in the order they sit in the track.
    at = 0
    for pat in track["patterns"]:
        length = pat["bars"] * 4 * model.PPQ
        for start, ln, pitch, vel in pat["notes"]:
            on = int((at + start) / ticks_per_sec * SR)
            events.append((on, 1, pitch, vel))
            events.append((int((at + start + ln) / ticks_per_sec * SR), 0, pitch, 0))
        at += length
    events.sort()
    out = np.zeros((frames, 2), dtype=np.float32)
    pos = 0
    ev = 0
    while pos < frames:
        while ev < len(events) and events[ev][0] < pos + BLOCK:
            when, on, pitch, vel = events[ev]
            if on:
                inst.note_on(pitch, vel, sample_position=max(0, when - pos))
            else:
                inst.note_off(pitch, sample_position=max(0, when - pos))
            ev += 1
        fill = getattr(inst, "fill", None)
        if fill is not None:
            fill()
        _, buf = audiocore.get_buffer(inst.output)
        a = np.frombuffer(bytes(buf), dtype=np.int16).astype(np.float32) / 32768.0
        n = min(len(a) // 2, frames - pos)
        out[pos:pos + n] = a[:2 * n].reshape(-1, 2)
        pos += BLOCK
    inst.deinit()
    # The engine's track level and balance, so the peaks are what the mix reaches.
    level = min(1.5, track.get("volume", 100) / 127.0) * 0.8
    p = max(0, min(127, track.get("pan", 64))) / 127.0
    return out * level * np.array([min(1.0, 2.0 * (1.0 - p)), min(1.0, 2.0 * p)], dtype=np.float32)


def db(x):
    return 20 * np.log10(max(float(x), 1e-9))


def check(path, seconds, wav_dir):
    song = json.loads(path.read_text())
    ok = True
    print("%s (%d BPM)" % (song["name"], round(song["tempo"])))
    mix = None
    for t in song["tracks"]:
        a = render_track(t, song["tempo"], seconds)
        peak = float(np.max(np.abs(a)))
        sec = max(float(np.max(np.abs(a[i:i + SR]))) for i in range(0, len(a), SR))
        heard = peak > 0.01
        ok &= heard
        print("  %-6s %-18s patch %-2s peak %6.1f dBFS  loudest second %6.1f  %s"
              % (t["name"], t["instrument"], t.get("patch", 0), db(peak), db(sec), "ok" if heard else "SILENT"))
        mix = a if mix is None else mix + a
    mpeak = float(np.max(np.abs(mix)))
    print("  mix of the tracks played together: peak %.1f dBFS" % db(mpeak))
    ok &= mpeak < 1.0
    if wav_dir:
        import soundfile
        soundfile.write(str(pathlib.Path(wav_dir) / (path.stem + ".wav")), np.clip(mix, -1, 1), SR)
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("songs", nargs="*")
    ap.add_argument("--seconds", type=float, default=20)
    ap.add_argument("--wav-dir")
    args = ap.parse_args()
    songs = [pathlib.Path(s) for s in args.songs] or (
        [ROOT / "assets" / "demo_song.json"] + sorted((ROOT / "songs" / "sketches").glob("*.json")))
    ok = all([check(s, args.seconds, args.wav_dir) for s in songs if s.name != "soundfont_check.json"])
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
