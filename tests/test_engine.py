# SPDX-FileCopyrightText: 2026 PyDevices
#
# SPDX-License-Identifier: MIT
"""``engine_contract.py`` on CPython, unix MicroPython and unix
CircuitPython, then once per planted fault.

    python -m unittest discover -s tests

The interpreters: this Python; ``$PYDEVICES_MICROPYTHON`` and
``$PYDEVICES_CIRCUITPYTHON``, else ``micropython`` and ``circuitpython`` on
the PATH, else ``bin/`` in a folder above this checkout. One that isn't
there is skipped, loudly. The ``lib`` folders of audioinstruments and
audioeffects (``$AUDIOINSTRUMENTS_DIR`` and ``$AUDIOEFFECTS_DIR``, else
checkouts beside this one) go on every interpreter's path, so the
real-instrument checks run; they need audiodsp installed or built in.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
CONTRACT = str(TESTS / "engine_contract.py")
sys.path.insert(0, str(TESTS))

from engine_contract import PLANTS  # noqa: E402


def _find(env, name):
    explicit = os.environ.get(env)
    if explicit:
        return explicit
    found = shutil.which(name)
    if found:
        return found
    for parent in TESTS.parents:
        candidate = parent / "bin" / name
        if candidate.is_file() and os.access(str(candidate), os.X_OK):
            return str(candidate)
    return None


def audio_checkout(name):
    """The checkout of audioinstruments or audioeffects: ``$AUDIOINSTRUMENTS_DIR``
    or ``$AUDIOEFFECTS_DIR``, else one beside this checkout (or beside the
    repository this is a worktree of)."""
    explicit = os.environ.get(name.upper() + "_DIR")
    if explicit:
        return explicit
    for parent in TESTS.parents:
        candidate = parent / name
        if (candidate / "lib" / name).is_dir():
            return str(candidate)
    return None


def audio_libs():
    """The ``lib`` folders of audioinstruments and audioeffects that exist."""
    found = [audio_checkout(n) for n in ("audioinstruments", "audioeffects")]
    return [os.path.join(c, "lib") for c in found if c]


def _cmd(kind):
    if kind == "cpython":
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(audio_libs() + [env["PYTHONPATH"]] if env.get("PYTHONPATH") else audio_libs())
        return [sys.executable, CONTRACT], env
    exe = _find("PYDEVICES_MICROPYTHON", "micropython") if kind == "micropython" else \
        _find("PYDEVICES_CIRCUITPYTHON", "circuitpython")
    if exe is None:
        return None, None
    env = dict(os.environ)
    env["MICROPYPATH"] = ":".join([".frozen"] + audio_libs())
    return [exe, "-X", "heapsize=64M", CONTRACT], env


def _run(cmd, env, *args):
    with tempfile.TemporaryDirectory() as tmp:
        return subprocess.run(cmd + list(args) + ["--tmp", tmp], env=env, capture_output=True, text=True,
                              timeout=300)


class Contract(unittest.TestCase):
    def _contract(self, kind):
        cmd, env = _cmd(kind)
        if cmd is None:
            message = "no %s: the contract did NOT run on it" % kind
            sys.stderr.write("\n*** " + message + " ***\n")
            self.skipTest(message)
        result = _run(cmd, env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(" 0 failed", result.stdout)
        return result

    def test_cpython(self):
        self._contract("cpython")

    def test_micropython(self):
        self.assertIn("on micropython", self._contract("micropython").stdout)

    def test_circuitpython(self):
        self.assertIn("on circuitpython", self._contract("circuitpython").stdout)


class PlantedFaults(unittest.TestCase):
    """A check that can't fail proves nothing: each plant must fail its check."""

    def _plants(self, kind):
        cmd, env = _cmd(kind)
        if cmd is None:
            self.skipTest("no %s" % kind)
        clean = _run(cmd, env).stdout
        for plant in PLANTS:
            with self.subTest(plant=plant):
                if plant == "frames-miscounted" and "skip a real instrument" in clean:
                    continue  # its check needs the audio modules
                result = _run(cmd, env, "--plant=" + plant)
                self.assertIn("PLANTED " + plant, result.stdout, result.stdout + result.stderr)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("FAIL ", result.stdout, result.stdout + result.stderr)

    def test_cpython(self):
        self._plants("cpython")

    def test_micropython(self):
        self._plants("micropython")


if __name__ == "__main__":
    unittest.main()
