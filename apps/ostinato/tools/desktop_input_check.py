"""The desktop app's keyboard and wheel, as the browser version has them.

    .venv/bin/python tools/desktop_input_check.py --desktop processes|threads|threads-ft

Keys go into the window's own event stream (desktop/control.py), so they
take a real key's path: the page's shortcut filter, then LVGL.

  keys     Space plays and stops, R arms recording, Ctrl+Z and Ctrl+Y undo
           and redo a note, Escape closes the guide
  shift    with Shift held a pattern slot switches every track; without it,
           only the selected one
  wheel    the wheel over the piano roll scrolls it two notes a notch
  text     a scene's name typed with Shift for the capitals, then with Caps
           Lock; Space and R type into the field instead of playing or arming
"""

import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "app" / "shared"))
import model  # noqa: E402


def run(s):
    app = s.app
    out = []
    check = lambda ok, line: out.append((bool(ok), line))

    # -- keys
    s.reset()
    app.key("SPACE"); time.sleep(0.6)
    playing = app.geom()["tick"] is not None
    app.key("SPACE"); time.sleep(0.4)
    stopped = app.geom()["tick"] is None
    check(playing and stopped, "keys: Space played (%s) and stopped (%s)" % (playing, stopped))
    app.key("r"); time.sleep(0.3)
    armed = app.geom()["armed"] is not None
    app.key("r"); time.sleep(0.3)
    check(armed and app.geom()["armed"] is None, "keys: R armed recording and disarmed it")
    app.select(0)
    g = app.geom()["grid"]
    gx, gy, gw, gh = g["rect"]
    app.click(gx + 0.5 * gw / g["steps"], gy + 0.5 * gh / len(g["rows"])); time.sleep(0.4)
    n0 = len(app.project()["tracks"][0]["patterns"][0]["notes"])
    app.key("z", ["ctrl"]); time.sleep(0.4)
    n1 = len(app.project()["tracks"][0]["patterns"][0]["notes"])
    app.key("y", ["ctrl"]); time.sleep(0.4)
    n2 = len(app.project()["tracks"][0]["patterns"][0]["notes"])
    check((n0, n1, n2) == (1, 0, 1), "keys: a note clicked in, Ctrl+Z took it out, Ctrl+Y put it back %s" % [n0, n1, n2])
    s.send({"kind": "first-run"}); time.sleep(1.0)
    tip_open = app.geom()["tip"]
    app.key("ESCAPE"); time.sleep(0.4)
    s.settle()

    # -- shift: B with Shift held switches every track, C without it only the selected one
    s.reset()
    app.select(2)
    app.key("LSHIFT", op="down"); time.sleep(0.2)
    app.click_rect(app.geom()["slots"][1])
    app.key("LSHIFT", op="up"); time.sleep(0.2)
    with_shift = [t.get("current", 0) for t in app.project()["tracks"]]
    app.click_rect(app.geom()["slots"][2])
    without = [t.get("current", 0) for t in app.project()["tracks"]]
    check(with_shift == [1] * len(with_shift) and without == [1, 1, 2] + [1] * (len(without) - 3),
          "shift: B with Shift held %s, then C without %s" % (with_shift, without))

    # -- wheel over the piano roll
    s.reset()
    app.select(1)
    r = app.geom()["roll"]
    low0 = r["low"]
    rx, ry, rw, rh = r["rect"]
    app.wheel(-100, rx + rw / 2, ry + rh / 2); time.sleep(0.4)
    low1 = app.geom()["roll"]["low"]
    app.wheel(100, rx + rw / 2, ry + rh / 2); time.sleep(0.4)
    low2 = app.geom()["roll"]["low"]
    check(low1 == low0 + 2 and low2 == low0, "wheel: the roll scrolled %d to %d and back to %d" % (low0, low1, low2))

    # -- text: a scene's name, Shift for the capitals, then Caps Lock
    q = model.new_project()
    tid = str(q["tracks"][2]["id"])
    q["song"] = [{"patterns": {tid: 0}, "repeat": 1} for _ in range(3)]
    q["song_mode"] = True
    s.reset(q)
    app.click_rect(app.geom()["scenes"]["names"][2]); time.sleep(0.4)
    for ch in "Big Drop r":
        if ch == " ":
            app.key("SPACE")
        elif ch.isupper():
            app.key(ch.lower(), ["shift"])
        else:
            app.key(ch)
    app.key("RETURN"); time.sleep(0.5)
    g = app.geom()
    name = app.project()["song"][2].get("name")
    check(name == "Big Drop r" and g["tick"] is None and g["armed"] is None,
          "text: scene 3 is named %r; typing didn't play (%s) or arm (%s)" % (name, g["tick"], g["armed"]))
    app.click_rect(app.geom()["scenes"]["names"][1]); time.sleep(0.4)
    for ch in "intro":
        app.key(ch, ["caps"])
    app.key("RETURN"); time.sleep(0.5)
    caps = app.project()["song"][1].get("name")
    check(caps == "INTRO", "text: with Caps Lock on, 'intro' types %r" % caps)
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
    ok = report("input", results)
    (ROOT / "runs" / "desktop_input_check.json").write_text(json.dumps(results, indent=1))
    sys.exit(0 if ok and results else 1)


if __name__ == "__main__":
    main()
