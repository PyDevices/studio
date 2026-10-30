"""The UI's line to whatever hosts it: here, the browser page (index.html).

The page holds what a worker can't: the file pickers, local storage, the
keyboard shortcuts and the wheel. The UI talks to it in plain dicts:

    send(msg)            a message to the page ({"kind": ...})
    listen(fn)           fn(dict) for every message from the page
    fetch_bytes(path)    a file, by URL or by a path under the app (async)
    fetch_text(path)
    heap_mb()            the UI's memory, for the test reports
    run(coro)            run a coroutine: here on the worker's event loop

The desktop app has its own page.py with the same names (desktop/ui/).
"""

import asyncio
import os

from js import Object, Uint8Array, fetch, self as worker
from pyodide.ffi import create_proxy, to_js

BASE = os.environ.get("BASE", "")
_proxies = []


def send(msg, transfer=None):
    js_msg = to_js(dict(msg, type="app"), dict_converter=Object.fromEntries)
    if transfer:
        worker.postMessage(js_msg, to_js(transfer))
    else:
        worker.postMessage(js_msg)


def listen(fn):
    proxy = create_proxy(lambda msg: fn(msg.to_py()))
    _proxies.append(proxy)
    worker.onPageMessage = proxy


def _url(path):
    return path if "://" in path else BASE + path


async def fetch_bytes(path):
    resp = await fetch(_url(path))
    if not resp.ok:
        raise OSError("%s: %s" % (path, resp.status))
    return bytes(Uint8Array.new(await resp.arrayBuffer()))


async def fetch_text(path):
    resp = await fetch(_url(path))
    if not resp.ok:
        raise OSError("%s: %s" % (path, resp.status))
    return str(await resp.text())


def heap_mb():
    import pyodide_js

    return round(pyodide_js._module.HEAP8.length / 1048576, 1)


def run(coro):
    return asyncio.ensure_future(coro)
