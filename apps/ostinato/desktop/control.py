"""The control socket: what the checks drive the desktop app through.

The browser checks reach the app through Chrome's DevTools: they send the UI
messages (``window.__ostinato.ui.send``), click the canvas, play the fake
keyboard and read the worklet's tap. This socket gives the desktop app the
same handles, so tools/checkkit.py's desktop session can run the same checks.

One JSON object per line, both ways, on 127.0.0.1 (``--control PORT``):

    {"kind": "geom"}                  any message the page would send the UI
                                      (Files.on_page); its answer, if any, comes
                                      back as {"kind": "geom", ...}
    {"desk": "mouse", "op": "down", "x": 10, "y": 20}
                                      down, up, move or click, in window pixels,
                                      posted to the window as the mouse would
    {"desk": "wheel", "dy": 100, "x": .., "y": ..}
    {"desk": "key", "key": "s", "mod": ["ctrl"], "op": "press"}
                                      a key into the window's event stream
                                      (press, down or up; "SPACE", "LSHIFT", "a"...),
                                      with Ctrl, Shift or Caps Lock held
    {"desk": "midi", "events": [[ms, [0x90, 60, 100]], ...]}
                                      played into the MIDI loopback port on time
                                      (by desktop/midi_send.py, a process of its own)
    {"desk": "tap", "on": true, "folder": "C:\\\\..."}
                                      the mixer records what it plays ->
                                      {"desk": "tap_open"} / {"desk": "tap_closed"}
    {"desk": "stats"}                 -> {"desk": "stats", "underruns": .., ...}
    {"desk": "export"}                -> {"desk": "export", "progress": .., "last": ..}
    {"desk": "pickers", "reset": false}
                                      -> {"desk": "pickers", "calls": n}: how often the
                                      app asked where to save or what to open
    {"desk": "shot", "path": "C:\\...bmp", "rect": [x, y, w, h]}
                                      the client area (w, h 0: all of it) as a BMP,
                                      even under other windows -> {"desk": "shot"}
    {"desk": "window", "op": "size", "frac": 0.8} / {"desk": "window", "op": "maximize"}
                                      restore the window and size it as dragging its
                                      frame would, or maximise it again
    {"desk": "ping"}                  -> {"desk": "pong", "ready": bool, "maximized": bool, "client": [w, h]}
    {"desk": "quit"}

Everything that touches LVGL runs on the UI thread (page.tell); this thread
only reads, writes and posts window messages.
"""

import json
import socket
import threading
import time
import traceback

import page

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_MOUSEWHEEL = 0x020A
MK_LBUTTON = 0x0001


