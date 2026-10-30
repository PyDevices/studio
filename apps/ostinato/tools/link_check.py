"""The window and the browser page as two panels of one device agree on the song.

    ~/gh/pydevices/.toolbox/bin/python tools/link_check.py [--defeat]

It runs the device (desktop/device.py) in this process with stand-in
engines, the desktop UI's side of the link (desktop/ui/engines.py over
desktop/linkrunner.py) as one panel, and the song page (songlink's page.html)
in headless Chromium as the other. Then:

- the window loads a song and edits it (tempo, a note, a mute): the page shows each;
- the page edits it (a step, a mute, a volume): the window's project has each,
  and the window was told to redraw;
- both append a note to the same pattern at once: all three end the same;
- the page presses Play: the window hears that the song is playing;
- the device sends two of the page's ops to the window in the wrong order
  (a planted fault): the window notices, takes the song again, and agrees;
  the same for two of the window's ops to the page.

``--defeat`` turns off the window's order check, the guard the planted
fault is for: the check must fail.

Linux or Windows, any CPython with Playwright (the toolbox's Python has it);
no audio is made.
"""

import json
import os
import socket
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import ostinato  # noqa: E402,F401  (sys.path, as the desktop app has it)

import hmilink  # noqa: E402

import device as devmod  # noqa: E402
import engines as ui_engines  # noqa: E402
from songlink import model  # noqa: E402
from linkrunner import LinkRunner  # noqa: E402

SR = 48000
results = []


def check(ok, line):
    results.append((bool(ok), line))
    print(("ok    " if ok else "FAIL  ") + line, flush=True)
    return ok


class FakeRunner:
    """Engines that answer where they are and keep what they were sent."""

    def __init__(self, count):
        self.count = count
        self.sent = []
        self.out = []
        self.t0 = time.perf_counter_ns()

    def broadcast(self, msg):
        if msg.get("t") == "status":
            ns = time.perf_counter_ns()
            frame = (ns - self.t0) * SR // 1_000_000_000
            for i in range(self.count):
                self.out.append({"t": "status", "engine": i, "frame": frame, "clock": [frame, ns], "tick": None})
            return
        self.sent.append(("all", msg))

    def mixer(self, msg):
        if msg.get("t") != "stats":
            self.sent.append(("mixer", msg))

    def send(self, i, msg):
        self.sent.append((i, msg))

    def poll(self):
        out, self.out = self.out, []
        return out


class DeviceThread(threading.Thread):
    def __init__(self, port, plant):
        super().__init__(daemon=True)
        self.runner = FakeRunner(2)
        self.dev = devmod.SongDevice(self.runner, 2, port=port, bind="127.0.0.1", plant=plant, log=lambda s: None)
        self.dev.start()
        self.lock = threading.Lock()
        self.running = True

    def run(self):
        while self.running:
            with self.lock:
                self.dev.poll()
            time.sleep(0.002)

    def doc(self):
        with self.lock:
            return json.loads(json.dumps(self.dev.model.doc))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def song():
    p = model.new_project()
    p["name"] = "link check"
    p["tracks"] = p["tracks"][:2]
    s = model.STEP
    p["tracks"][0]["patterns"][0]["notes"] = [[0, s, 36, 110], [8 * s, s, 36, 110], [4 * s, s, 38, 100]]
    p["tracks"][1]["patterns"][0]["notes"] = [[0, s, 36, 100], [6 * s, s, 43, 100]]
    return p


class Desktop:
    """The window's side of the link, with no window: what app/ui/main.py drives."""

    def __init__(self, port):
        self.runner = LinkRunner("127.0.0.1", port, connect_s=10.0)
        self.runner.start()
        ui_engines.runner = self.runner
        self.project = song()
        self.engines = ui_engines.Engines(2, self.project)
        self.remote = []
        self.engines.on_remote.append(self.remote.extend)
        self.engines.load(self.project)

    def pump(self, seconds=0.3):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.engines.poll()
            time.sleep(0.01)


