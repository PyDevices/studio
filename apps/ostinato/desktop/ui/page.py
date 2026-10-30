"""The desktop's page: what index.html does for the browser version.

The shared UI (app/ui) talks to its page through the same names (``send``,
``listen``, ``fetch_bytes``, ``fetch_text``, ``heap_mb``, ``run``; see
app/ui/page.py). In a browser the page is the HTML around the canvas; here
it is this module, in the UI's own process and thread:

- **files**: Windows' Open and Save dialogs (desktop/dialogs.py), the
  project file a quick save writes back to, MIDI and WAV export (the engines
  render, this sums them and runs the master rack, as the page does);
- **autosave and the first run**: a file in the data folder instead of
  local storage;
- **shortcuts, Shift and the wheel**, from the window's events
  (``see_event``) before LVGL gets them, as the page's key listeners do;
- **fonts and assets**: read from the checkout; the lvgl-bindings fonts are
  downloaded once and kept in the data folder;
- **the control socket's replies** (desktop/control.py): the answers the
  browser checks read from ``window.__geom`` and friends.

Messages for the UI can come from any thread (``tell``); they reach it on
the UI thread, from an LVGL timer, as a browser worker gets its messages
from its own event loop.
"""

import json
import os
import queue
import threading
import time
import traceback

import lvgl as lv

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SR = 48000
REPLIES = ("geom", "project", "uistats", "settled", "masterstats")

config = {
    "data": os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "Ostinato"),
    "test_files": None,      # a folder that stands in for the dialogs (the checks)
    "fresh": False,          # start without the autosave or the first-run guide
    "hwnd": None,            # the window, as the dialogs' owner
}
reply = None                 # fn(msg) for the replies the checks wait for (the control socket)
state = {"typing": False, "project_path": None, "last_export": None, "export": None, "shift": False,
         "pointer": (0, 0), "picker_calls": 0}

inbox = queue.SimpleQueue()
_fn = None
_keep = []


def tell(msg):
    """A message for the UI (Files.on_page), from any thread; a callable runs on the UI thread."""
    inbox.put(msg)


def listen(fn):
    global _fn
    _fn = fn
    import engines

    if engines.current is not None:
        engines.current.listeners.append(_from_engines)
    cb = lambda t: _drain()
    _keep.append(cb)
    _keep.append(lv.timer_create(cb, 15, None))
    start = lambda t: _startup()
    _keep.append(start)
    once = lv.timer_create(start, 300, None)
    once.set_repeat_count(1)
    _keep.append(once)


def _from_engines(msg):
    """The mixer's answers to the control socket's tap."""
    t = msg.get("t")
    if t in ("tap_open", "tap_closed") and reply is not None:
        reply({"desk": t, "folder": msg.get("folder")})


def _drain():
    for _ in range(100):
        try:
            msg = inbox.get_nowait()
        except queue.Empty:
            return
        try:
            if callable(msg):
                msg()
            elif _fn is not None:
                _fn(msg)
        except Exception:  # noqa: BLE001  (one bad message must not stop the app)
            traceback.print_exc()


