# SPDX-FileCopyrightText: 2026 PyDevices
#
# SPDX-License-Identifier: MIT
"""The sound module's gate: REAPER plays a multi-channel song to the module
over a loopback MIDI port, with nothing set up on the module, on each
interpreter in turn.

    python tools/gate.py [--only cpython micropython-windows ...] [--max-lag MS]

Run it under WSL. Each interpreter starts ``soundmodule.run`` with no
setup but the port, REAPER plays ``tools/song.py``'s song to the port in
real time, and the module renders what it hears to a WAV file. The gate
passes for an interpreter when every part arrived with its own notes,
bank and program, the instrument each part asked for is the one that
played, every part's entry can be heard in the WAV, and the render never
fell more than ``--max-lag`` behind the clock.

The interpreters:

``cpython``, ``micropython-windows``
    Windows' own CPython and ``micropython.exe``, reading the port with
    ``midiports.windows``.
``micropython-unix``, ``circuitpython-unix``
    The unix ports under WSL, which can't see a Windows MIDI port: on
    these ``tools/bridge.py`` carries the port to the module through two
    files (``soundmodule.pipes``).

What it needs, on Windows:

* a loopback MIDI port pair: ``--port`` (default ``Sound module``) for
  REAPER to send to, and ``--device`` (default ``Sound module device
  side``) for the module. With Windows MIDI Services:
  ``midi loopback create --name-a "Sound module" --name-b "Sound module device side"``
* REAPER, with a configuration (``--reaper-cfg``) in which that output is
  enabled. The gate never touches any other REAPER configuration.
* a CPython venv (``--venv``) with ``pydevices-audiodsp`` at
  audiocomponents' pin and ``pydevices-desktop``.

Everything the interpreters import is staged into ``--stage`` from the
sibling checkouts' ``origin/main``: audiocomponents' ``lib``,
pydevices' ``midiports`` and ``uwin32``, and this checkout's ``lib``.
The WAV files, the logs and REAPER's project of the song are left in
``<stage>/out`` and ``<stage>/song``.
"""

import argparse
import array
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

INTERPRETERS = ("cpython", "micropython-windows", "micropython-unix", "circuitpython-unix")


def _find_sibling(name):
    """``name``'s checkout beside this one, or beside the folder holding it (a worktree)."""
    here = REPO
    for _ in range(3):
        here = os.path.dirname(here)
        path = os.path.join(here, name)
        if os.path.isdir(os.path.join(path, ".git")) or os.path.isfile(os.path.join(path, ".git")):
            return path
    return None


def win_path(path):
    """A WSL path as Windows sees it."""
    return subprocess.run(["wslpath", "-w", path], check=True, capture_output=True, text=True).stdout.strip()


def _archive(repo, paths, dest):
    """``paths`` from ``repo``'s ``origin/main`` into ``dest``."""
    tar = subprocess.run(["git", "-C", repo, "archive", "origin/main"] + list(paths),
                         check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(tar)) as t:
        t.extractall(dest, filter="data")
    head = subprocess.run(["git", "-C", repo, "rev-parse", "--short", "origin/main"],
                          check=True, capture_output=True, text=True).stdout.strip()
    return head


