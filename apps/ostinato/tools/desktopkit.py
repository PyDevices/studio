"""The checks' session against the desktop app (python -m ostinato on Windows).

    from desktopkit import DesktopSession
    with DesktopSession("processes") as s:      # or "threads", "threads-ft", "split"
        s.load(project)
        s.app.click_rect(s.app.geom()["play"])

The same calls as checkkit.Session (the browser's), over the app's control
socket (desktop/control.py) instead of Chrome's DevTools: UI messages and
their answers, the mouse posted to the window, keys into its event stream,
MIDI played into "Ostinato Loopback", and the mixer's tap for listening.
It starts the app with tools/win_run.sh, fresh, on its own data folder, with
a folder standing in for the file dialogs, and stops it at the end.
"""

import json
import os
import pathlib
import socket
import subprocess
import sys
import time

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent.parent / "lib"))

from songlink import model  # noqa: E402

SR = 48000
PORT = 8771
WSTAGE = os.environ.get("STAGE", r"C:\temp\ostinato-desktop")
STAGE = pathlib.Path(subprocess.run(["wslpath", "-u", WSTAGE], capture_output=True, text=True).stdout.strip())
RUNS = "runs\\check"          # under the staging folder: the app's files, data and taps
_TAKES = [0]


def win(*parts):
    return "\\".join((WSTAGE, RUNS) + parts)


def wsl(*parts):
    return STAGE.joinpath(*RUNS.split("\\"), *parts)


class DesktopApp:
    """What a check does to the app: the same calls as groovebox_check.App."""

    dpr = 1                     # LVGL is the window's client area at scale 1

    def __init__(self, sess):
        self.s = sess

    def geom(self):
        return self.s.ask({"kind": "geom"}, "geom")

    def project(self):
        return json.loads(self.s.ask({"kind": "project"}, "project")["json"])

    def click_rect(self, r):
        self.click(r[0] + r[2] / 2, r[1] + r[3] / 2)
        time.sleep(0.25)

    def click(self, x, y):
        self.s.send({"desk": "mouse", "op": "click", "x": round(x), "y": round(y)})
        time.sleep(0.05)

    def move(self, x, y):
        self.s.send({"desk": "mouse", "op": "move", "x": round(x), "y": round(y)})
        self._at = (x, y)

    def down(self):
        x, y = getattr(self, "_at", (0, 0))
        self.s.send({"desk": "mouse", "op": "down", "x": round(x), "y": round(y)})

    def up(self):
        x, y = getattr(self, "_at", (0, 0))
        self.s.send({"desk": "mouse", "op": "up", "x": round(x), "y": round(y)})

    def key(self, key, mod=(), op="press"):
        """A key into the window (press, down or up): SPACE, ESCAPE, RETURN,
        LSHIFT, DELETE or a letter, with "ctrl", "shift" or "caps" held."""
        self.s.send({"desk": "key", "key": key, "mod": list(mod), "op": op})
        time.sleep(0.1)

    def wheel(self, dy, x, y):
        self.s.send({"desk": "wheel", "dy": dy, "x": round(x), "y": round(y)})
        time.sleep(0.1)

    def fresh(self):
        self.s.send({"kind": "loaded", "json": json.dumps(model.new_project())})
        time.sleep(1.5)

    def select(self, i):
        self.s.send({"kind": "select", "index": i})
        time.sleep(0.6)

    def play_midi(self, events):
        """[[ms from now, [status, d1, d2]], ...] through the MIDI loopback."""
        self.s.send({"desk": "midi", "events": events})

    def uistats(self):
        return self.s.ask({"kind": "uistats"}, "uistats")

    def masterstats(self):
        """Each track's and the master's meter, as the UI reads them."""
        m = self.s.ask({"kind": "masterstats"}, "masterstats")
        return dict(m.get("stats") or {}, engines=m.get("engines"), meters=m.get("meters"))   # as index.html has it

    def record(self, seconds, before=None):
        """What the mixer plays, from the moment its tap opens: do ``before``,
        then ``seconds`` of it, as mono float32."""
        _TAKES[0] += 1
        name = "take%d" % _TAKES[0]
        self.s.ask({"desk": "tap", "on": True, "folder": win("taps", name)}, "tap_open", timeout=10)
        if before:
            before()
        time.sleep(seconds + 0.3)
        self.s.ask({"desk": "tap", "on": False, "folder": win("taps", name)}, "tap_closed", timeout=20)
        raw = np.fromfile(wsl("taps", name, "mix.raw"), dtype="<i2")
        out = (raw.reshape(-1, 2).astype(np.float32) / 32768.0).mean(axis=1)
        try:
            import soundfile

            soundfile.write(str(ROOT / "runs" / ("desktop_%s.wav" % name)), out, SR)
        except Exception:  # noqa: BLE001
            pass
        return out


