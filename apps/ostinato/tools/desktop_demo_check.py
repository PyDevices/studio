"""Phase 2's gate on the desktop: python -m ostinato opens maximised, loads
the demo song on its first run and plays it.

    .venv/bin/python tools/desktop_demo_check.py --desktop processes|threads|threads-ft

It starts the app as a person would the first time (not --fresh, on an empty
data folder), so the first-run guide loads the demo song; closes the guide
with Escape, presses Space, and listens to 20 s of the mixer's output.
"""

import json
import pathlib
import sys
import time

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))


def run(s):
    app = s.app
    out = []
    check = lambda ok, line: out.append((bool(ok), line))
    from desktopkit import wsl

    s.stop()
    for f in ("welcomed", "autosave.ostinato.json"):  # as if it had never run
        wsl("data", f).unlink(missing_ok=True)
    s.boot(fresh=False)
    time.sleep(2.0)
    pong = s.ask({"desk": "ping"}, "pong")
    g = app.geom()
    check(pong.get("maximized") and pong.get("client") == g["canvas"],
          "window: maximised %s, client %s, LVGL %s" % (pong.get("maximized"), pong.get("client"), g["canvas"]))
    p = app.project()
    check(p.get("song_mode") and len(p.get("song") or []) > 10 and "SoundHelix" in (p.get("name") or ""),
          "demo: %r loaded, %d scenes" % (p.get("name"), len(p.get("song") or [])))
    app.key("ESCAPE"); time.sleep(0.5)
    s.settle()
    st0 = s.stats()
    x = app.record(20.0, before=lambda: app.key("SPACE"))
    pos = app.geom()["position"]
    app.key("SPACE"); time.sleep(0.3)
    st1 = s.stats()
    secs = len(x) / 48000
    rms = [float(np.sqrt(np.mean(x[i:i + 48000] ** 2))) for i in range(48000, len(x) - 48000, 48000)]
    check(min(rms) > 0.01 and secs > 19, "plays: %.1f s heard, quietest second at %.3f RMS; position %s"
          % (secs, min(rms) if rms else 0, pos))
    lanes = [b - a for a, b in zip(st0["lane_underruns"], st1["lane_underruns"])]
    check(st1["underruns"] == st0["underruns"], "no dropouts: %d underruns while it played (device %d, engines %s)"
          % (st1["underruns"] - st0["underruns"], st1["device_underruns"] - st0["device_underruns"], lanes))
    return out


def main():
    import argparse

    from checkkit import add_session_args, report, session

    ap = argparse.ArgumentParser()
    add_session_args(ap)
    a = ap.parse_args()
    if not a.desktop:
        sys.exit("this check is for the desktop app: --desktop processes|threads|threads-ft")
    with session(a) as s:
        results = run(s)
        if s.log:
            print("app errors:", s.log[:6])
    ok = report("demo", results)
    (ROOT / "runs" / "desktop_demo_check.json").write_text(json.dumps(results, indent=1))
    sys.exit(0 if ok and results else 1)


if __name__ == "__main__":
    main()
