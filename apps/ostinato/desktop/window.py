"""The desktop app's window: pydevices' WinDisplay, maximised, as the installed PWA is.

``PYDEVICES_MAXIMIZED=1`` makes the desktop board config open WinDisplay
maximised at the work area, DPI aware, with LVGL the client area at scale 1.
Here the window gets its title, and its events pass through the page's
shortcut filter on their way to LVGL (``page.see_event``), as the browser
page's key listeners see every key the canvas gets.
"""

import ctypes
import os
import time
from ctypes import wintypes

_app = None
_get_client_rect = None


def client_rect(hwnd):
    """The window's client size (w, h). Its own prototype of GetClientRect:
    pydevices' uwin32 sets argtypes on the shared user32 functions, with a
    RECT of its own that wintypes.RECT doesn't pass as."""
    global _get_client_rect
    if _get_client_rect is None:
        proto = ctypes.WINFUNCTYPE(wintypes.BOOL, ctypes.c_void_p, ctypes.POINTER(wintypes.RECT))
        _get_client_rect = proto(("GetClientRect", ctypes.windll.user32))
    r = wintypes.RECT()
    _get_client_rect(ctypes.c_void_p(hwnd), ctypes.byref(r))
    return r.right - r.left, r.bottom - r.top


def open_window(page):
    global _app
    import appdev
    import board_config

    drv = board_config.display_drv
    hwnd = getattr(drv, "_hwnd", None) or getattr(getattr(drv, "_display", None), "_hwnd", None)
    if hwnd is not None and not isinstance(hwnd, int):
        hwnd = ctypes.cast(hwnd, ctypes.c_void_p).value
    page.config["hwnd"] = hwnd
    if hwnd:
        ctypes.windll.user32.SetWindowTextW(ctypes.c_void_p(hwnd), "Ostinato")
    read = board_config.host_read
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = ctypes.c_void_p

    focused = [False]
    # The installed PWA's canvas keeps the size it opened at; here the window
    # does: restored or dragged smaller, it goes back to maximised (LVGL draws
    # at one fixed size, so a smaller window would cut the UI off).
    keep = bool(hwnd) and os.environ.get("PYDEVICES_MAXIMIZED") == "1"
    size = []                # the maximised client size, as first seen
    next_look = [0.0]
    u = ctypes.WinDLL("user32")     # its own handle: uwin32 sets argtypes on windll's

    def keep_size():
        h = ctypes.c_void_p(hwnd)
        now = client_rect(hwnd)
        zoomed = u.IsZoomed(h)
        if zoomed and not size:
            size.append(now)
        if size and not u.IsIconic(h) and (not zoomed or now != size[0]):
            u.ShowWindow(h, 9)              # SW_RESTORE, then SW_MAXIMIZE: back to the work area
            u.ShowWindow(h, 3)

    def host_read(*args):
        evs = read(*args)
        if evs:
            for ev in evs if isinstance(evs, list) else (evs,):
                page.see_event(ev)
        if hwnd:
            # The page's blur: when the window stops being the one with the
            # keyboard, nothing it saw held down can still be held.
            now = user32.GetForegroundWindow() == hwnd
            if focused[0] and not now:
                page.focus_lost()
            focused[0] = now
        if keep and time.perf_counter() >= next_look[0]:
            next_look[0] = time.perf_counter() + 0.25
            try:
                keep_size()
            except Exception:  # noqa: BLE001  (never at the cost of the window's input)
                pass
        return evs

    board_config.host_read = host_read
    _app = appdev.App.current() or appdev.App(board_config)
    print("window %dx%d (hwnd %s)" % (drv.width, drv.height, hwnd), flush=True)
    return _app


def quit():
    if _app is not None:
        _app.request_quit()


def gc_policy(mode):
    """The free-threaded build's collections stop every thread for a moment,
    the mixer's too: after start, freeze what's built and collect rarely
    (``freeze``), or not at all (``off``)."""
    import gc

    gc.collect()
    gc.freeze()
    if mode == "off":
        gc.disable()
    else:
        gc.set_threshold(50000, 50, 1000)
