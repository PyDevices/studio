"""The demo song: it loads on the first run and from the menu, its controls
work, and it exports with every track heard.

    .venv/bin/python tools/demo_check.py --cdp http://127.0.0.1:9222 [--long]
    .venv/bin/python tools/demo_check.py --desktop processes|threads|threads-ft [--long]

Sends the first-run message: the demo song must load under the welcome guide
(which stays on top: see runs/demo_first_run.png), with its 36 scenes and
SoundHelix's swing. It nudges swing up and down from the real buttons, and
scrolls the scene list: an edit to a scene far down the list must leave it
scrolled. Demo song in the menu must load it too. Then a WAV export of
scenes 11 to 13, where all four tracks play: the right length, never silent
for a second, and every track heard (each engine renders one track, and the
page measures each). --long exports the whole song instead, about two
minutes, and checks its peak stays under full scale; it's saved to
runs/demo_song.wav for listening.

Playback without dropouts, and swing reaching the sound, are checked in
tools/master_check.py.
"""
import argparse, json, pathlib, sys, time

import numpy as np
import soundfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from files_check import export, menu

SHORT = (10, 13)        # scenes 11 to 13: the first stretch with all four tracks playing


def run(s, long=False):
    app = s.app
    out = []
    check = lambda ok, line: out.append((ok, line))

    s.reset()
    s.send({"kind": "first-run"})
    time.sleep(0.5)
    s.settle()
    s.screenshot(str(ROOT / "runs" / "demo_first_run.png"))
    p = app.project()
    check("SoundHelix" in p["name"], "the first run loads the demo song (%s)" % p["name"])
    check(len(p["song"]) == 36 and p["song_mode"], "36 scenes, in song mode (%d)" % len(p["song"]))
    check(p["swing"] == 15, "SoundHelix's groove is the swing (%d)" % p["swing"])
    check([t["instrument"] for t in p["tracks"]] == ["tr808", "virus", "juno106", "dx7"],
          "four tracks on their instruments")
    s.send({"kind": "key", "key": "escape"}); time.sleep(0.3)

    g = app.geom()
    sx, sy, sw, sh = g["swing"]
    app.click_rect([sx + sw - sh, sy, sh, sh])
    up = app.project()["swing"]
    app.click_rect([sx, sy, sh, sh]); app.click_rect([sx, sy, sh, sh])
    down = app.project()["swing"]
    check((up, down) == (18, 14), "swing + then - - from 58%%: 59%%, 57%% (%d, %d)" % (up, down))

    sc = app.geom()["scenes"]
    lx, ly, lw, lh = sc["list"]
    top0 = sc["rows"][0][1]
    dpr = app.dpr
    s.mouse.move((lx + lw * 0.85) / dpr, (ly + lh * 0.8) / dpr); s.mouse.down()
    for k in range(1, 11):
        s.mouse.move((lx + lw * 0.85) / dpr, (ly + lh * (0.8 - 0.06 * k)) / dpr); time.sleep(0.03)
    s.mouse.up(); time.sleep(0.8)
    sc = app.geom()["scenes"]
    scrolled = top0 - sc["rows"][0][1]
    check(scrolled > lh * 0.3, "the scene list scrolls (%d px)" % scrolled)
    visible = [i for i, r in enumerate(sc["rows"]) if ly <= r[1] and r[1] + r[3] <= ly + lh]
    i = visible[-1]
    old = app.project()["song"][i]["patterns"]
    app.click_rect(sc["letters"][i][0]); time.sleep(0.4)          # the Drums letter: its picker
    picker = app.geom().get("picker")
    pick = 2 if old.get(str(app.project()["tracks"][0]["id"])) != 2 else 3
    if picker:
        app.click_rect(picker[pick]); time.sleep(0.5)
    sc2 = app.geom()["scenes"]
    check(app.project()["song"][i]["patterns"] != old, "picking a pattern for scene %d changes it" % (i + 1))
    check(abs(sc2["rows"][0][1] - sc["rows"][0][1]) <= 8, "the list stays scrolled after the edit (%d, %d)"
          % (sc["rows"][0][1], sc2["rows"][0][1]))

    menu(app, "demo"); time.sleep(0.5)
    g = app.geom()
    asked = "confirm" in g                     # the scene edit above is unsaved
    if asked:
        app.click_rect(g["confirm"]["discard"]); time.sleep(0.5)
    s.settle()
    p = app.project()
    check(asked and "SoundHelix" in p["name"] and p["swing"] == 15 and len(p["song"]) == 36,
          "Demo song in the menu asks first (there's an unsaved edit), then loads it")

    if long:
        song = p
    else:
        song = json.loads(json.dumps(p))
        song["song"] = song["song"][SHORT[0]:SHORT[1]]
        s.load(song)
    mix, sr = export(s, app, shot="demo_rendering.png" if long else None)
    if long:
        soundfile.write(str(ROOT / "runs" / "demo_song.wav"), mix, sr)
    mono = np.abs(mix).mean(axis=1) if mix.ndim > 1 else np.abs(mix)
    bars = sum(4 * sc_["repeat"] for sc_ in song["song"])       # every demo pattern is 4 bars
    want = bars * 4 * 60 / song["tempo"]
    secs = len(mono) / sr
    check(want < secs < want + 4, "the WAV is %s (%.1f s for %.1f s)" % ("the whole song" if long else
          "scenes %d to %d" % (SHORT[0] + 1, SHORT[1]), secs, want))
    if long:
        peak = s.last_export()["peak"]
        check(peak < 1.0, "the mix peaks under full scale, so live playback never limits (peak %.2f)" % peak)
    win = sr // 10
    rms = np.sqrt(np.convolve(mono[: int(want * sr)] ** 2, np.ones(win) / win, "valid")[::win])
    quiet = max((len(q) for q in "".join("q" if x < 1e-3 else "." for x in rms).split(".")), default=0)
    check(quiet < 10, "never silent for a second (longest quiet %.1f s)" % (quiet / 10))
    last = s.last_export()
    for t, level in zip(song["tracks"], last["rms"]):      # track i renders on engine i
        db = 20 * np.log10(level + 1e-9)
        check(db > -40, "%s (%s) is heard: %.1f dBFS" % (t["name"], t["instrument"], db))
    return out


def main():
    from checkkit import add_session_args, report, session
    ap = argparse.ArgumentParser()
    add_session_args(ap)
    ap.add_argument("--long", action="store_true", help="export the whole song (about two minutes)")
    a = ap.parse_args()
    with session(a) as s:
        ok = report("demo", run(s, long=a.long))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
