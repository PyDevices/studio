"""Drives a running Vamp (lib/vamp/main.py) over hmilink, as Ostinato's window
does, and checks the box keeps the song and the time.

    python tools/vamp_link_check.py HOST:PORT [--song FILE] [--browser] [--json OUT]

The box is already running: on desktop MicroPython, or on the board over its
Ethernet port. This Python needs hmilink (``$HMILINK_PATH``, a pydevices
checkout beside this one, or Ostinato's vendor/lib) and, for ``--browser``,
Playwright. The default song is Ostinato's SoundFont check song. The check is one panel. It loads a song, presses Play, changes
the tempo, mutes a track, adds a note and puts a reverb on the master, then
stops, and requires:

- the box's playhead to move at the song's tempo, before and after the change;
- no late notes and no underruns;
- a second panel that joins at the end to get the same song this one has.

It prints the link's round trip (a ping, and each op to its echo), what the
box says it costs (block time, edits, collections) and the samples each track
preloaded. Start the box with ``--plant nomute`` (it echoes mutes without
applying them) and the check must fail. ``--browser`` also opens the page
the box serves in headless Chromium and plays and mutes from there.
"""

import json
import os
import platform
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OSTINATO = os.path.join(ROOT, "apps", "ostinato")


def _hmilink():
    found = [os.environ.get("HMILINK_PATH")]
    parent = ROOT
    for _ in range(3):
        parent = os.path.dirname(parent)
        found.append(os.path.join(parent, "pydevices", "hmilink"))
    found.append(os.path.join(OSTINATO, "vendor", "lib"))
    for p in found:
        if p and os.path.isfile(os.path.join(p, "hmilink", "__init__.py")):
            return p
    raise SystemExit("no hmilink: set HMILINK_PATH to the folder holding the hmilink package")


sys.path[0:0] = [os.path.join(ROOT, "lib"), _hmilink()]

import hmilink  # noqa: E402
from hmilink import tcp  # noqa: E402

from songlink import model  # noqa: E402

FAILS = []


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what, flush=True)
    if not ok:
        FAILS.append(what)
    return ok


class Probe:
    def __init__(self, host, port):
        self.panel = p = hmilink.Panel(tcp.connect(host, port))
        self.tele = None
        self.events = []
        self.echo = {}               # op id -> ms from send to echo
        self.sent = {}
        self.pongs = []
        p.on_tele = self._tele
        p.on_event = self.events.append
        p.on_op = self._op
        p.on_pong = self.pongs.append
        self.wait(lambda: p.ready, 10, "the song from the box")

    def _tele(self, v):
        self.tele = (time.perf_counter(), v)

    def _op(self, msg):
        sent = self.sent.pop(msg.get("id"), None) if msg.get("from") == self.panel.me else None
        if sent is not None:
            self.echo[msg.get("id")] = (time.perf_counter() - sent) * 1000.0

    def wait(self, cond, seconds, what):
        end = time.perf_counter() + seconds
        while not cond():
            self.panel.poll()
            if time.perf_counter() > end:
                raise TimeoutError("no %s in %.0f s" % (what, seconds))
            time.sleep(0.002)

    def run(self, seconds):
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            self.panel.poll()
            time.sleep(0.002)

    def send(self, kind, pointer, value=None):
        i = self.panel.send(kind, pointer, value)
        self.sent[i] = time.perf_counter()
        return i

    def box(self):
        return self.tele[1]["mixer"]["box"] if self.tele and self.tele[1].get("mixer") else {}

    def rate(self, seconds=2.0):
        """Ticks a second of the box's own audio clock, over ``seconds``: the
        playhead against the frame it reports with it. (Arrival times jitter
        by a telemetry period, a few percent of two seconds.)"""
        self.tele = None
        self.wait(lambda: self.tele and self.tele[1].get("tick") is not None, 5, "playhead")
        v0 = self.tele[1]
        self.run(seconds)
        v1 = self.tele[1]
        frames = v1["engines"][0]["frame"] - v0["engines"][0]["frame"]
        return (v1["tick"] - v0["tick"]) * 48000.0 / frames

    def clock_rate(self, seconds=6.0):
        """The box's frames a second of this machine's time: the sample rate
        the box really plays at, to within a telemetry period over ``seconds``."""
        self.tele = None
        self.wait(lambda: self.tele is not None, 5, "telemetry")
        t0, v0 = self.tele
        self.run(seconds)
        t1, v1 = self.tele
        return (v1["engines"][0]["frame"] - v0["engines"][0]["frame"]) / (t1 - t0)


