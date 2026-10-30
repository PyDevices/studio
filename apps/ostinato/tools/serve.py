"""Serve Ostinato from the repo root for local use and tests.

    .venv/bin/python tools/serve.py [--port 8765] [--isolate]

No caching, so an edit shows on reload. ``--isolate`` adds the COOP/COEP
headers that SharedArrayBuffer needs (P0's SAB comparison only).
"""

import argparse
import functools
import http.server
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


class Handler(http.server.SimpleHTTPRequestHandler):
    isolate = False
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      ".mjs": "text/javascript", ".js": "text/javascript", ".wasm": "application/wasm",
                      ".whl": "application/zip", ".json": "application/json"}

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        if self.isolate:
            self.send_header("Cross-Origin-Opener-Policy", "same-origin")
            self.send_header("Cross-Origin-Embedder-Policy", "credentialless")
        super().end_headers()

    def log_message(self, *args):
        pass

    def do_POST(self):
        """Test hook: POST /__tap?start=F&channels=C with raw float32 frames
        appends them to $TAP_DIR/tap.raw (one line per chunk in tap.idx)."""
        import os
        from urllib.parse import parse_qs, urlparse

        u = urlparse(self.path)
        if u.path != "/__tap" or not os.environ.get("TAP_DIR"):
            self.send_error(404)
            return
        q = parse_qs(u.query)
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        d = pathlib.Path(os.environ["TAP_DIR"])
        with open(d / "tap.raw", "ab") as f:
            off = f.tell()
            f.write(body)
        with open(d / "tap.idx", "a") as f:
            f.write("%s %s %d %d\n" % (q["start"][0], q["channels"][0], off, len(body)))
        self.send_response(204)
        self.end_headers()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--isolate", action="store_true")
    a = ap.parse_args()
    Handler.isolate = a.isolate
    server = http.server.ThreadingHTTPServer(("127.0.0.1", a.port), functools.partial(Handler, directory=str(ROOT)))
    print("serving %s on http://127.0.0.1:%d/" % (ROOT, a.port), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