def wait(page, desktop, dev, why, seconds=5.0):
    """Pump until the window, the page and the device hold the same song."""
    end = time.monotonic() + seconds
    while True:
        desktop.pump(0.1)
        d = dev.doc()
        ready = page.evaluate("window.ostinato.ready")
        p = page.evaluate("JSON.stringify(window.ostinato.doc)") if ready else "null"
        same_page = ready and json.loads(p) == d
        same_desk = json.loads(json.dumps(desktop.project)) == d
        if (same_page and same_desk) or time.monotonic() > end:
            return check(same_page and same_desk, "%s: window %s, page %s" % (
                why, "agrees" if same_desk else "DIFFERS", "agrees" if same_page else "DIFFERS"))


def notes(doc, i=0):
    return doc["tracks"][i]["patterns"][0]["notes"]


def scenario(browser, plant=None, defeat=False):
    port = free_port()
    dt = DeviceThread(port, plant)
    dt.start()
    desktop = Desktop(port)
    page = browser.new_page()
    page.goto("http://127.0.0.1:%d/" % port)
    page.wait_for_function("window.ostinato.ready", timeout=10000)
    try:
        if plant is None:
            basics(page, desktop, dt)
        elif plant == "swap":
            planted_window(page, desktop, dt, defeat)
        else:
            planted_page(page, desktop, dt)
    finally:
        page.close()
        desktop.runner.stop()
        dt.running = False
        dt.join(2)
        dt.dev.close()


def basics(page, desktop, dt):
    s = model.STEP
    wait(page, desktop, dt, "the window's song reached the device and the page")
    check(page.evaluate("document.querySelector('#name').textContent") == "link check", "the page shows its name")

    # The window edits.
    e = desktop.engines
    drums, bass = desktop.project["tracks"][0]["id"], desktop.project["tracks"][1]["id"]
    e.op({"op": "set", "path": ["tempo"], "value": 133.0})
    e.op({"op": "insert", "path": [["track", drums], "patterns", 0, "notes", -1], "value": [12 * s, s, 42, 90]})
    e.op({"op": "set", "path": [["track", bass], "mute"], "value": True})
    e.op({"op": "remove", "path": [["track", bass], "patterns", 0, "notes", 0]})
    wait(page, desktop, dt, "the window's tempo, note, mute and removed note")
    check(page.evaluate("document.querySelector('#tempo').value") == "133", "the page's tempo reads 133")
    check(page.evaluate("document.querySelectorAll('.track .mute')[1].classList.contains('on')"),
          "the page's Bass shows muted")
    check(page.evaluate("document.querySelector('.cell[data-step=\"12\"][data-pitch=\"42\"]').classList.contains('on')"),
          "the page's grid shows the window's new hat")
    sent = [m for _, m in dt.runner.sent if m.get("t") == "op"]
    check(any(m["op"].get("path") == ["tempo"] for m in sent), "the device gave the engines the tempo")

    # The page edits.
    desktop.remote.clear()
    page.click('.cell[data-step="2"][data-pitch="38"]')
    page.click(".track[data-track='0'] .mute")
    page.evaluate("window.ostinato.send('replace', '/tracks/1/volume', 64)")
    wait(page, desktop, dt, "the page's step, mute and volume")
    check([2 * s, s, 38, 100] in notes(desktop.project), "the window's project has the page's step")
    check(desktop.project["tracks"][0]["mute"] is True and desktop.project["tracks"][1]["volume"] == 64,
          "and its mute and volume")
    check(len([c for c in desktop.remote if "path" in c]) >= 3, "the window was told to redraw (%d)" % len(desktop.remote))

    # Both append to the same pattern at once.
    page.evaluate("window.ostinato.send('add', '/tracks/0/patterns/0/notes/-', [%d, %d, 49, 100])" % (14 * s, s))
    e.op({"op": "insert", "path": [["track", drums], "patterns", 0, "notes", -1], "value": [15 * s, s, 46, 100]})
    e.op({"op": "insert", "path": [["track", drums], "patterns", 0, "notes", -1], "value": [13 * s, s, 39, 100]})
    page.evaluate("window.ostinato.send('add', '/tracks/0/patterns/0/notes/-', [%d, %d, 51, 100])" % (10 * s, s))
    wait(page, desktop, dt, "four crossed appends to one pattern")
    st = desktop.runner.panel.stats
    print("      window panel: %s" % st)
    # An edit by index while another panel's remove is on its way: the window
    # edits note 1 after the device has removed note 0 but before the window
    # has heard. It must land on the same note everywhere.
    before = len(notes(dt.doc()))
    page.evaluate("window.ostinato.send('remove', '/tracks/0/patterns/0/notes/0')")
    end = time.monotonic() + 3
    while len(notes(dt.doc())) == before and time.monotonic() < end:
        time.sleep(0.01)
    e.op({"op": "set", "path": [["track", drums], "patterns", 0, "notes", 1, 3], "value": 1})
    wait(page, desktop, dt, "a remove and an index edit after the crossing")

    # The page presses Play; the window hears it.
    desktop.remote.clear()
    page.click("#play")
    end = time.monotonic() + 3
    while not desktop.engines.playing and time.monotonic() < end:
        desktop.pump(0.05)
    check(desktop.engines.playing, "the page pressed Play and the window shows it playing")
    check(dt.dev.playing, "and the device started the engines")
    page.click("#play")
    end = time.monotonic() + 3
    while desktop.engines.playing and time.monotonic() < end:
        desktop.pump(0.05)
    check(not desktop.engines.playing, "and Stop")

    # The window loads another song: the page has it.
    other = song()
    other["name"] = "second song"
    other["tempo"] = 90.0
    desktop.engines.load(other)
    desktop.project = desktop.engines.project
    wait(page, desktop, dt, "the window's New/Open")
    check(page.evaluate("document.querySelector('#name').textContent") == "second song", "the page shows the new song")