class Mouse:
    """Playwright's ``page.mouse`` for the desktop app, in window pixels (the
    canvas's, at scale 1), so a check written against the browser's mouse
    drives either front end through ``s.mouse``."""

    def __init__(self, app):
        self.app = app

    def move(self, x, y, steps=1):
        self.app.move(x, y)

    def down(self, button="left"):
        self.app.down()

    def up(self, button="left"):
        self.app.up()

    def click(self, x, y):
        self.app.click(x, y)

    def dblclick(self, x, y):
        self.app.click(x, y)
        time.sleep(0.05)
        self.app.click(x, y)

    def wheel(self, dx, dy):
        x, y = getattr(self.app, "_at", (0, 0))
        self.app.wheel(dy, x, y)


# Playwright's key names to the app's (desktop/control.py: keys.K_<name> or a letter).
_KEYS = {"Enter": "RETURN", "Delete": "DELETE", "Backspace": "BACKSPACE", "Escape": "ESCAPE", "Space": "SPACE",
         " ": "SPACE", "Shift": "LSHIFT", "Control": "LCTRL", "Tab": "TAB"}
_MODS = {"Shift": "shift", "Control": "ctrl", "Alt": "alt", "Meta": "ctrl"}


class Keyboard:
    """Playwright's ``page.keyboard`` for the desktop app: "Shift+B",
    "Control+c", "Enter", down("Shift")... into the window's event stream."""

    def __init__(self, app):
        self.app = app
        self.held = set()

    def _split(self, combo):
        *mods, key = combo.split("+") if combo != "+" else ["+"]
        mod = sorted({_MODS[m] for m in mods} | {_MODS[h] for h in self.held if h in _MODS})
        if len(key) == 1 and key.isalpha():
            if key.isupper() and "shift" not in mod:
                mod.append("shift")
            key = key.lower()
        return _KEYS.get(key, key), mod

    def press(self, combo):
        key, mod = self._split(combo)
        self.app.key(key, mod)

    def down(self, key):
        self.held.add(key)
        self.app.key(_KEYS.get(key, key), op="down")

    def up(self, key):
        self.held.discard(key)
        self.app.key(_KEYS.get(key, key), op="up")

    def type(self, text):
        """As keyboard.type does: capitals without Shift held, as Caps Lock types them."""
        for ch in text:
            if ch == " ":
                self.app.key("SPACE")
            elif ch.isalpha() and ch.isupper():
                self.app.key(ch.lower(), ["caps"])
            else:
                self.app.key(ch)


