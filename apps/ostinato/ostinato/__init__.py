"""Ostinato on the desktop: ``python -m ostinato`` from a checkout.

The browser version and the desktop app share everything in ``app/``. What
differs lives in ``desktop/``: the runner that hosts the engines (processes
or threads), the mixer that plays them through WASAPI, Windows MIDI, and the
page (dialogs, autosave, shortcuts, the control socket the checks use).
Importing this package puts both on ``sys.path`` the way the browser's
workers see their files, desktop first so its counterparts win.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATHS = [os.path.join(ROOT, *p.split("/")) for p in
         ("desktop/ui", "desktop", "app/ui", "app/engine", "app/shared", "vendor/lib")]

for _p in reversed(PATHS):
    if _p not in sys.path:
        sys.path.insert(0, _p)
