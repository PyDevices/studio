"""The desktop app starts with no network: the browser's offline check, for python -m ostinato.

    .venv/bin/python tools/desktop_offline_check.py --desktop processes|threads|threads-ft

The only thing the app fetches from the web is its text fonts (from
lvgl-bindings), kept in its data folder after the first start, as the
service worker keeps them for the browser version. The network is cut by
pointing the app's proxies at a closed port.

  cached   after one start online, a start with no network comes up with
           every font loaded, and plays
  cold     with nothing cached and no network, it still starts, on LVGL's
           built-in font
"""

import pathlib
import shutil
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

DEAD = "http://127.0.0.1:9"          # nothing listens on the discard port: every fetch fails at once
OFFLINE = {"HTTP_PROXY": DEAD, "HTTPS_PROXY": DEAD, "NO_PROXY": "", "WSLENV": "HTTP_PROXY:HTTPS_PROXY:NO_PROXY"}


def last_start(s):
    """What the app printed since it was last started."""
    text = s.logfile.read_text(errors="replace")
    return text[text.rfind("\n== start"):]


def plays(s):
    """The app answers and its transport runs: Space plays, Space stops."""
    s.reset()
    s.app.key("SPACE"); time.sleep(0.8)
    on = s.app.geom()["tick"] is not None
    s.app.key("SPACE"); time.sleep(0.4)
    return on and s.app.geom()["tick"] is None


def run(s):
    from desktopkit import wsl

    out = []
    check = lambda ok, line: out.append((bool(ok), line))
    online = s.env
    try:
        # -- cached: the session started online once already, so the fonts are on disk
        s.env = dict(online, **OFFLINE)
        s.restart()
        log = last_start(s)
        failed = [line for line in log.splitlines() if "fonts not loaded" in line]
        check("ui ready" in log and not failed and plays(s),
              "cached: with no network the app started (%s) with every font (%s) and plays"
              % ("ui ready" in log, failed[0][:120] if failed else "all loaded"))

        # -- cold: nothing cached, no network
        s.stop()
        shutil.rmtree(wsl("data", "cache"), ignore_errors=True)
        s.boot(fresh=False)
        log = last_start(s)
        check("ui ready" in log and "fonts not loaded" in log and plays(s),
              "cold: with nothing cached and no network it still started (%s), on the built-in font (%s), and plays"
              % ("ui ready" in log, "fonts not loaded" in log))
    finally:
        s.env = online
        s.restart()                 # back online for whatever runs next (it fetches the fonts again)
    return out


def main():
    import argparse

    from checkkit import add_session_args, report, session

    ap = argparse.ArgumentParser()
    add_session_args(ap)
    a = ap.parse_args()
    if not a.desktop:
        sys.exit("this check is for the desktop app (the browser's is tools/offline_check.py)")
    with session(a) as s:
        ok = report("offline", run(s))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