def planted_window(page, desktop, dt, defeat):
    s = model.STEP
    wait(page, desktop, dt, "planted: before the swap")
    if defeat:
        # Turn off the window's order check: every op looks like the next one.
        panel = desktop.runner.panel
        inner = panel._handle

        def blind(msg):
            if msg.get("t") == "op" and "seq" in msg:
                msg["seq"] = panel.seq + 1
            inner(msg)

        panel._handle = blind
    page.evaluate("window.ostinato.send('add', '/tracks/0/patterns/0/notes/-', [%d, %d, 49, 100])" % (14 * s, s))
    page.evaluate("window.ostinato.send('add', '/tracks/0/patterns/0/notes/-', [%d, %d, 51, 100])" % (10 * s, s))
    wait(page, desktop, dt, "planted: two of the page's ops sent to the window in the wrong order", 3.0)
    st = desktop.runner.panel.stats
    check(st.get("out_of_order", 0) >= 1 and st.get("resyncs", 0) >= 1,
          "planted: the window caught it and took the song again (%s)" % st)


def planted_page(page, desktop, dt):
    s = model.STEP
    drums = desktop.project["tracks"][0]["id"]
    wait(page, desktop, dt, "planted: before the swap")
    e = desktop.engines
    e.op({"op": "insert", "path": [["track", drums], "patterns", 0, "notes", -1], "value": [15 * s, s, 46, 100]})
    e.op({"op": "insert", "path": [["track", drums], "patterns", 0, "notes", -1], "value": [13 * s, s, 39, 100]})
    wait(page, desktop, dt, "planted: two of the window's ops sent to the page in the wrong order", 3.0)
    st = page.evaluate("window.ostinato.stats")
    check(st["order"] >= 1 and st["resyncs"] >= 1, "planted: the page caught it and took the song again (%s)" % st)


def main():
    from playwright.sync_api import sync_playwright

    defeat = "--defeat" in sys.argv
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            if not defeat:
                print("-- the window and the page edit one song")
                scenario(browser)
                print("-- planted: the page sees two ops swapped")
                scenario(browser, plant="swap-page")
            print("-- planted: the window sees two ops swapped%s" % (", its order check off" if defeat else ""))
            scenario(browser, plant="swap", defeat=defeat)
        finally:
            browser.close()
    bad = [line for ok, line in results if not ok]
    print("link check: %s" % ("FAILED (%d)" % len(bad) if bad else "passed"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
