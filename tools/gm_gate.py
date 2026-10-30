# SPDX-FileCopyrightText: 2026 PyDevices
#
# SPDX-License-Identifier: MIT
"""Project 5's gate: General MIDI song files played start to finish through
the module, offline, on each interpreter, and measured.

    python tools/gm_gate.py [--only cpython ...] [--songs FOLDER] [--plants]

Run it under WSL, with numpy (the anchor's toolbox Python has it). Two
kinds of song:

* The gate's own (``tools/gm_songs.py``), on a sine bank: the pitch of
  bent and tuned notes (RPN 0, 1 and 2, Increment, RPN null, Reset All
  Controllers and GM System On), and levels after CC7, CC11, CC121 and GM
  System On, each to the cent and the tenth of a dB, and silence after
  All Sound Off, All Notes Off and the pedal lifting.
* Ordinary GM files from ``--songs`` (any ``*.mid``), on the GM bank
  (``--bank``, else ``$SOUNDFONT``, else the first in ``~/soundfonts``): every note
  played, no part holding a note at the end, no voice sounding after the
  tail, nothing raised, and the WAV silent at its end.

Each runs on CPython (a Windows venv, ``--venv-python``), MicroPython
windows (``micropython.exe``), MicroPython unix and CircuitPython unix
(``bin/`` above this checkout). Everything they import is staged into
``--stage`` from this checkout and the audiocomponents checkout given.
``--plants`` runs ``PLANTS`` too, on the first interpreter, and each must
fail. WAVs and logs are left in ``<stage>/out``.
"""

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
INTERPRETERS = ("cpython", "micropython-windows", "micropython-unix", "circuitpython-unix")
#: Each fault and the measure that must catch it. (reset-keeps-bend is the
#: contract's alone: a SoundFont that centres its own bend on reset masks it.)
PLANTS = ("rpn-ignored", "rpn-null-ignored", "reset-keeps-expression", "reset-keeps-rpn",
          "note-off-dropped", "sends-last-wins", "volume-as-macro")
CENTS = 3.0  # a pitch probe's tolerance
DB = 0.5  # a level probe's
RINGING_DB = 50.0  # a reverb tail is louder than this under the note
SILENT_DB = -60.0  # below the reference note, or full scale for a whole file
RATE = 48000


STATS = ("notes", "played", "held", "sounding", "song", "render", "reverb", "chorus", "error")


def _bin(name):
    for parent in [REPO] + _parents(REPO):
        p = os.path.join(parent, "bin", name)
        if os.path.isfile(p):
            return p
    return shutil.which(name)


def _parents(path):
    out = []
    while True:
        up = os.path.dirname(path)
        if up == path:
            return out
        out.append(up)
        path = up


def win_path(path):
    return subprocess.run(["wslpath", "-w", path], check=True, capture_output=True, text=True).stdout.strip()


def stage(args):
    """Copy what the interpreters import into ``args.stage``."""
    root = args.stage
    lib = os.path.join(root, "lib")
    tools = os.path.join(root, "tools")
    tests = os.path.join(root, "tests")
    for d in (lib, tools, tests):
        shutil.rmtree(d, ignore_errors=True)
    shutil.copytree(os.path.join(args.audiocomponents, "lib"), lib,
                    ignore=shutil.ignore_patterns("__pycache__", "*.egg-info", "pyproject.toml"))
    shutil.copytree(os.path.join(REPO, "lib", "soundmodule"), os.path.join(lib, "soundmodule"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    os.makedirs(tools)
    os.makedirs(tests)
    for f in ("render_mid.py", "smf.py"):
        shutil.copy(os.path.join(HERE, f), tools)
    shutil.copy(os.path.join(REPO, "tests", "engine_contract.py"), tests)
    os.makedirs(os.path.join(root, "out"), exist_ok=True)


def command(name, args, song, wav, bank, plant=None):
    root = args.stage
    windows = name in ("cpython", "micropython-windows")
    p = win_path if windows else (lambda x: x)
    tail = ["--bank", p(bank), "--path", p(os.path.join(root, "lib")), "--tail", str(args.tail)]
    if plant:
        tail += ["--plant", plant]
    script = [p(os.path.join(root, "tools", "render_mid.py")), p(song), p(wav)] + tail
    if name == "cpython":
        return [args.venv_python, "-u"] + script
    heap = ["-X", "heapsize=" + args.heap]
    exe = {"micropython-windows": args.micropython_exe, "micropython-unix": args.micropython,
           "circuitpython-unix": args.circuitpython}[name]
    return [exe] + heap + script


def run(name, args, song, bank, plant=None):
    base = os.path.splitext(os.path.basename(song))[0]
    tag = "%s-%s%s" % (base, name, "-" + plant if plant else "")
    wav = os.path.join(args.stage, "out", tag + ".wav")
    cmd = command(name, args, song, wav, bank, plant)
    started = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout, cwd=args.stage)
        out = r.stdout + r.stderr
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
        out += "\nerror 'timed out after %d s'" % args.timeout
    with open(os.path.join(args.stage, "out", tag + ".log"), "w") as f:
        f.write(" ".join(cmd) + "\n" + out)
    stats = {}
    for line in out.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0] in STATS:
            stats[parts[0]] = parts[1]
    stats["wall"] = time.time() - started
    return wav, stats, out