def stage(args):
    lib = os.path.join(args.stage, "lib")
    if os.path.isdir(lib):
        shutil.rmtree(lib)
    os.makedirs(lib)
    tmp = os.path.join(args.stage, "tmp-archive")
    if os.path.isdir(tmp):
        shutil.rmtree(tmp)
    heads = {}
    heads["audiocomponents"] = _archive(args.audiocomponents, ["lib"], tmp)
    for name in os.listdir(os.path.join(tmp, "lib")):
        shutil.move(os.path.join(tmp, "lib", name), lib)
    heads["pydevices"] = _archive(args.pydevices, ["midiports/midiports", "utils/uwin32.py"], tmp)
    shutil.move(os.path.join(tmp, "midiports", "midiports"), lib)
    shutil.move(os.path.join(tmp, "utils", "uwin32.py"), lib)
    shutil.rmtree(tmp)
    shutil.copytree(os.path.join(REPO, "lib", "soundmodule"), os.path.join(lib, "soundmodule"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    tools = os.path.join(args.stage, "tools")
    os.makedirs(tools, exist_ok=True)
    shutil.copy(os.path.join(HERE, "bridge.py"), tools)
    os.makedirs(os.path.join(args.stage, "reaper"), exist_ok=True)
    shutil.copy(os.path.join(HERE, "play.lua"), os.path.join(args.stage, "reaper"))
    song = os.path.join(args.stage, "song")
    if os.path.isdir(song):
        shutil.rmtree(song)
    subprocess.run([sys.executable, os.path.join(HERE, "song.py"), song], check=True, capture_output=True)
    with open(os.path.join(song, "song.json")) as f:
        info = json.load(f)
    out = os.path.join(args.stage, "out")
    os.makedirs(out, exist_ok=True)
    return info, heads


def _wait_for(path, text, seconds, proc):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            with open(path, errors="replace") as f:
                if text in f.read():
                    return True
        except OSError:
            pass
        if proc.poll() is not None:
            return False
        time.sleep(0.2)
    return False


def _reaper(args, log):
    cfg = os.path.join(args.stage, "reaper", "play-config.lua")
    w = lambda p: win_path(p).replace("\\", "\\\\")  # noqa: E731
    with open(cfg, "w") as f:
        f.write('SONG = "%s"\nPORT = "%s"\nPROJECT = "%s"\nLOG = "%s"\n' % (
            w(os.path.join(args.stage, "song")), args.port, w(os.path.join(args.stage, "song", "song.rpp")), w(log)))
    if os.path.exists(log):
        os.remove(log)
    session = log[:-len("-reaper.log")] + ".rpp"  # REAPER saves the song's tracks into it at the end
    shutil.copy(os.path.join(args.stage, "reaper", "empty.rpp"), session)
    try:
        subprocess.run([args.reaper, "-cfgfile", args.reaper_cfg, "-ignoreerrors", "-nosplash",
                        win_path(session), win_path(os.path.join(args.stage, "reaper", "play.lua"))],
                       timeout=120, capture_output=True)
    except subprocess.TimeoutExpired:
        _kill_windows(args.reaper_cfg)
    with open(log) as f:
        return f.read()


def _kill_windows(marker):
    """End the Windows processes whose command line holds ``marker``: a
    timeout here only ends WSL's side of them."""
    script = ("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*%s*' -and "
              "$_.Name -ne 'powershell.exe' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
              % marker.replace("'", "''"))
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", script], capture_output=True, timeout=60)


def _command(args, name, pipe, wav):
    run = ["-m", "soundmodule.run", "--wav", wav, "--idle", "3", "--wait", "90", "--seconds", "120"]
    env = dict(os.environ)
    if name == "cpython":
        return [args.venv_python, "-u"] + run + ["--port", args.device], env
    heap = ["-X", "heapsize=%s" % args.heap]  # five instruments outgrow the 8 MB (2 MB) default
    if name == "micropython-windows":
        env["MICROPYPATH"] = os.path.join(args.stage, "lib")
        env["WSLENV"] = (env.get("WSLENV", "") + ":MICROPYPATH/l").lstrip(":")
        return [args.micropython_exe] + heap + run + ["--port", args.device], env
    env["MICROPYPATH"] = ".frozen:" + os.path.join(args.stage, "lib")
    exe = args.micropython if name == "micropython-unix" else args.circuitpython
    return [exe] + heap + run + ["--pipe", pipe], env


REPORT = re.compile(r"soundmodule: (.*): (\d+) bytes of MIDI, (\d+) Identity Requests answered, "
                    r"render behind by at most (\d+) ms while playing \((\d+) ms before the first note\)")
CHANNEL = re.compile(r"channel +(\d+): +(\d+) notes, bank (\d+):(\d+) program (\d+), (.*)")


def _rms_db(samples, start, end):
    s = samples[max(0, start):max(0, end)]
    if not s:
        return -120.0
    total = 0
    for v in s:
        total += v * v
    return max(-120.0, 20 * math.log10(max(math.sqrt(total / len(s)), 1e-9) / 32768.0))


def analyse_wav(path, info):
    """Each part's entry in the WAV: the level just after it in dBFS, the
    file aligned on its first sound (the first part's first note)."""
    with wave.open(path) as w:
        rate, channels = w.getframerate(), w.getnchannels()
        data = array.array("h", w.readframes(w.getnframes()))
    mono = array.array("h", data[::channels])  # left is enough to hear what came in
    onset = next((i for i, v in enumerate(mono) if abs(v) > 300), None)
    if onset is None:
        return None, None, []
    end = len(mono) - next(i for i, v in enumerate(reversed(mono)) if abs(v) > 300)
    first = min(p["starts"] for p in info["parts"])
    entries = []
    for p in sorted(info["parts"], key=lambda p: p["starts"]):
        at = onset + int((p["starts"] - first) * rate)
        entries.append((p, _rms_db(mono, at + rate // 4, at + rate * 7 // 4),
                        _rms_db(mono, at - rate * 7 // 4, at - rate // 4)))
    return onset / rate, (end - onset) / rate, entries


def judge(name, output, info, wav, max_lag):
    """``(passed, lines)`` for one interpreter's run."""
    lines = []
    ok = True
    m = REPORT.search(output)
    if not m:
        return False, ["no report from the module"] + output.strip().splitlines()[-8:]
    interp, total, identities, lag = m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4))
    lines.append("%s: %d bytes of MIDI, render behind by at most %d ms while playing (%s ms in the setup bar), "
                 "%d Identity Requests answered" % (interp, total, lag, m.group(5), identities))
    if lag > max_lag:
        ok = False
        lines.append("FAIL: the render fell %d ms behind (more than %d)" % (lag, max_lag))
    heard = {}
    for c in CHANNEL.finditer(output):
        heard.setdefault(int(c.group(1)), []).append(c)
    for p in info["parts"]:
        cs = heard.get(p["channel"])
        if not cs:
            ok = False
            lines.append("FAIL: channel %d (%s) played nothing" % (p["channel"], p["instrument"]))
            continue
        if len(cs) > 1:
            ok = False
            lines.append("FAIL: channel %d played more than one sound" % p["channel"])
        c = cs[0]
        got = (int(c.group(2)), int(c.group(3)), int(c.group(4)), int(c.group(5)))
        want = (p["notes"], p["bank_msb"], p["bank_lsb"], p["program"])
        sound = c.group(6).strip()
        right = got == want and sound.split(":")[0] == p["instrument"]
        ok = ok and right
        lines.append("%s channel %2d: %3d/%3d notes, bank %d:%d program %d, %s"
                     % ("ok  " if right else "FAIL", p["channel"], got[0], want[0], got[1], got[2], got[3], sound))
    onset, span, entries = analyse_wav(wav, info)
    if onset is None:
        return False, lines + ["FAIL: the WAV is silent"]
    # From the first note to the last sound: the song's notes, then the release.
    first = min(p["starts"] for p in info["parts"])
    music = info["music_ends"] - first
    timed = music - 0.5 <= span <= music + 4.0  # the reverb rings on a while
    ok = ok and timed
    lines.append("%s the sound lasts %.1f s from the first note; the song's notes take %.1f s"
                 % ("ok  " if timed else "FAIL", span, music))
    for p, after, before in entries:
        heard_it = after > -50.0
        ok = ok and heard_it
        lines.append("%s %-9s enters at %4.1f s: %6.1f dBFS after, %6.1f before"
                     % ("ok  " if heard_it else "FAIL", p["instrument"], p["starts"], after, before))
    return ok, lines


def run_one(args, name, info):
    out = os.path.join(args.stage, "out")
    wav = os.path.join(out, name + ".wav")
    log = os.path.join(out, name + ".log")
    for p in (wav, log):
        if os.path.exists(p):
            os.remove(p)
    bridge = None
    pipe = None
    if name.endswith("-unix"):
        pipe = os.path.join(args.work, "pipe-" + name)
        if os.path.isdir(pipe):
            shutil.rmtree(pipe)
        os.makedirs(pipe)
        blog = os.path.join(out, name + "-bridge.log")
        bridge = subprocess.Popen([args.venv_python, "-u", win_path(os.path.join(args.stage, "tools", "bridge.py")),
                                   "--port", args.device, "--pipe", win_path(pipe), "--seconds", "300"],
                                  cwd=os.path.join(args.stage, "lib"), stdout=open(blog, "w"),
                                  stderr=subprocess.STDOUT)
        if not _wait_for(blog, "bridge:", 60, bridge):
            bridge.kill()
            return False, ["FAIL: the bridge didn't start"] + open(blog).read().splitlines()[-5:]
    target = win_path(wav) if name in ("cpython", "micropython-windows") else wav
    cmd, env = _command(args, name, pipe, target)
    started = time.monotonic()
    proc = subprocess.Popen(cmd, cwd=os.path.join(args.stage, "lib"), env=env,
                            stdout=open(log, "w"), stderr=subprocess.STDOUT)
    try:
        if not _wait_for(log, "listening on", 90, proc):
            proc.kill()
            return False, ["FAIL: the module didn't start"] + open(log).read().splitlines()[-8:]
        play = _reaper(args, os.path.join(out, name + "-reaper.log"))
        proc.wait(timeout=150)
    finally:
        if proc.poll() is None:
            proc.kill()
            if target != wav:  # a Windows interpreter: end its Windows side too
                _kill_windows(target)
        if bridge is not None:
            open(os.path.join(pipe, "stop"), "w").close()
            try:
                bridge.wait(timeout=20)
            except subprocess.TimeoutExpired:
                bridge.kill()
                _kill_windows(win_path(pipe))
    with open(log, errors="replace") as f:
        output = f.read()
    ok, lines = judge(name, output, info, wav, args.max_lag)
    reached = re.search(r"reached ([\d.]+) s of ([\d.]+)", play)
    if not reached or float(reached.group(1)) < float(reached.group(2)) - 1:
        ok = False
        lines.insert(0, "FAIL: REAPER didn't play the song through: " + " | ".join(play.strip().splitlines()[-3:]))
    lines.append("took %.0f s; %s" % (time.monotonic() - started, play.strip().splitlines()[-1]))
    return ok, lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", nargs="+", choices=INTERPRETERS)
    ap.add_argument("--stage", default="/mnt/c/temp/soundmodule")
    ap.add_argument("--work", default=os.environ.get("TMPDIR", "/tmp") + "/soundmodule-gate")
    ap.add_argument("--port", default="Sound module")
    ap.add_argument("--device", default="Sound module device side")
    ap.add_argument("--reaper", default="/mnt/c/Program Files/REAPER (x64)/reaper.exe")
    ap.add_argument("--reaper-cfg", default=r"C:\temp\soundmodule\reaper\cfg.ini")
    ap.add_argument("--venv-python", default=None)
    ap.add_argument("--micropython-exe", default=shutil.which("micropython.exe"))
    ap.add_argument("--micropython", default=None)
    ap.add_argument("--circuitpython", default=None)
    ap.add_argument("--audiocomponents", default=_find_sibling("audiocomponents"))
    ap.add_argument("--pydevices", default=_find_sibling("pydevices"))
    ap.add_argument("--max-lag", type=int, default=250)
    ap.add_argument("--heap", default="64M", help="MicroPython's and CircuitPython's heap")
    args = ap.parse_args(argv)
    anchor = os.path.dirname(args.pydevices) if args.pydevices else ""
    args.venv_python = args.venv_python or os.path.join(args.stage, "venv", "Scripts", "python.exe")
    args.micropython = args.micropython or os.path.join(anchor, "bin", "micropython")
    args.circuitpython = args.circuitpython or os.path.join(anchor, "bin", "circuitpython")
    os.makedirs(args.work, exist_ok=True)
    rpp = os.path.join(args.stage, "reaper", "empty.rpp")
    os.makedirs(os.path.dirname(rpp), exist_ok=True)
    if not os.path.exists(rpp):
        with open(rpp, "w") as f:
            f.write('<REAPER_PROJECT 0.1 "7.0" 0\n>\n')
    info, heads = stage(args)
    print("staged audiocomponents %s, pydevices %s; the song: %d parts, %.0f s"
          % (heads["audiocomponents"], heads["pydevices"], len(info["parts"]), info["seconds"]))
    results = {}
    for name in args.only or INTERPRETERS:
        print("\n== %s" % name, flush=True)
        ok, lines = run_one(args, name, info)
        results[name] = ok
        for line in lines:
            print("  " + line, flush=True)
        print("  %s" % ("PASS" if ok else "FAIL"), flush=True)
    print("\ngate: " + ", ".join("%s %s" % (n, "pass" if ok else "FAIL") for n, ok in results.items()))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