class Control:
    def __init__(self, port, runner, midi_out_name, quit_fn):
        self.port = port
        self.runner = runner
        self.midi_out_name = midi_out_name
        self.quit_fn = quit_fn
        self.conn = None
        self.lock = threading.Lock()
        self.held = False            # the left button, for moves while it's down
        page.reply = self.reply

    def start(self):
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", self.port))
        self.server.listen(1)
        threading.Thread(target=self._serve, name="control", daemon=True).start()
        self._sender()

    def _sender(self):
        import os
        import subprocess
        import sys

        if getattr(self, "sender", None) is None or self.sender.poll() is not None:
            script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "midi_send.py")
            self.sender = subprocess.Popen([sys.executable, script, self.midi_out_name], stdin=subprocess.PIPE,
                                           text=True)

    def reply(self, msg):
        data = (json.dumps(msg) + "\n").encode()
        with self.lock:
            conn = self.conn
            if conn is None:
                return
            try:
                conn.sendall(data)
            except OSError:
                pass

    def _serve(self):
        while True:
            conn, _ = self.server.accept()
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with self.lock:
                self.conn = conn
            buf = b""
            try:
                while True:
                    chunk = conn.recv(1 << 16)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if line.strip():
                            try:
                                self._handle(json.loads(line))
                            except Exception:  # noqa: BLE001  (one bad command doesn't close the socket)
                                traceback.print_exc()
            except OSError:
                traceback.print_exc()
            finally:
                with self.lock:
                    self.conn = None
                conn.close()

    # -- the commands ---------------------------------------------------------------------

    def _handle(self, msg):
        what = msg.get("desk")
        if what is None:
            page.tell(msg)
        elif what == "ping":
            self.reply(dict(_window_state(), desk="pong", ready=page._fn is not None))
        elif what == "mouse":
            self._mouse(msg["op"], int(msg["x"]), int(msg["y"]))
        elif what == "wheel":
            self._wheel(float(msg.get("dy", 0)), int(msg.get("x", 0)), int(msg.get("y", 0)))
        elif what == "key":
            page.tell(lambda: _inject_key(msg["key"], msg.get("mod", []), msg.get("op", "press")))
        elif what == "midi":
            self._midi(msg["events"])
        elif what == "tap":
            self.runner.mixer({"t": "tap", "on": bool(msg.get("on")), "folder": msg["folder"],
                               "lane": int(msg.get("lane", 0))})
        elif what == "stats":
            page.tell(self._stats)
        elif what == "export":
            page.tell(lambda: self.reply({"desk": "export", "progress": page.export_progress(),
                                          "last": page.state["last_export"]}))
        elif what == "pickers":
            if msg.get("reset"):
                page.state["picker_calls"] = 0
            self.reply({"desk": "pickers", "calls": page.state["picker_calls"]})
        elif what == "shot":
            _shot(msg["path"], msg.get("rect") or [0, 0, 0, 0])
            self.reply({"desk": "shot", "path": msg["path"]})
        elif what == "window":
            _window_op(msg.get("op"), float(msg.get("frac", 0.8)))
        elif what == "quit":
            page.tell(self.quit_fn)

    def _stats(self):
        import engines

        link = engines.current
        m = link.master_status or {}
        lanes = m.get("lane_underruns") or []
        queued = m.get("queued") or [0] * len(lanes)
        # As the worklet's stats(): the streams are the engines' rings, the
        # target and low point are the output queue's (WASAPI's padding).
        self.reply({"desk": "stats", "underruns": (m.get("underruns") or 0) + sum(lanes),
                    "device_underruns": m.get("underruns"), "lane_underruns": lanes, "target": m.get("target"),
                    "floor": m.get("floor"), "low": m.get("low"), "frame": m.get("frame"), "adapt": m.get("adapt"), "late": m.get("late"),
                    "queued": queued,
                    "streams": [{"underruns": u, "queued": q} for u, q in zip(lanes, queued)]})

    def _post(self, msg, wparam, lparam):
        import ctypes

        hwnd = page.config["hwnd"]
        if hwnd:
            ctypes.windll.user32.PostMessageW(ctypes.c_void_p(hwnd), msg, ctypes.c_size_t(wparam),
                                              ctypes.c_ssize_t(lparam))

    def _mouse(self, op, x, y):
        lp = (y & 0xFFFF) << 16 | (x & 0xFFFF)
        if op in ("move", "down", "click"):
            self._post(WM_MOUSEMOVE, MK_LBUTTON if self.held else 0, lp)
        if op in ("down", "click"):
            self.held = True
            self._post(WM_LBUTTONDOWN, MK_LBUTTON, lp)
        if op == "click":
            time.sleep(0.03)          # a real click lasts a few frames; LVGL reads its pointer every 10-15 ms
        if op in ("up", "click"):
            self.held = False
            self._post(WM_LBUTTONUP, 0, lp)

    def _wheel(self, dy, x, y):
        self._mouse("move", x, y)
        notches = -int(round(dy / 100.0)) or (-1 if dy > 0 else 1)
        self._post(WM_MOUSEWHEEL, ((notches * 120) & 0xFFFF) << 16, 0)

    def _midi(self, evs):
        """Play [[ms, bytes], ...] into the loopback's output, each on time,
        from a process of its own (desktop/midi_send.py), started once."""
        self._sender()
        self.sender.stdin.write(json.dumps(evs) + "\n")
        self.sender.stdin.flush()


def _window_state():
    """The window as Windows has it: maximised or not, and its client size."""
    import ctypes

    hwnd = page.config["hwnd"]
    if not hwnd:
        return {}
    u = ctypes.WinDLL("user32")       # its own handle: uwin32 sets argtypes on windll's
    from window import client_rect

    return {"maximized": bool(u.IsZoomed(ctypes.c_void_p(hwnd))), "client": list(client_rect(hwnd))}


