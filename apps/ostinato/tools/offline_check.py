"""Check that the app starts offline after one visit, fonts included.

    .venv/bin/python tools/offline_check.py

Loads index.html once (the service worker caches everything the app and its
workers fetch, the lvgl-bindings fonts too), cuts the network, reloads, and
fails unless the app comes up with every font loaded.
"""

import pathlib
import subprocess
import sys
import time

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main():
    server = subprocess.Popen([sys.executable, str(ROOT / "tools" / "serve.py"), "--port", "8775"],
                              stdout=subprocess.DEVNULL)
    time.sleep(1)
    logs = []
    ok = False
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
            ctx = browser.new_context(viewport={"width": 1600, "height": 900})
            page = ctx.new_page()
            page.on("console", lambda m: logs.append(m.text[:300]))
            page.goto("http://127.0.0.1:8775/index.html")
            page.wait_for_function("window.__appReady || window.__appError", timeout=120_000)
            page.wait_for_function("navigator.serviceWorker.controller !== null", timeout=30_000)
            time.sleep(3)
            online_logs = len(logs)
            ctx.set_offline(True)
            t0 = time.time()
            page.reload()
            page.wait_for_function("window.__appReady || window.__appError", timeout=120_000)
            err = page.evaluate("window.__appError || ''")
            time.sleep(4)
            page.screenshot(path=str(ROOT / "runs" / "offline.png"))
            later = logs[online_logs:]
            fonts_failed = [l for l in later if "fonts not loaded" in l]
            ui_ready = [l for l in later if "ui ready" in l]
            print("offline start: %.1f s, error %r, ui ready %s, font failures %s" % (
                time.time() - t0, err, bool(ui_ready), fonts_failed or "none"))
            ok = not err and ui_ready and not fonts_failed
            browser.close()
    finally:
        server.terminate()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
