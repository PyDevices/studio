"""Download midiports and adafruit_midi into vendor/lib/ for the engine,
hmilink for the desktop's two processes, and the UI worker's board config
into vendor/board/.

midiports comes from a tagged pydevices release, pinned here; adafruit_midi
comes from the list in midiports' own package.json at that tag, and the board
config is that release's board_configs/workerdisplay. When midiports ships as
a wheel the MIDI half goes away and the wheel carries it.

    .venv/bin/python scripts/fetch_midi.py
"""

import json
import pathlib
import urllib.request

PYDEVICES = "v0.7.6"  # a pydevices release tag
RAW = "https://raw.githubusercontent.com/PyDevices/pydevices/%s/" % PYDEVICES
# hmilink isn't in a release yet: a pydevices main commit, until one carries it.
HMILINK = "aa6911844168c21296a8f72dc825d744bf2a6c1f"
ROOT = pathlib.Path(__file__).resolve().parent.parent
DEST = ROOT / "vendor" / "lib"
BOARD = ROOT / "vendor" / "board"
# The browser's engines read the page's relay (webmidi.py) and the desktop app
# reads Windows MIDI (windows.py; tools/gen_boot.py leaves it out of the
# browser's files). The other transports are for other hosts; usb_host.py and
# usb_device.py import usb.core, which Ostinato's runtimes don't have.
SKIP = (
    "midiports/uart.py",
    "midiports/linux.py",
    "midiports/usb_host.py",
    "midiports/usb_device.py",
)


def get(url):
    with urllib.request.urlopen(url) as resp:
        return resp.read()


def fetch(package_json, dest, skip=(), raw=RAW):
    package = json.loads(get(raw + package_json))
    for target, source in package["urls"]:
        if target in skip:
            continue
        if source.startswith("github:PyDevices/pydevices/"):
            source = raw + source[len("github:PyDevices/pydevices/"):]
        path = dest / target
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(get(source))
        print(path.relative_to(ROOT).as_posix())


def main():
    fetch("midiports/package.json", DEST, SKIP)
    # The engine process is an hmilink device and the UI one of its panels
    # (desktop/device.py, desktop/linkrunner.py).
    fetch("hmilink/package.json", DEST, ("hmilink/osc.py",),
          "https://raw.githubusercontent.com/PyDevices/pydevices/%s/" % HMILINK)
    # The UI worker draws on the canvas the page hands it (tools/gen_boot.py
    # gives the UI everything in vendor/board/).
    fetch("board_configs/workerdisplay/package.json", BOARD)


if __name__ == "__main__":
    main()