# -- measuring ------------------------------------------------------------------


def load(path):
    import numpy as np
    with wave.open(path) as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float64)
        ch = w.getnchannels()
    return data.reshape(-1, ch).mean(axis=1) / 32768.0


def window(x, t):
    return x[int(t[0] * RATE):int(t[1] * RATE)]


def rms_db(x):
    import numpy as np
    v = float(np.sqrt(np.mean(x * x))) if len(x) else 0.0
    return 20 * np.log10(v) if v > 0 else -200.0


def pitch(x):
    """The strongest partial's frequency, by FFT with a parabola through the peak."""
    import numpy as np
    w = x * np.hanning(len(x))
    n = 1 << (len(w) * 8 - 1).bit_length()
    spec = np.abs(np.fft.rfft(w, n))
    k = int(np.argmax(spec[1:])) + 1
    a, b, c = np.log(spec[k - 1] + 1e-12), np.log(spec[k] + 1e-12), np.log(spec[k + 1] + 1e-12)
    shift = 0.5 * (a - c) / (a - 2 * b + c)
    return (k + shift) * RATE / n


def measure_probes(wav, spec):
    """[(what, ok, detail)] for a gate song's probes."""
    import numpy as np
    x = load(wav)
    ref_db = rms_db(window(x, spec["reference"])) if "reference" in spec else None
    results = []
    loudest = max(abs(x)) if len(x) else 0.0
    for p in spec["probes"]:
        seg = window(x, p["t"])
        if p["kind"] == "pitch":
            f = pitch(seg) if rms_db(seg) > -60 else 0.0
            cents = 1200 * np.log2(f / p["hz"]) if f > 0 else 9999.0
            results.append((p["what"], abs(cents) <= CENTS, "%.2f Hz, want %.2f (%+.1f cents)" % (f, p["hz"], cents)))
        elif p["kind"] == "level":
            d = rms_db(seg) - ref_db
            results.append((p["what"], abs(d - p["db"]) <= DB, "%+.2f dB, want %+.2f" % (d, p["db"])))
        elif p["kind"] == "ringing":
            d = rms_db(seg) - ref_db
            results.append((p["what"], -RINGING_DB < d < -1.0, "%.1f dB under the reference" % -d))
        else:
            base = ref_db if ref_db is not None else 20 * np.log10(loudest + 1e-12)
            d = rms_db(seg) - base
            under = "reference" if ref_db is not None else "loudest"
            results.append((p["what"], d <= SILENT_DB, "%.1f dB under the %s" % (-d, under)))
    return results