def browser_steps(pr, host, port):
    """The page the box serves, in headless Chromium: it shows the song, and
    its Play and mute buttons reach the box."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto("http://%s:%d/" % (host, port))
        page.wait_for_function("window.ostinato.ready", timeout=15000)
        name = page.evaluate("document.querySelector('#name').textContent")
        check(name == pr.panel.doc.get("name"), "the box's page shows the song (%r)" % name)
        before = pr.box().get("notes", 0)
        page.click("#play")
        pr.run(2.0)
        tele = pr.tele[1] if pr.tele else {}
        check(tele.get("playing") and pr.box().get("notes", 0) > before,
              "the page's Play starts the box (%s notes)" % (pr.box().get("notes", 0) - before))
        page.click(".track[data-track='1'] .mute")
        pr.run(1.0)
        check(pr.panel.doc["tracks"][1]["mute"] is True, "the page's mute reaches the box and this panel")
        page.click(".track[data-track='1'] .mute")
        page.click("#play")
        pr.run(1.0)
        check(not pr.tele[1].get("playing"), "and its Stop stops it")
        browser.close()


def main(argv):
    host, _, port = argv[1].rpartition(":")
    song_path = os.path.join(OSTINATO, "songs", "sketches", "soundfont_check.json")
    if "--song" in argv:
        song_path = argv[argv.index("--song") + 1]
    with open(song_path) as f:
        song = json.load(f)
    out = {}

    pr = Probe(host or "127.0.0.1", int(port))
    p = pr.panel
    p.command("listen", me=p.me)
    for _ in range(10):
        p.ping()
        pr.run(0.1)
    pings = sorted(pr.pongs)
    out["ping_ms"] = pings[len(pings) // 2] if pings else None
    print("link: ping median %.1f ms (%d)" % (out["ping_ms"] or -1, len(pings)))

    # Load: the box opens the song's samples and says what they cost. A box
    # already holding this song has nothing new to read.
    had = p.doc.get("name") == song.get("name") and len(p.doc["tracks"]) == len(song["tracks"])
    t = time.perf_counter()
    p.command("load", project=song, by=[p.me, 1])
    pr.wait(lambda: any(e.get("name") == "loaded" for e in pr.events), 60, "load")
    pr.wait(lambda: p.ready and p.doc.get("name") == song.get("name"), 10, "the loaded song")
    out["load_ms"] = (time.perf_counter() - t) * 1000.0
    pr.run(0.5)
    pre = [e["msg"] for e in pr.events if e.get("name") == "engine" and e["msg"].get("t") == "preloaded"]
    out["preloaded"] = pre
    for m in pre:
        print("box: track %s preloaded %d samples, %.1f MB in %d ms" % (
            m["track"], m["samples"], m["bytes"] / 1048576.0, m["ms"]))
    failed = [e["msg"] for e in pr.events if e.get("name") == "engine" and e["msg"].get("t") == "failed"]
    check(not failed, "every track built (%s)" % [m.get("why") for m in failed])
    check(len(pre) == len(song["tracks"]) or (had and not pre),
          "every track preloaded its samples (%d of %d%s), load %.0f ms" % (
              len(pre), len(song["tracks"]), ", the box had them" if had and not pre else "", out["load_ms"]))

    p.command("play", tick=0.0, by=p.me)
    pr.run(1.0)
    tempo = p.doc["tempo"]
    r = pr.rate()
    want = tempo * model.PPQ / 60.0
    check(abs(r - want) < want * 0.005, "the playhead moves at %.0f bpm (%.1f ticks/s, want %.1f)" % (tempo, r, want))

    sr = pr.clock_rate()
    out["clock_hz"] = sr
    if "microsoft" in platform.release().lower():
        # WSL's clock runs about 2% fast against Windows', so it can't judge this.
        print("skip the box's clock: %.0f frames a second by WSL's clock; run this from Windows" % sr)
    else:
        check(abs(sr - 48000) < 48000 * 0.01, "the box's clock runs at 48 kHz (%.0f frames a second)" % sr)

    ids = {}
    ids["tempo"] = pr.send("replace", "/tempo", 140.0)
    pr.run(0.5)
    r = pr.rate()
    want = 140.0 * model.PPQ / 60.0
    check(abs(r - want) < want * 0.005, "after a tempo change, 140 bpm (%.1f ticks/s, want %.1f)" % (r, want))

    ids["mute"] = pr.send("replace", "/tracks/0/mute", True)
    pr.run(1.0)
    ids["note"] = pr.send("add", "/tracks/1/patterns/0/notes/-", [model.STEP * 2, model.STEP, 43, 100])
    pr.run(1.0)
    ids["rack"] = pr.send("replace", "/master/rack", [{"name": "Reverb", "bypass": False, "macros": {}}])
    pr.run(3.0)
    box = pr.box()
    out["box_playing"] = box
    p.command("stop", by=p.me)
    pr.run(0.5)

    echoes = {k: pr.echo.get(i) for k, i in ids.items()}
    out["echo_ms"] = echoes
    print("link: op to echo, ms:", ", ".join("%s %.1f" % (k, v) for k, v in echoes.items() if v is not None))
    check(None not in echoes.values(), "every op came back from the box")
    print("box:", json.dumps(box))
    pump = box.get("pump", {})
    check(box.get("late") == 0, "no late notes (%s)" % box.get("late"))
    check(pump.get("underruns") == 0, "no underruns (%s)" % pump.get("underruns"))
    check(box.get("notes", 0) > 20, "the box played the song (%s notes)" % box.get("notes"))

    if "--browser" in argv:
        browser_steps(pr, host or "127.0.0.1", int(port))

    # A second panel, fresh: the box's song is the one this panel has.
    other = Probe(host or "127.0.0.1", int(port))
    same = json.dumps(other.panel.doc, sort_keys=True) == json.dumps(p.doc, sort_keys=True)
    check(same, "a panel joining now gets the same song (mute %s here, %s on the box)" % (
        p.doc["tracks"][0]["mute"], other.panel.doc["tracks"][0]["mute"]))
    other.panel.close()
    p.close()

    if "--json" in argv:
        with open(argv[argv.index("--json") + 1], "w") as f:
            json.dump(out, f, indent=1)
    print("vamp link check:", "FAILED: " + "; ".join(FAILS) if FAILS else "all passed")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
