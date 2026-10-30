"""Puts Vamp on a board through mpftp: vamp and songlink in /lib, hmilink
beside them, and a song to start with.

    python tools/vamp_files.py DEVICE [--hmilink DIR] [--song FILE]

DEVICE is what mpftp takes (COM4, ws://<board-address>). Then, on the board::

    import vamp; vamp.run()

``--hmilink`` names the hmilink package to copy (the folder holding
``__init__.py``); the default is a pydevices checkout beside this one, else
Ostinato's vendor/lib. ``--song`` is the song the box opens, /vamp_song.json
on the board (default: Ostinato's SoundFont check song).
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OSTINATO = os.path.join(ROOT, "apps", "ostinato")

PACKAGES = {
    "vamp": ["__init__.py", "engine.py", "device.py", "main.py"],
    "songlink": ["__init__.py", "model.py", "link.py", "page.html"],
}
HMILINK = ["__init__.py", "tcp.py"]
SONG = os.path.join(OSTINATO, "songs", "sketches", "soundfont_check.json")


def _hmilink():
    parent = ROOT
    for _ in range(3):
        parent = os.path.dirname(parent)
        p = os.path.join(parent, "pydevices", "hmilink", "hmilink")
        if os.path.isfile(os.path.join(p, "__init__.py")):
            return p
    return os.path.join(OSTINATO, "vendor", "lib", "hmilink")


def mpftp(*args):
    cmd = [os.environ.get("MPFTP", "mpftp")] + list(args)
    print(" ".join(cmd), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode:
        raise SystemExit(r.stdout + r.stderr)
    return r.stdout


def main(argv):
    device = argv[1]
    hmi = argv[argv.index("--hmilink") + 1] if "--hmilink" in argv else _hmilink()
    song = argv[argv.index("--song") + 1] if "--song" in argv else SONG
    for d in ("/lib", "/lib/vamp", "/lib/songlink", "/lib/hmilink"):
        try:
            mpftp("mkdir", "-d", device, d)
        except SystemExit:
            pass                    # there already; a put into a missing one fails below
    for package, names in PACKAGES.items():
        for name in names:
            mpftp("put", "-d", device, os.path.join(ROOT, "lib", package, name), "/lib/%s/%s" % (package, name))
    for name in HMILINK:
        mpftp("put", "-d", device, os.path.join(hmi, name), "/lib/hmilink/" + name)
    mpftp("put", "-d", device, song, "/vamp_song.json")
    print("Vamp is on %s: import vamp; vamp.run()" % device)


if __name__ == "__main__":
    main(sys.argv)
