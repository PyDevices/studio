"""The desktop app split in two on Windows, with a browser as a second front panel.

    .venv/bin/python tools/split_check.py

It starts ``python -m ostinato --split`` on Windows (tools/desktopkit.py,
runner "split"), opens the song page the engines' process serves
(http://127.0.0.1:9100/) in headless Chromium from WSL, and then:

- the window opens the SoundFont song: the page shows it, with what its
  samples cost;
- the page adds a step, mutes a track and sets the tempo: the window's song has each;
- the window's swing buttons: the page shows the new swing;
- the page presses Play: the song plays live through the mixer (its tap), and Stop.

Each run saves runs/split_window.bmp, the window after the page's edits.
tools/link_check.py checks the same link on any machine, without audio, and
plants the faults; this one is the real app.
"""

import json
import pathlib
import sys
import time

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from desktopkit import DesktopSession, win, wsl  # noqa: E402,F401
from files_check import menu  # noqa: E402

SONG = ROOT / "songs" / "sketches" / "soundfont_check.json"
PAGE = "http://127.0.0.1:9100/"


def main():
    from playwright.sync_api import sync_playwright

    out = []

    def check(ok, line):
        out.append((bool(ok), line))
        print(("ok    " if ok else "FAIL  ") + line, flush=True)

    def until(fn, seconds=5.0):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            v = fn()
            if v:
                return v
            time.sleep(0.1)
        return fn()

    with DesktopSession("split") as s, sync_playwright() as pw:
        app = s.app
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(PAGE)
        page.wait_for_function("window.ostinato && window.ostinato.ready", timeout=15000)
        check(True, "the page opened from the engines' process")

        # The window opens a song.
        s.reset()
        s.set_open(SONG.read_text(encoding="utf-8"))
        menu(app, "open")
        time.sleep(0.3)
        s.settle()
        name = until(lambda: page.evaluate("document.querySelector('#name').textContent") == "SoundFont Check")
        check(name, "the window opened 'SoundFont Check' and the page shows it")
        cost = until(lambda: page.evaluate("document.querySelector('#samples').textContent"), 10.0)
        check("MB" in (cost or ""), "the page says what its samples cost: %r" % cost)

        # The page edits; the window has each.
        tracks = app.project()["tracks"]
        before = len(tracks[0]["patterns"][tracks[0].get("current", 0)]["notes"])
        cell = page.query_selector('.cell[data-pitch="38"]:not(.on)')
        cell.click()
        page.click(".track[data-track='1'] .mute")
        page.fill("#tempo", "131")
        page.dispatch_event("#tempo", "change")
        p = until(lambda: (lambda q: q if q["tempo"] == 131 else None)(app.project())) or app.project()
        t0 = p["tracks"][0]
        check(len(t0["patterns"][t0.get("current", 0)]["notes"]) == before + 1, "the page's step is in the window's song")
        check(p["tracks"][1]["mute"] is True, "and its mute")
        check(p["tempo"] == 131, "and its tempo (%s)" % p["tempo"])
        shot = win("split_window.bmp")
        s.ask({"desk": "shot", "path": shot}, "shot", timeout=10)
        try:
            (ROOT / "runs" / "split_window.bmp").write_bytes(wsl("split_window.bmp").read_bytes())
        except OSError:
            pass

        # The window edits; the page has it.
        g = app.geom()
        sx, sy, sw, sh = g["swing"]
        swing0 = app.project()["swing"]
        app.click_rect([sx + sw - sh, sy, sh, sh])
        swing1 = app.project()["swing"]
        seen = until(lambda: page.evaluate("document.querySelector('#swing').value") == str(swing1))
        check(swing1 != swing0 and seen, "the window's swing %s -> %s shows on the page" % (swing0, swing1))

        # Play from the page: the window's song plays through the mixer.
        x = app.record(5.0, before=lambda: page.click("#play"))
        playing = page.evaluate("document.querySelector('#play').textContent") == "Stop"
        page.click("#play")
        stopped = until(lambda: page.evaluate("document.querySelector('#play').textContent") == "Play")
        sr = 48000
        rms = [float(np.sqrt(np.mean(x[i:i + sr] ** 2))) for i in range(sr, len(x) - sr, sr)]
        check(playing and stopped, "the page pressed Play, then Stop")
        check(rms and min(rms) > 0.01, "and the mixer played the song: %.1f s heard, quietest second at %.3f RMS"
              % (len(x) / sr, min(rms) if rms else 0))
        browser.close()
        if s.log:
            print("app errors:", s.log[:6])

    bad = [line for ok, line in out if not ok]
    print("split check: %s" % ("FAILED (%d)" % len(bad) if bad else "passed"))
    (ROOT / "runs").mkdir(exist_ok=True)
    (ROOT / "runs" / "split_check.json").write_text(json.dumps(out, indent=1))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
