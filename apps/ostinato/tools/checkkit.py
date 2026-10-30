"""One app session for the checks: a server, a page, the app booted once.

    from checkkit import Session
    with Session(cdp) as s:          # boots the app
        s.load(project)               # a new project, returns once it's built
        s.app.click_rect(s.app.geom()["play"])

Each check has a ``run(s)`` that takes a session and returns its results,
and a ``main()`` that makes its own; tools/check_all.py runs them all in one.
A check starts from ``s.load()`` (or ``s.reset()``), not from a page reload:
a reload starts six Python workers again, about 15 s.

The desktop app has a session with the same calls (tools/desktopkit.py,
through its control socket): ``--desktop processes|threads|threads-ft``
instead of ``--cdp`` runs a check against ``python -m ostinato``. A check
that keeps to the session's and the app's calls (not ``s.page``) runs
against either: ``s.mouse`` and ``s.keyboard`` are Playwright's on the
browser and stand-ins with the same calls on the desktop, and ``s.pixel``,
``s.screenshot``, ``s.resize`` and ``s.picker_calls`` work on both.
"""

import base64
import json
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "app" / "shared"))

import model  # noqa: E402
from groovebox_check import App  # noqa: E402

PORT = 8770


class Session:
    def __init__(self, cdp="", port=PORT):
        self.cdp = cdp
        self.port = port
        self.log = []          # the page's console lines with errors in them
        self.navigations = 0   # page loads, the checks' own and any other (a window resize reloads)
        self.expected = 0      # of those, the ones a check asked for

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        self.server = subprocess.Popen([sys.executable, str(ROOT / "tools" / "serve.py"), "--port", str(self.port)],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)
        self.pw = sync_playwright().start()
        if self.cdp:
            self.browser = self.pw.chromium.connect_over_cdp(self.cdp)
            self.ctx = self.browser.new_context(no_viewport=True)
        else:
            self.browser = self.pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
            self.ctx = self.browser.new_context(viewport={"width": 1920, "height": 1080})
        for script in ("fake_webmidi.js", "fake_pickers.js"):
            self.ctx.add_init_script(path=str(ROOT / "tools" / script))
        self.page = self.ctx.new_page()
        pg = self.page
        self.mouse, self.keyboard = pg.mouse, pg.keyboard      # the desktop session has the same
        keep = lambda text: ("rror" in text or "Traceback" in text) and self.log.append(text[:300])
        pg.on("console", lambda m: keep(m.text))
        pg.on("pageerror", lambda e: self.log.append("page error: %s" % e))
        pg.on("framenavigated", lambda f: f == pg.main_frame and self._navigated(f.url))
        self.boot()
        return self

    def __exit__(self, *exc):
        try:
            self.ctx.close()
            if not self.cdp:
                self.browser.close()
            self.pw.stop()
        finally:
            self.server.terminate()

    def _navigated(self, url):
        self.navigations += 1

    def url(self, query="fresh=1"):
        return "http://127.0.0.1:%d/index.html?%s" % (self.port, query)

    def boot(self, query="fresh=1"):
        """Load the page and start the sound (the click a browser needs)."""
        pg = self.page
        self.expected += 1
        pg.goto(self.url(query))
        pg.wait_for_function("window.__appReady || window.__appError", timeout=120_000)
        err = pg.evaluate("window.__appError || null")
        if err:
            raise RuntimeError("the app didn't start: %s" % err)
        vw, vh = pg.evaluate("[innerWidth, innerHeight]")
        pg.mouse.click(vw // 2, vh // 2)
        self.app = App(pg)
        self.settle()
        return self.app

    # -- the app ------------------------------------------------------------------

    def send(self, msg):
        self.page.evaluate("m => window.__ostinato.ui.send(m)", msg)

    def settle(self, timeout=60.0):
        """Wait until every engine has built its tracks since the last load."""
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            self.page.evaluate("window.__settled = null; window.__ostinato.ui.send({kind: 'settled'})")
            try:
                self.page.wait_for_function("window.__settled !== null", timeout=2000)
            except Exception:
                continue        # an engine building an instrument holds up the reply
            if self.page.evaluate("window.__settled"):
                time.sleep(0.3)  # the worklet's queue fills again after a rebuild
                return time.monotonic() - t0
            time.sleep(0.1)
        raise TimeoutError("the engines never settled")

    def load(self, project):
        """Open ``project`` as File > Open would, and wait until it's built."""
        self.send({"kind": "loaded", "json": json.dumps(project)})
        time.sleep(0.2)
        self.settle()

    def reset(self, project=None):
        """As if the app had just opened, on ``project`` (a new one by default)."""
        self.send({"kind": "reset", "json": json.dumps(project or model.new_project())})
        time.sleep(0.2)
        self.settle()

    def op(self, op):
        self.send({"kind": "op", "op": op})

    def stats(self):
        return self.page.evaluate("window.__ostinato.audio.stats()")

    # -- a long take of what plays (tools/timing_check.py) ---------------------------

    def tap_on(self):
        from groovebox_check import TAKE

        self._take = TAKE
        self.page.evaluate("window.__ostinato.audio.tapChunks.length = 0; window.__ostinato.audio.tap(true)")
        self.page.wait_for_function("window.__ostinato.audio.tapChunks.length > 0", timeout=10_000)
        self._held = self.page.evaluate(TAKE)

    def tap_poll(self):
        """Collect what's arrived (the page holds the chunks until then)."""
        self._held += self.page.evaluate(self._take)

    def tap_off(self):
        """Stop the take: (mono float32, the context frame of its first sample)."""
        self.page.evaluate("window.__ostinato.audio.tap(false)")
        time.sleep(0.3)
        self.tap_poll()
        return assemble(self._held)

    def now_frame(self):
        """The output's context frame playing now."""
        return self.page.evaluate("Math.round(window.__ostinato.audio.context.currentTime * 48000)")

    def restart(self):
        """Start the app again, not fresh: the autosave comes back."""
        return self.boot(query="")

    # -- files: tools/fake_pickers.js stands in for the pickers --------------------

    def clear_files(self):
        self.page.evaluate("window.__files = {}")

    def file(self, ext):
        """The bytes of the file last saved with a name ending ``ext``, or None."""
        b64 = self.page.evaluate("""ext => {
            const hit = Object.entries(window.__files).find(([k]) => k.endsWith(ext));
            if (!hit) return null;
            const bufs = hit[1].map((p) => typeof p === "string" ? new TextEncoder().encode(p)
                                    : p instanceof ArrayBuffer ? new Uint8Array(p)
                                    : new Uint8Array(p.buffer, p.byteOffset, p.byteLength));
            let s = "";
            for (const u8 of bufs)
                for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
            return btoa(s);
        }""", ext)
        return None if b64 is None else base64.b64decode(b64)

    def wait_file(self, ext, timeout=10.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            data = self.file(ext)
            if data is not None:
                return data
            time.sleep(0.2)
        raise TimeoutError("nothing saved ending %s" % ext)

    def set_open(self, text):
        """What File > Open opens next."""
        self.page.evaluate("s => { window.__openPayload = s; }", text)

    def last_export(self):
        return self.page.evaluate("window.__lastExport || null")

    def export_progress(self):
        return self.page.evaluate("window.__exportProgress()")

    def picker_calls(self):
        """How many times the app asked where to save or what to open."""
        return self.page.evaluate("window.__pickerCalls")

    def reset_pickers(self):
        self.page.evaluate("window.__pickerCalls = 0")

    def screenshot(self, path):
        self.page.screenshot(path=path)

    def pixel(self, x, y):
        """The colour on screen at canvas pixel (x, y), from a one-pixel screenshot
        (decoded here: a 1x1 PNG is one filter byte and one pixel)."""
        import struct
        import zlib

        dpr = self.app.dpr
        png = self.page.screenshot(clip={"x": x / dpr, "y": y / dpr, "width": 1, "height": 1})
        at, data = 8, b""
        while at < len(png):
            n, kind = struct.unpack(">I4s", png[at:at + 8])
            if kind == b"IDAT":
                data += png[at + 8: at + 8 + n]
            at += 12 + n
        return tuple(zlib.decompress(data)[1:4])

    # -- the window: the browser's resize (the canvas stays its size, nothing reloads)

    def resize(self, frac):
        pg = self.page
        w, h = pg.evaluate("[innerWidth, innerHeight]")
        self._cdp = self.ctx.new_cdp_session(pg)
        self._cdp.send("Emulation.setDeviceMetricsOverride", {"width": int(w * frac), "height": int(h * frac),
                                                              "deviceScaleFactor": self.app.dpr, "mobile": False})
        time.sleep(1.5)

    def unresize(self):
        self._cdp.send("Emulation.clearDeviceMetricsOverride")
        time.sleep(1.0)

    def window(self):
        w, h = self.page.evaluate("[innerWidth, innerHeight]")
        return {"client": [w, h]}


def assemble(chunks):
    """The tap's [start frame, channels, base64 float32] chunks as one mono take, and its first frame."""
    import numpy as np

    chunks = sorted(chunks)
    first = chunks[0][0]
    frames = chunks[-1][0] - first + len(base64.b64decode(chunks[-1][2])) // 4 // chunks[-1][1]
    x = np.zeros(frames, dtype=np.float32)
    for st_, ch, b64 in chunks:
        d = np.frombuffer(base64.b64decode(b64), dtype=np.float32).reshape(-1, ch).mean(axis=1)
        x[st_ - first: st_ - first + len(d)] = d
    return x, first


def add_session_args(ap):
    ap.add_argument("--cdp", default="", help="drive this Chrome (tools/win_chrome.sh) instead of headless Chromium")
    ap.add_argument("--desktop", default="", choices=("", "processes", "threads", "threads-ft", "split"),
                    help="run against the desktop app (python -m ostinato) with this runner instead of a browser")
    ap.add_argument("--size", default=None, help="desktop: a WxH window instead of maximised")


def session(a):
    """The session ``--cdp`` / ``--desktop`` ask for."""
    if getattr(a, "desktop", ""):
        from desktopkit import DesktopSession

        return DesktopSession(a.desktop, size=getattr(a, "size", None))
    return Session(a.cdp)


def report(name, results):
    """Print a check's results in the house format and return whether all passed.
    ``results``: name -> dict with "pass", or a list of (ok, line)."""
    ok_all = True
    if isinstance(results, list):
        for ok, line in results:
            print("%s  %s" % ("ok  " if ok else "FAIL", line), flush=True)
            ok_all &= bool(ok)
    else:
        for k, r in results.items():
            ok = r.get("pass", False)
            ok_all &= bool(ok)
            print("%-9s %s  %s" % (k, "PASS" if ok else "FAIL", {a: b for a, b in r.items() if a != "pass"}), flush=True)
    return ok_all
