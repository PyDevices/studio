"""Puts the box on a board: the engine, the song model, the page it serves and
hmilink, through mpftp.

    python tools/box_files.py DEVICE [--hmilink DIR]

DEVICE is what mpftp takes (COM4, ws://192.168.1.50). The files land in
/ostinato on the board, hmilink in /lib/hmilink, and /ostinato_box.py starts
it all::

    import ostinato_box; ostinato_box.main()

``--hmilink`` names the hmilink package to copy (the folder holding
``__init__.py``); the default is the one in vendor/lib.
"""

import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FILES = [
    ("app/box/boxengine.py", "/ostinato/boxengine.py"),
    ("app/box/boxdevice.py", "/ostinato/boxdevice.py"),
    ("app/box/main.py", "/ostinato/box_main.py"),
    ("app/shared/model.py", "/ostinato/model.py"),
    ("app/shared/hmilink_model.py", "/ostinato/hmilink_model.py"),
    ("app/link/page.html", "/ostinato/page.html"),
    ("songs/sketches/soundfont_check.json", "/ostinato/soundfont_check.json"),
]
HMILINK = ["__init__.py", "tcp.py"]

LAUNCHER = '''"""Ostinato's box: python tools/box_files.py put this here."""
import sys

if "/ostinato" not in sys.path:
    sys.path.append("/ostinato")


def main(*args):
    import box_main
    return box_main.main(list(args))
'''


def mpftp(*args):
    cmd = [os.environ.get("MPFTP", "mpftp")] + list(args)
    print(" ".join(cmd), flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode:
        raise SystemExit(r.stdout + r.stderr)
    return r.stdout


def main(argv):
    device = argv[1]
    hmi = os.path.join(ROOT, "vendor", "lib", "hmilink")
    if "--hmilink" in argv:
        hmi = argv[argv.index("--hmilink") + 1]
    for d in ("/ostinato", "/lib", "/lib/hmilink"):
        try:
            mpftp("mkdir", "-d", device, d)
        except SystemExit:
            pass                    # there already; a put into a missing one fails below
    for local, remote in FILES:
        mpftp("put", "-d", device, os.path.join(ROOT, local), remote)
    for name in HMILINK:
        mpftp("put", "-d", device, os.path.join(hmi, name), "/lib/hmilink/" + name)
    with tempfile.TemporaryDirectory() as tmp:
        launcher = os.path.join(tmp, "ostinato_box.py")
        with open(launcher, "w") as f:
            f.write(LAUNCHER)
        mpftp("put", "-d", device, launcher, "/ostinato_box.py")
    print("the box is on %s: import ostinato_box; ostinato_box.main()" % device)


if __name__ == "__main__":
    main(sys.argv)
