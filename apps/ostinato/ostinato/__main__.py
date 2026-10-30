"""Start Ostinato on the desktop.

    python -m ostinato [--engines processes|threads] [--midi NAME]... [--fresh]
    python -m ostinato --split [--port 9100]
    python -m ostinato --device [--port 9100] [--song FILE]
    python -m ostinato --connect HOST:PORT

It opens the app maximised, with the engines in processes (the default) or
in threads, the mixer playing through WASAPI, and every MIDI input there is
(or the ones ``--midi`` names).

``--split`` runs the engines and the song in a process of their own, an
hmilink device (desktop/device.py), and the window as one of its panels over
TCP; a browser at http://127.0.0.1:9100/ is another. ``--device`` is that
process alone, with no window, and ``--connect`` the window alone, joining a
device that is already running. The rest is for the checks and for
measuring: ``--control PORT`` opens the control socket (desktop/control.py),
``--test-files DIR`` stands a folder in for the file dialogs, ``--data DIR``
moves the autosave.
"""

import argparse
import importlib
import os
import sys

import ostinato  # noqa: F401  (the paths; also what a spawned engine process imports first)


def parse(argv):
    ap = argparse.ArgumentParser(prog="python -m ostinato", description="Ostinato, a groovebox and instrument")
    ap.add_argument("--engines", choices=("processes", "threads"), default="processes",
                    help="run the engines as processes (the default) or as threads (a debug path; parallel on 3.15t)")
    ap.add_argument("--count", type=int, default=4, help="how many engines (default 4, one a track)")
    ap.add_argument("--midi", action="append", default=[],
                    help="MIDI inputs to open, by part of their name (repeat it; default every input)")
    ap.add_argument("--midi-out", default="Ostinato Loopback", help="where the control socket's MIDI goes")
    ap.add_argument("--fresh", action="store_true", help="start without the last session or the first-run guide")
    ap.add_argument("--data", default=None, help="where the autosave and cached fonts live")
    ap.add_argument("--control", type=int, default=0, help="open the control socket on this port")
    ap.add_argument("--test-files", default=None, help="a folder that stands in for the Open and Save dialogs")
    ap.add_argument("--size", default=None, help="WxH: a window of this size instead of maximised")
    ap.add_argument("--start-frames", type=int, default=1536)
    ap.add_argument("--min-frames", type=int, default=768, help="the output queue's floor (16 ms)")
    ap.add_argument("--ahead-frames", type=int, default=512, help="how far ahead the engines render")
    ap.add_argument("--prio", default="high", choices=("high", "above", "normal"),
                    help="the processes' priority class")
    ap.add_argument("--split", action="store_true",
                    help="the engines and the song in a process of their own (an hmilink device), this window its panel")
    ap.add_argument("--device", action="store_true", help="only the engines and the song, no window")
    ap.add_argument("--connect", default=None, help="HOST:PORT: only the window, a panel of a running device")
    ap.add_argument("--port", type=int, default=9100, help="the device's port (--split, --device)")
    ap.add_argument("--bind", default="0.0.0.0", help="where the device listens (default every interface)")
    ap.add_argument("--song", default=None, help="a song for --device to start with")
    ap.add_argument("--plant", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--parent", action="store_true", help=argparse.SUPPRESS)   # --split's device: quit with the window
    ap.add_argument("--gc", default="auto", choices=("auto", "freeze", "off"),
                    help="after start: freeze what's built and collect rarely, or stop collecting "
                         "(auto: freeze when the GIL is off, else as usual)")
    return ap.parse_args(argv)


def run_device(a, prio):
    """The engines, the mixer and the song, served over hmilink until Ctrl+C,
    or, started by --split, until the window that started it goes."""
    import device
    import runners

    opts = {"start_frames": a.start_frames, "min_frames": a.min_frames, "ahead_frames": a.ahead_frames}
    runner = runners.make("processes", a.count, opts, prio=prio, midi_names=a.midi)
    runner.start()
    runners._priority(prio)
    try:
        device.run(runner, a.count, port=a.port, bind=a.bind, song_path=a.song, plant=a.plant, parent=a.parent)
    except KeyboardInterrupt:
        pass
    finally:
        runner.stop()


def start_device(a):
    """--split: this same command as a device, in a process of its own."""
    import subprocess

    args = [sys.executable, "-m", "ostinato", "--device", "--port", str(a.port), "--bind", a.bind,
            "--count", str(a.count), "--prio", a.prio,
            "--start-frames", str(a.start_frames), "--min-frames", str(a.min_frames),
            "--ahead-frames", str(a.ahead_frames), "--parent"]
    for name in a.midi:
        args += ["--midi", name]
    if a.plant:
        args += ["--plant", a.plant]
    # It quits when this window's link closes (--parent). Not a pipe on its
    # stdin to watch: on Windows a thread blocked reading one stalls the
    # engine processes it starts.
    return subprocess.Popen(args, stdin=subprocess.DEVNULL, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv=None):
    a = parse(argv)
    prio = None if a.prio == "normal" else a.prio
    if a.device:
        run_device(a, prio)
        return
    if a.size:
        w, h = a.size.lower().split("x")
        os.environ["PYDEVICES_WIDTH"], os.environ["PYDEVICES_HEIGHT"] = w, h
        os.environ["PYDEVICES_SCALE"] = "1"
    else:
        os.environ["PYDEVICES_MAXIMIZED"] = "1"
    os.environ["ENGINES"] = str(a.count)

    import engines
    import page
    import runners

    if a.data:
        page.config["data"] = os.path.abspath(a.data)
    page.config["fresh"] = a.fresh
    page.config["test_files"] = os.path.abspath(a.test_files) if a.test_files else None

    opts = {"start_frames": a.start_frames, "min_frames": a.min_frames, "ahead_frames": a.ahead_frames}
    linked = bool(a.split or a.connect)
    if linked:
        from linkrunner import LinkRunner

        if a.connect:
            host, _, port = a.connect.rpartition(":")
            runner = LinkRunner(host or "127.0.0.1", int(port))
            # The device's song is the one to show: no autosave over it.
            runner.adopt = True
            page.config["fresh"] = True
        else:
            runner = LinkRunner("127.0.0.1", a.port, proc=start_device(a))
    else:
        # The processes runner reads MIDI in its mixer process; threads read it here.
        runner = runners.make(a.engines, a.count, opts, prio=prio,
                              midi_names=a.midi if a.engines == "processes" else None)
    runner.start()
    runners._priority(prio)
    engines.runner = runner
    print("Ostinato: %d engines as %s, Python %s%s" % (
        a.count, "a device's panel on port %d" % runner.port if linked else a.engines, sys.version.split()[0],
        "" if getattr(sys, "_is_gil_enabled", lambda: True)() else ", GIL off"), flush=True)

    from midi import MidiIn

    midi = None
    if a.engines == "threads" and not linked:
        midi = MidiIn(runner, a.midi)
        midi.start()
    control = None
    try:
        import window

        window.open_window(page)
        if a.control:
            from control import Control

            control = Control(a.control, runner, a.midi_out, window.quit)
            control.start()
        gc_mode = a.gc
        if gc_mode == "auto":
            # Without the GIL a collection stops every thread, the mixer's too
            # (an underrun each, Gate 0): collect rarely. With it, as usual.
            gc_mode = "auto" if getattr(sys, "_is_gil_enabled", lambda: True)() else "freeze"
        if gc_mode != "auto":
            page.tell(lambda: window.gc_policy(gc_mode))
        importlib.import_module("main")         # app/ui/main.py: builds the UI and runs until the window closes
    finally:
        if midi is not None:
            midi.stop()
        runner.stop()


if __name__ == "__main__":
    main()
