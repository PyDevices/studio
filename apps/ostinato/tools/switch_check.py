"""Changing an instrument mid-song silences only that track.

    .venv/bin/python tools/switch_check.py --cdp http://127.0.0.1:9222
    .venv/bin/python tools/switch_check.py --desktop processes|threads|threads-ft

Plays drums and bass, then switches the Lead (its own engine) through four
instruments while they play. The worklet must count no dropouts on any
stream: the rebuilding engine pauses its stream instead.
"""
import argparse, pathlib, sys, time
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent.parent / "lib")); sys.path.insert(0, str(ROOT / "tools"))
from songlink import model


def run(s):
    app = s.app
    p = model.new_project()
    p["tracks"][0]["patterns"][0]["notes"] = [[k * 24, 24, 42 if k % 4 else 36, 100] for k in range(16)]
    p["tracks"][1]["patterns"][0]["notes"] = [[k * 48, 40, 45 + (k % 3) * 5, 100] for k in range(8)]
    s.reset(p)
    g = app.geom()
    app.click_rect(g["play"]); time.sleep(3)
    before = s.stats()
    for name in ("dx7", "prophet5", "mellotron", "minimoog"):
        s.send({"kind": "instrument", "index": 3, "name": name})
        time.sleep(2.5)
    after = s.stats()
    app.click_rect(g["stop"])
    lead = app.project()["tracks"][3]["instrument"]
    d = [x["underruns"] - y["underruns"] for x, y in zip(after["streams"], before["streams"])]
    return [(sum(d) == 0 and lead == "minimoog",
             "dropouts per stream during four instrument changes: %s; Lead ended on %s" % (d, lead))]


def main():
    from checkkit import add_session_args, report, session
    ap = argparse.ArgumentParser(); add_session_args(ap); a = ap.parse_args()
    with session(a) as s:
        ok = report("switch", run(s))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
