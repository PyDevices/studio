"""Download the newest PyDevices wheels Ostinato runs on into vendor/wheels/.

Ostinato always takes the latest released stack, so moving to a new release
is: run this, run the checks, commit wheels.json. The wheels themselves are
not committed (vendor/ is ignored); wheels.json records exactly which files
the app loads, and the service worker caches them for offline use.

    .venv/bin/python scripts/fetch_wheels.py
"""

import json
import pathlib
import urllib.request

INDEX = "https://test.pypi.org/pypi/%s/json"
ABI = "cp314-cp314-pyemscripten_2026_0_wasm32"
PACKAGES = [
    "pydevices",
    "pydevices-audiodsp",
    "pydevices-lvgl",
    "pydevices-audioinstruments",
    "pydevices-audioeffects",
]

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEST = ROOT / "vendor" / "wheels"


def pick(name):
    """The newest version with a browser wheel, pre-releases included: the
    stack's fixes reach TestPyPI as .devN/rcN first."""
    from packaging.version import Version

    with urllib.request.urlopen(INDEX % name) as resp:
        info = json.load(resp)
    for version in sorted(info["releases"], key=Version, reverse=True):
        for want in (ABI, "py3-none-any"):
            for f in info["releases"][version]:
                if f["filename"].endswith(want + ".whl") and not f.get("yanked"):
                    return version, f
    raise SystemExit("no browser wheel for %s" % name)


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    chosen = {}
    for name in PACKAGES:
        version, f = pick(name)
        target = DEST / f["filename"]
        if not target.exists():
            urllib.request.urlretrieve(f["url"], target)
        chosen[name] = {"version": version, "file": f["filename"], "sha256": f["digests"]["sha256"]}
        print("%-28s %-8s %s" % (name, version, f["filename"]))
    (ROOT / "wheels.json").write_text(json.dumps(chosen, indent=2) + "\n")


if __name__ == "__main__":
    main()