def measure_file(wav, stats):
    """[(what, ok, detail)] for an ordinary file played start to finish."""
    import numpy as np
    out = []
    out.append(("finished without an error", "error" not in stats, stats.get("error", "")))
    notes, played = int(stats.get("notes", -1)), int(stats.get("played", -2))
    out.append(("every note-on played", notes == played and notes > 0, "%d of %d" % (played, notes)))
    out.append(("no part holds a note at the end", stats.get("held") == "0", "held %s" % stats.get("held")))
    out.append(("no voice sounding after the tail", stats.get("sounding") == "0",
                "sounding %s" % stats.get("sounding")))
    try:
        x = load(wav)
    except Exception as e:
        out.append(("the WAV reads", False, repr(e)))
        return out
    song = float(stats.get("song", 0))
    full = len(x) / RATE
    out.append(("the WAV runs the song and its tail", full >= song + 2.5, "%.1f s for a %.1f s song" % (full, song)))
    peak = 20 * np.log10(np.max(np.abs(x)) + 1e-12)
    end = rms_db(x[-RATE // 2:])
    out.append(("silent at the end", end - peak <= SILENT_DB, "last 0.5 s %.1f dB under the peak" % (peak - end)))
    return out


# -- the gate -------------------------------------------------------------------


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="+", choices=INTERPRETERS)
    ap.add_argument("--stage", default="/mnt/c/temp/soundmodule-p5/stage")
    ap.add_argument("--songs", default=None, help="a folder of ordinary GM files")
    ap.add_argument("--bank", default=None)
    ap.add_argument("--audiocomponents", default=None)
    ap.add_argument("--venv-python", default="/mnt/c/temp/soundmodule-p5/venv/Scripts/python.exe")
    ap.add_argument("--micropython-exe", default="/mnt/c/Users/bradb/.local/bin/micropython.exe")
    ap.add_argument("--micropython", default=None)
    ap.add_argument("--circuitpython", default=None)
    ap.add_argument("--heap", default="64M")
    # A GM pad's release runs to seven seconds after its note-off.
    ap.add_argument("--tail", type=float, default=10.0)
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument("--plants", action="store_true")
    ap.add_argument("--report", default=None, help="write the results as JSON here")
    args = ap.parse_args(argv)
    if args.audiocomponents is None:
        for parent in _parents(REPO):
            if os.path.isdir(os.path.join(parent, "audiocomponents", "lib")):
                args.audiocomponents = os.path.join(parent, "audiocomponents")
                break
    args.micropython = args.micropython or _bin("micropython")
    args.circuitpython = args.circuitpython or _bin("circuitpython")
    if args.bank is None:
        args.bank = os.environ.get("SOUNDFONT")
    if args.bank is None:
        found = sorted(glob.glob(os.path.expanduser("~/soundfonts/*.sf2")))
        args.bank = found[0] if found else None
    os.makedirs(args.stage, exist_ok=True)
    stage(args)
    gen = os.path.join(args.stage, "gate-songs")
    subprocess.run([sys.executable, os.path.join(HERE, "gm_songs.py"), gen, "--audiocomponents",
                    args.audiocomponents], check=True)
    sine = os.path.join(gen, "sine.sf2")
    own = [os.path.join(gen, n + ".mid") for n in ("bends", "resets")]
    files = sorted(glob.glob(os.path.join(args.songs, "*.mid"))) if args.songs else []
    if files:
        local = os.path.join(args.stage, "songs")
        os.makedirs(local, exist_ok=True)
        for f in files:
            shutil.copy(f, local)
        files = [os.path.join(local, os.path.basename(f)) for f in files]
    names = args.only or INTERPRETERS
    report = {}
    failed = 0
    for name in names:
        print("== %s" % name)
        for song in own + files:
            ours = song in own
            spec = None
            if ours:
                with open(song[:-4] + ".json") as f:
                    spec = json.load(f)
            wav, stats, out = run(name, args, song, sine if ours else args.bank)
            results = measure_probes(wav, spec) if ours and "error" not in stats else measure_file(wav, stats)
            if ours and "error" not in stats:
                results = measure_file(wav, stats)[:4] + results
            bad = [r for r in results if not r[1]]
            failed += len(bad)
            print("  %-36s %s  %d checks, %d failed, %.1f s of music in %s s" % (
                os.path.basename(song), "PASS" if not bad else "FAIL", len(results), len(bad),
                float(stats.get("song", 0)), stats.get("render", "?")))
            for what, ok, detail in results:
                if not ok or ours:
                    print("      %s %-44s %s" % ("ok  " if ok else "FAIL", what, detail))
            report.setdefault(name, {})[os.path.basename(song)] = {
                "stats": stats, "results": [[w, bool(o), d] for w, o, d in results]}
    if args.plants:
        name = names[0]
        print("== plants, on %s: each must fail" % name)
        for plant in PLANTS:
            caught = []
            # The gate's songs end in resets, which free a lost note-off's
            # voice, so a dropped note-off is looked for in ordinary files.
            for song in own + (files if plant == "note-off-dropped" else []):
                ours = song in own
                wav, stats, out = run(name, args, song, sine if ours else args.bank, plant)
                if "PLANTED " + plant not in out:
                    caught.append("not planted")
                    continue
                if not ours:
                    results = measure_file(wav, stats)
                else:
                    with open(song[:-4] + ".json") as f:
                        spec = json.load(f)
                    results = measure_file(wav, stats)[:4]
                    if "error" not in stats:
                        results += measure_probes(wav, spec)
                caught += ["%s: %s" % (os.path.basename(song), w) for w, ok, d in results if not ok]
            good = bool(caught) and "not planted" not in caught
            if not good:
                failed += 1
            print("  %-24s %s  %s" % (plant, "caught" if good else "MISSED", "; ".join(caught[:3])))
            report.setdefault("plants", {})[plant] = caught
    if args.report:
        with open(args.report, "w") as f:
            json.dump(report, f, indent=1)
    print("gate %s" % ("PASSED" if not failed else "FAILED (%d)" % failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