class DesktopSession:
    def __init__(self, runner="processes", port=PORT, size=None):
        self.runner = runner
        self.port = port
        self.size = size           # "WxH": a window of this size instead of maximised
        self.log = []
        self.navigations = 0
        self.expected = 0
        self.proc = None
        self.sock = None
        self.app = DesktopApp(self)
        self.mouse = Mouse(self.app)
        self.keyboard = Keyboard(self.app)
        self.env = {}              # extra environment for the app's process (the offline check's dead proxy)
        (ROOT / "runs").mkdir(exist_ok=True)
        self.logfile = ROOT / "runs" / ("desktop_%s.log" % runner)

    def __enter__(self):
        import shutil

        subprocess.run([str(ROOT / "tools" / "win_kill.sh")], capture_output=True)
        shutil.rmtree(wsl(), ignore_errors=True)
        for d in ("files", "data", "taps"):
            wsl(d).mkdir(parents=True, exist_ok=True)
        self.boot(fresh=True)
        return self

    def __exit__(self, *exc):
        self.stop()
        self._scan_log()

    # -- the app's process ------------------------------------------------------------

    def boot(self, fresh=True, query=None):
        """Start the app and wait until its engines have built their tracks."""
        if query is not None:
            fresh = "fresh" in query
        # "split": the engines and the song in a device process, the window its panel (--split).
        runner = "processes" if self.runner == "split" else self.runner
        args = [str(ROOT / "tools" / "win_run.sh"), runner, "--control", str(self.port),
                "--midi", "Ostinato Loopback", "--midi-out", "Ostinato Loopback",
                "--test-files", win("files"), "--data", win("data")]
        if self.runner == "split":
            args.append("--split")
        if fresh:
            args.append("--fresh")
        if self.size:
            args += ["--size", self.size]
        out = open(self.logfile, "a")
        out.write("\n== start %s %s\n" % (time.strftime("%H:%M:%S"), " ".join(args[1:])))
        out.flush()
        env = dict(os.environ, TIMEOUT="7200")
        for k, v in self.env.items():
            env[k] = env["WSLENV"] + ":" + v if k == "WSLENV" and env.get("WSLENV") else v
        self.proc = subprocess.Popen(args, stdout=out, stderr=subprocess.STDOUT, env=env)
        self.expected += 1
        self.navigations += 1
        self._connect(120)
        end = time.monotonic() + 120
        while not self.ask({"desk": "ping"}, "pong").get("ready"):
            if time.monotonic() > end:
                raise RuntimeError("the desktop app didn't start (%s)" % self.logfile)
            time.sleep(0.5)
        self.settle()
        return self.app

    def restart(self):
        self.stop()
        return self.boot(fresh=False)

    def stop(self):
        if self.sock is not None:
            try:
                self.send({"desk": "quit"})
            except OSError:
                pass
        if self.proc is not None:
            try:
                self.proc.wait(20)
            except subprocess.TimeoutExpired:
                pass
        subprocess.run([str(ROOT / "tools" / "win_kill.sh")], capture_output=True)
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
        self.proc = None
        if self.sock is not None:
            self.sock.close()
            self.sock = None
        time.sleep(1)

    def _connect(self, timeout):
        end = time.monotonic() + timeout
        while True:
            try:
                self.sock = socket.create_connection(("127.0.0.1", self.port), timeout=2)
                break
            except OSError:
                if time.monotonic() > end or (self.proc is not None and self.proc.poll() is not None):
                    raise RuntimeError("no control socket on %d (%s)" % (self.port, self.logfile))
                time.sleep(0.5)
        self.sock.settimeout(None)
        self.buf = b""

    def _scan_log(self):
        try:
            text = self.logfile.read_text(errors="replace")
        except OSError:
            return
        self.log += [line[:300] for line in text.splitlines() if "Traceback" in line or "rror" in line]

    # -- the control socket ------------------------------------------------------------

    def send(self, msg):
        self.sock.sendall((json.dumps(msg) + "\n").encode())

    def _line(self, timeout):
        self.sock.settimeout(timeout)
        try:
            while b"\n" not in self.buf:
                chunk = self.sock.recv(1 << 20)
                if not chunk:
                    raise ConnectionError("the desktop app closed its control socket")
                self.buf += chunk
        finally:
            self.sock.settimeout(None)
        line, self.buf = self.buf.split(b"\n", 1)
        return json.loads(line)

    def ask(self, msg, want, timeout=5.0):
        """Send ``msg`` and return the first answer of kind ``want``."""
        self.send(msg)
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                raise TimeoutError("no %s from the desktop app" % want)
            try:
                r = self._line(left)
            except socket.timeout:
                raise TimeoutError("no %s from the desktop app" % want) from None
            if r.get("kind") == want or r.get("desk") == want:
                return r

    # -- checkkit.Session's calls -------------------------------------------------------

    def settle(self, timeout=60.0):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            try:
                ok = self.ask({"kind": "settled"}, "settled", timeout=2.0)["ok"]
            except TimeoutError:
                continue
            if ok:
                time.sleep(0.3)
                return time.monotonic() - t0
            time.sleep(0.1)
        raise TimeoutError("the engines never settled")

    def load(self, project):
        self.send({"kind": "loaded", "json": json.dumps(project)})
        time.sleep(0.2)
        self.settle()

    def reset(self, project=None):
        self.send({"kind": "reset", "json": json.dumps(project or model.new_project())})
        time.sleep(0.2)
        self.settle()

    def op(self, op):
        self.send({"kind": "op", "op": op})

    def stats(self):
        return self.ask({"desk": "stats"}, "stats")

    # -- files: a folder stands in for the dialogs ---------------------------------------

    def clear_files(self):
        for p in wsl("files").iterdir():
            if not p.name.startswith("open."):
                p.unlink()

    def file(self, ext):
        hits = sorted((p for p in wsl("files").iterdir() if p.name.endswith(ext) and not p.name.startswith("open.")),
                      key=lambda p: p.stat().st_mtime)
        return hits[-1].read_bytes() if hits else None

    def wait_file(self, ext, timeout=10.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            data = self.file(ext)
            if data is not None:
                return data
            time.sleep(0.2)
        raise TimeoutError("nothing saved ending %s" % ext)

    def set_open(self, text):
        wsl("files", "open.ostinato.json").write_text(text, encoding="utf-8")

    def last_export(self):
        return self.ask({"desk": "export"}, "export")["last"]

    def export_progress(self):
        return self.ask({"desk": "export"}, "export")["progress"]

    # -- a long take of what plays (tools/timing_check.py) ---------------------------

    def tap_on(self):
        _TAKES[0] += 1
        self._take = "take%d" % _TAKES[0]
        self.ask({"desk": "tap", "on": True, "folder": win("taps", self._take)}, "tap_open", timeout=10)

    def tap_poll(self):
        pass                      # the mixer writes its tap to disk as it goes

    def tap_off(self):
        """Stop the take: (mono float32, the context frame of its first sample)."""
        self.ask({"desk": "tap", "on": False, "folder": win("taps", self._take)}, "tap_closed", timeout=30)
        raw = np.fromfile(wsl("taps", self._take, "mix.raw"), dtype="<i2")
        info = json.loads(wsl("taps", self._take, "tap.json").read_text())
        return (raw.reshape(-1, 2).astype(np.float32) / 32768.0).mean(axis=1), int(info["first_frame"])

    def now_frame(self):
        """The output's context frame playing now (the mixer's last report)."""
        return self.stats()["frame"]

    def picker_calls(self):
        """How many times the app asked where to save or what to open (a dialog each, for a person)."""
        return self.ask({"desk": "pickers"}, "pickers")["calls"]

    def reset_pickers(self):
        self.ask({"desk": "pickers", "reset": True}, "pickers")

    def _grab(self, x=0, y=0, w=0, h=0):
        """The window's client area (w, h 0: all of it) as an RGB array, rows by columns."""
        _TAKES[0] += 1
        name = "shot%d.bmp" % _TAKES[0]
        self.ask({"desk": "shot", "path": win(name), "rect": [x, y, w, h]}, "shot", timeout=10)
        data = wsl(name).read_bytes()
        wsl(name).unlink()
        bw, bh = np.frombuffer(data, dtype="<i4", count=2, offset=18)      # desktop/control.py writes top-down BGRA
        px = np.frombuffer(data, dtype=np.uint8, offset=54).reshape(-bh, bw, 4)
        return px[:, :, 2::-1]

    def pixel(self, x, y):
        """The colour on screen at window pixel (x, y)."""
        return tuple(int(v) for v in self._grab(int(x), int(y), 1, 1)[0, 0])

    def screenshot(self, path):
        import struct
        import zlib

        rgb = self._grab()
        h, w, _ = rgb.shape
        raw = b"".join(b"\0" + rgb[k].tobytes() for k in range(h))
        chunk = lambda kind, body: (struct.pack(">I", len(body)) + kind + body
                                    + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF))
        with open(path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                    + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))

    def window(self):
        """The window as Windows has it: {"maximized": .., "client": [w, h]}."""
        return self.ask({"desk": "ping"}, "pong")

    def resize(self, frac):
        """Ask Windows for a smaller window, as dragging its frame would (``frac`` of the
        work area), then wait a moment for the app to answer."""
        self.send({"desk": "window", "op": "size", "frac": frac})
        time.sleep(1.5)

    def unresize(self):
        self.send({"desk": "window", "op": "maximize"})
        time.sleep(1.0)