def _shot(path, rect):
    """The window's client area into a BMP file, through PrintWindow (the
    window draws itself into a bitmap, so other windows on top don't matter)."""
    import ctypes
    import struct

    hwnd = ctypes.c_void_p(page.config["hwnd"])
    u = ctypes.WinDLL("user32")
    g = ctypes.WinDLL("gdi32")
    for f in (u.GetDC, g.CreateCompatibleDC, g.CreateCompatibleBitmap, g.SelectObject):
        f.restype = ctypes.c_void_p
    u.GetDC.argtypes = [ctypes.c_void_p]
    u.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    g.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    g.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    g.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    g.DeleteObject.argtypes = [ctypes.c_void_p]
    g.DeleteDC.argtypes = [ctypes.c_void_p]
    u.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
    g.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p,
                            ctypes.c_void_p, ctypes.c_uint]
    from window import client_rect

    cw, ch = client_rect(page.config["hwnd"])
    sdc = u.GetDC(hwnd)
    mdc = g.CreateCompatibleDC(sdc)
    bmp = g.CreateCompatibleBitmap(sdc, cw, ch)
    old = g.SelectObject(mdc, bmp)
    try:
        u.PrintWindow(hwnd, mdc, 3)          # PW_CLIENTONLY | PW_RENDERFULLCONTENT
        hdr = struct.pack("<IiiHHIIiiII", 40, cw, -ch, 1, 32, 0, 0, 0, 0, 0, 0)
        info = ctypes.create_string_buffer(hdr, 40 + 16)
        buf = ctypes.create_string_buffer(cw * ch * 4)
        g.GetDIBits(mdc, bmp, 0, ch, buf, info, 0)
    finally:
        g.SelectObject(mdc, old)
        g.DeleteObject(bmp)
        g.DeleteDC(mdc)
        u.ReleaseDC(hwnd, sdc)
    x, y, w, h = (int(v) for v in rect)
    if w <= 0 or h <= 0:
        x, y, w, h = 0, 0, cw, ch
    x, y = max(0, min(x, cw - 1)), max(0, min(y, ch - 1))
    w, h = min(w, cw - x), min(h, ch - y)
    rows = b"".join(buf.raw[((y + k) * cw + x) * 4:((y + k) * cw + x + w) * 4] for k in range(h))
    img = struct.pack("<IiiHHIIiiII", 40, w, -h, 1, 32, 0, len(rows), 0, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(b"BM" + struct.pack("<IHHI", 14 + 40 + len(rows), 0, 0, 54) + img + rows)


def _window_op(op, frac):
    """Restore and size the window (as dragging its frame would), or maximise it."""
    import ctypes
    from ctypes import wintypes

    hwnd = ctypes.c_void_p(page.config["hwnd"])
    u = ctypes.WinDLL("user32")
    if op == "maximize":
        u.ShowWindow(hwnd, 3)                 # SW_MAXIMIZE
        return
    u.ShowWindow(hwnd, 9)                     # SW_RESTORE
    area = wintypes.RECT()
    u.SystemParametersInfoW(0x30, 0, ctypes.byref(area), 0)    # SPI_GETWORKAREA
    w, h = int((area.right - area.left) * frac), int((area.bottom - area.top) * frac)
    u.SetWindowPos(hwnd, None, area.left + 40, area.top + 40, w, h, 0x0014)   # SWP_NOZORDER | SWP_NOACTIVATE


def _inject_key(name, mods, op="press"):
    """A key into the window's own event stream, so it takes the path a real
    key takes: the shortcuts (page.see_event), then LVGL."""
    import keys
    from displaydev import windisplay

    import events

    mod = 0
    for m in mods:
        mod |= {"ctrl": keys.KMOD_LCTRL, "shift": keys.KMOD_LSHIFT, "caps": keys.KMOD_CAPS,
                "alt": keys.KMOD_LALT}[m]
    code = getattr(keys, "K_" + name, None)
    if code is None:
        code = ord(name) if len(name) == 1 else int(name)
    label = keys.keyname(code)
    if op in ("press", "down"):
        windisplay._pending.append(events.Key(events.KEYDOWN, label, code, mod, 0, None))
    if op in ("press", "up"):
        windisplay._pending.append(events.Key(events.KEYUP, label, code, mod, 0, None))
