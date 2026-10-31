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
    "omnipython-audiodsp",
    "pydevices-lvgl",
    "omnipython-audioinstruments",
    "omnipython-audioeffects",
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


PYODIDE_CDN = "https://cdn.jsdelivr.net/pyodide/v%s/full/"


def numpy_wheel():
    """numpy as the vendored Pyodide's lock file names it. The instruments
    import it, and the offline bundle leaves it out, so it comes from
    Pyodide's own CDN, checked against the lock's hash."""
    import hashlib

    lock = json.loads((ROOT / "vendor" / "pyscript" / "pyodide" / "pyodide-lock.json").read_text())
    pkg = lock["packages"]["numpy"]
    release = (ROOT / "vendor" / "pyscript" / "pyodide" / "pyodide.js").read_text(errors="ignore")
    import re

    found = re.findall(r"cdn\.jsdelivr\.net/pyodide/v([0-9a-z.]+)", release) or re.findall(r"\b(3[0-9]{2}\.[0-9]+\.[0-9]+)\b", release)
    target = DEST / pkg["file_name"]
    if not target.exists():
        for version in dict.fromkeys(found):
            try:
                urllib.request.urlretrieve(PYODIDE_CDN % version + pkg["file_name"], target)
                break
            except OSError:
                continue
    if hashlib.sha256(target.read_bytes()).hexdigest() != pkg["sha256"]:
        raise SystemExit("numpy: %s doesn't match the lock file" % target.name)
    return pkg["version"], pkg["file_name"], pkg["sha256"]


def main():
    DEST.mkdir(parents=True, exist_ok=True)
    chosen = {}
    version, file_name, digest = numpy_wheel()
    chosen["numpy"] = {"version": version, "file": file_name, "sha256": digest}
    print("%-28s %-8s %s" % ("numpy", version, file_name))
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