def _data(*parts):
    path = os.path.join(config["data"], *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


AUTOSAVE = "autosave.ostinato.json"
WELCOMED = "welcomed"


def _startup():
    """As the page does once every worker is ready: restore the last
    session, and the first time ever, the demo song with the guide. A
    fresh start does neither, but still counts as the first."""
    flag = _data(WELCOMED)
    seen = os.path.exists(flag)
    if not seen:
        with open(flag, "w") as f:
            f.write("1")
    if config["fresh"]:
        return
    path = _data(AUTOSAVE)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            tell({"kind": "restore", "json": f.read()})
    if not seen:
        tell({"kind": "first-run"})


# -- what the UI asks of its page -------------------------------------------------------


def send(msg, transfer=None):
    kind = msg.get("kind")
    if kind in REPLIES:
        if reply is not None:
            reply(msg)
        return
    if kind == "typing":
        state["typing"] = bool(msg.get("on"))
        return
    try:
        if kind == "save":
            _save(msg)
        elif kind == "open":
            path = _ask_open()
            if path is None:
                raise _Cancelled()
            with open(path, encoding="utf-8") as f:
                text = f.read()
            state["project_path"] = path
            tell({"kind": "loaded", "json": text})
        elif kind == "autosave":
            _write_atomic(_data(AUTOSAVE), msg["json"].encode("utf-8"))
        elif kind == "export-midi":
            path = _ask_save(msg["name"] + ".mid", "MIDI file", "*.mid", "mid")
            if path is None:
                raise _Cancelled()
            _write_atomic(path, bytes(msg["bytes"]))
            tell({"kind": "exported", "file": os.path.basename(path)})
        elif kind == "export-wav":
            path = _ask_save(msg["name"] + ".wav", "WAV audio", "*.wav", "wav")
            if path is None:
                raise _Cancelled()
            _export_wav(int(msg["frames"]), path)
    except _Cancelled:
        tell({"kind": "failed", "why": "Cancelled"})
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        tell({"kind": "failed", "why": str(e) or type(e).__name__})


class _Cancelled(Exception):
    pass


def _save(msg):
    """A quick save writes back to the file last saved or opened; the first
    save, and Save as, ask where."""
    path = state["project_path"] if msg.get("quick") else None
    if path is None:
        path = _ask_save(msg["name"] + ".ostinato.json", "Ostinato project", "*.ostinato.json", "json")
        if path is None:
            raise _Cancelled()
    _write_atomic(path, msg["json"].encode("utf-8"))
    state["project_path"] = path
    tell({"kind": "saved", "file": os.path.basename(path)})


def _write_atomic(path, data):
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


_BAD = '<>:"/\\|?*'


def _safe(name):
    return "".join("_" if c in _BAD or ord(c) < 32 else c for c in name).strip() or "Untitled"


def _ask_save(name, description, pattern, ext):
    name = _safe(name)
    state["picker_calls"] += 1
    if config["test_files"]:
        return os.path.join(config["test_files"], name)
    import dialogs

    return dialogs.save_file(config["hwnd"], name, description, pattern, ext)


def _ask_open():
    state["picker_calls"] += 1
    if config["test_files"]:
        return os.path.join(config["test_files"], "open.ostinato.json")
    import dialogs

    return dialogs.open_file(config["hwnd"], "Ostinato project", "*.ostinato.json;*.json")


# -- WAV export: the engines render, the page sums, the master rack finishes ------------


def _export_wav(frames, path):
    import numpy as np

    import engines

    link = engines.current
    job = {"frames": frames, "sums": np.zeros(frames * 2, dtype=np.float32), "done": 0, "path": path,
           "t0": time.perf_counter(), "chunks": [0] * link.count, "energy": [0.0] * link.count, "told": 0.0,
           "count": link.count, "project": json.loads(json.dumps(link.project))}
    state["export"] = job
    if _on_engine not in link.listeners:
        link.listeners.append(_on_engine)
    link.broadcast({"t": "render", "frames": frames, "chunk": 96000})


def export_progress():
    job = state["export"]
    if job is None:
        return None
    return {"chunks": list(job["chunks"]), "done": job["done"], "ms": (time.perf_counter() - job["t0"]) * 1000}


def _on_engine(msg):
    job = state["export"]
    t = msg.get("t")
    if job is None or t not in ("pcm", "rendered"):
        return
    import numpy as np

    i = msg.get("engine", 0)
    if t == "pcm":
        at = msg["seq"] * 96000 * 2
        pcm = np.frombuffer(msg["pcm"], dtype=np.int16)
        n = min(len(pcm), len(job["sums"]) - at)
        if n > 0:
            v = pcm[:n].astype(np.float32) / 32768.0
            job["sums"][at:at + n] += v
            job["energy"][i] += float(np.dot(v, v))
        job["chunks"][i] += 1
        part = min(job["chunks"]) * 96000 / job["frames"]
        if part - job["told"] >= 0.01:
            job["told"] = part
            tell({"kind": "render-progress", "part": part})
    else:
        job["done"] += 1
        if job["done"] == job["count"]:
            tell({"kind": "render-progress", "part": 1, "master": True})
            threading.Thread(target=_finish_wav, args=(job,), name="export", daemon=True).start()


def _finish_wav(job):
    """Through the master rack, as live: the sum 6 dB down as int16, processed,
    then 16-bit stereo with the same peak limit the output applies."""
    import wave

    import numpy as np

    try:
        import mastercore

        frames = job["frames"]
        pcm = np.clip(np.round(job["sums"] * 16383.5), -32768, 32767).astype(np.int16)
        mm = mastercore.MasterMix(SR)
        mm.apply({"op": "replace", "value": job["project"]})
        back = np.frombuffer(mm.render_offline(pcm.tobytes(), frames), dtype=np.int16)
        out = back.astype(np.float32) / 16383.5
        peak = float(np.max(np.abs(out))) if len(out) else 0.0
        g = 0.97 / peak if peak > 0.97 else 1.0
        data = np.clip(np.round(out * g * 32767), -32768, 32767).astype("<i2").tobytes()
        tmp = job["path"] + ".part"
        with wave.open(tmp, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes(data)
        os.replace(tmp, job["path"])
        state["last_export"] = {"seconds": frames / SR, "renderMs": (time.perf_counter() - job["t0"]) * 1000,
                                "rms": [(e / (frames * 2)) ** 0.5 for e in job["energy"]],
                                "peak": float(np.max(np.abs(job["sums"]))) if frames else 0.0}
        tell({"kind": "exported", "file": os.path.basename(job["path"])})
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        tell({"kind": "failed", "why": str(e) or type(e).__name__})
    finally:
        state["export"] = None


# -- the window's keys and wheel, as index.html's listeners see them ---------------------

_keys = None


def see_event(ev):
    """Every window event passes here on its way to LVGL (which gets it too)."""
    global _keys
    if _keys is None:
        import events
        import keys

        _keys = (events, keys)
    events, keys = _keys
    t = ev.type
    if t == events.MOUSEMOTION or t in (events.MOUSEBUTTONDOWN, events.MOUSEBUTTONUP):
        state["pointer"] = ev.pos
    elif t == events.MOUSEWHEEL:
        if ev.y:
            # deltaY's sign: positive scrolls down (a wheel turned toward you).
            x, y = state["pointer"]
            tell({"kind": "wheel", "dy": -ev.y * 100, "x": x, "y": y})
    elif t in (events.KEYDOWN, events.KEYUP):
        down = t == events.KEYDOWN
        if ev.key in (keys.K_LSHIFT, keys.K_RSHIFT):
            shift(down)
        elif down and not state["typing"]:
            name = shortcut(ev.key, ev.mod or 0, keys)
            if name:
                tell({"kind": "key", "key": name})


def shift(down):
    if down != state["shift"]:
        state["shift"] = down
        tell({"kind": "key", "key": "shift-down" if down else "shift-up"})


def shortcut(key, mod, keys):
    """index.html's shortcut table: a key and its modifiers to the app's name for it."""
    ctrl = bool(mod & (keys.KMOD_CTRL | keys.KMOD_GUI))
    sh = bool(mod & keys.KMOD_SHIFT)
    ch = chr(key) if 32 <= key < 127 else None
    if key == keys.K_SPACE and not ctrl:
        return "space"
    if ch == "r" and not ctrl:
        return "r"
    if key == keys.K_ESCAPE:
        return "escape"
    if key in (keys.K_DELETE, keys.K_BACKSPACE):
        return "delete"
    if ctrl and ch:
        return {"z": "redo" if sh else "undo", "y": "redo", "s": "save-as" if sh else "save",
                "c": "copy", "v": "paste", "d": "duplicate", "a": "select-all"}.get(ch)
    return None


def focus_lost():
    """The window lost the keyboard: Shift can't be held any more (the page's blur)."""
    shift(False)


# -- assets and fonts --------------------------------------------------------------------


def _cached(url):
    """A file from the web, downloaded once into the data folder."""
    import urllib.parse

    u = urllib.parse.urlparse(url)
    path = _data("cache", u.netloc, *[p for p in u.path.split("/") if p and p != ".."])
    if not os.path.exists(path):
        import urllib.request

        with urllib.request.urlopen(url, timeout=20) as resp:
            data = resp.read()
        _write_atomic(path, data)
    with open(path, "rb") as f:
        return f.read()


async def fetch_bytes(path):
    if "://" in path:
        return _cached(path)
    with open(os.path.join(ROOT, path), "rb") as f:
        return f.read()


async def fetch_text(path):
    return (await fetch_bytes(path)).decode("utf-8")


def run(coro):
    """Run a coroutine to the end, now: the desktop UI has no event loop of
    its own running (multimer drives it), and nothing here waits on one."""
    import asyncio

    return asyncio.run(coro)


def heap_mb():
    """The UI process's working set, in MB."""
    try:
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (n, ctypes.c_size_t) for n in ("PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                                               "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage",
                                               "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]

        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        ctypes.windll.psapi.GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
        return round(pmc.WorkingSetSize / 1048576, 1)
    except Exception:  # noqa: BLE001
        return None
