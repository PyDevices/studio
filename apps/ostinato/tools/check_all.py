"""Every check, in one app session: boot once, run them all, time each.

    .venv/bin/python tools/check_all.py --cdp http://127.0.0.1:9222 [--long] [--only master,demo]
    .venv/bin/python tools/check_all.py --desktop processes|threads|threads-ft [--only groovebox,files]

Runs groovebox, files, switch, master, demo, workflow, workflow2 and workflow3 (tools/*_check.py) one after
another in the same page; against the desktop app, input and offline too
(its keyboard, and a start with no network: tools/desktop_*_check.py). Each starts from a fresh project rather than a
reload, so the app boots once. --long exports the whole demo song (about
two minutes more). Results are printed as each part finishes, then a
summary with how long each check took; the exit status is 0 only if
everything passed.
"""

import argparse
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from checkkit import add_session_args, report, session  # noqa: E402
import demo_check  # noqa: E402
import desktop_input_check  # noqa: E402
import desktop_offline_check  # noqa: E402
import files_check  # noqa: E402
import groovebox_check  # noqa: E402
import master_check  # noqa: E402
import switch_check  # noqa: E402
import workflow2_check  # noqa: E402
import workflow3_check  # noqa: E402
import workflow_check  # noqa: E402

CHECKS = [
    ("groovebox", lambda s, a: groovebox_check.run(s)),
    ("files", lambda s, a: files_check.run(s)),
    ("switch", lambda s, a: switch_check.run(s)),
    ("master", lambda s, a: master_check.run(s)),
    ("demo", lambda s, a: demo_check.run(s, long=a.long)),
    ("workflow", lambda s, a: workflow_check.run(s)),
    ("workflow2", lambda s, a: workflow2_check.run(s)),
    ("workflow3", lambda s, a: workflow3_check.run(s)),
    ("input", lambda s, a: desktop_input_check.run(s)),        # the desktop app only
    ("offline", lambda s, a: desktop_offline_check.run(s)),    # the desktop app only (the browser's: offline_check.py)
]
DESKTOP_ONLY = ("input", "offline")


def main():
    ap = argparse.ArgumentParser()
    add_session_args(ap)
    ap.add_argument("--long", action="store_true", help="export the whole demo song")
    ap.add_argument("--only", default="", help="comma-separated checks to run")
    a = ap.parse_args()
    only = set(filter(None, a.only.split(",")))
    summary = []
    t_all = time.monotonic()
    with session(a) as s:
        boot_s = time.monotonic() - t_all
        print("booted in %.0f s" % boot_s, flush=True)
        for name, fn in CHECKS:
            if only and name not in only:
                continue
            if name in DESKTOP_ONLY and not a.desktop:
                continue
            print("== %s" % name, flush=True)
            t0 = time.monotonic()
            try:
                ok = report(name, fn(s, a))
            except Exception as e:                  # one broken check doesn't stop the rest
                import traceback
                traceback.print_exc()
                print("FAIL  %s raised %s: %s" % (name, type(e).__name__, e), flush=True)
                ok = False
            if s.navigations > s.expected:
                print("NOTE  the page reloaded on its own during %s (a window resize?): its results may not count"
                      % name, flush=True)
                s.expected = s.navigations
            summary.append((name, ok, time.monotonic() - t0))
        if s.log:
            print("page errors:", s.log[:6])
    total = time.monotonic() - t_all
    print("\n%-10s %-5s %s" % ("check", "", "seconds"))
    for name, ok, secs in summary:
        print("%-10s %-5s %.0f" % (name, "PASS" if ok else "FAIL", secs))
    print("%-10s %-5s %.0f (boot %.0f)" % ("all", "PASS" if all(ok for _, ok, _ in summary) else "FAIL", total, boot_s))
    sys.exit(0 if summary and all(ok for _, ok, _ in summary) else 1)


if __name__ == "__main__":
    main()
