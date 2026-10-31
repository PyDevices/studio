"""Ostinato on CPython, Phase 0 spike: the demo song through the desktop
runner and mixer, live MIDI from a Windows port, and the shared UI drawing.

    python.exe -I desktop/app.py --engines processes|threads --seconds 60 --out runs/x
        [--midi "Ostinato Loopback"] [--no-ui] [--min-frames 1024] [--plant delay|drop]

It loads the demo song, adds a fifth track (Karplus, a sound that decays, so
every key's onset can be found), selects it for live MIDI, plays the song in
song mode, and taps everything the mixer writes to ``--out``. It stops itself
after ``--seconds`` and writes ``run.json`` (MIDI arrivals, stats).
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import ostinato  # noqa: E402,F401  (the same paths as python -m ostinato)


def main():
    import argparse
    import json
    import threading
    import time

    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", choices=("processes", "threads"), default="processes")
    ap.add_argument("--count", type=int, default=5)
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--out", default="runs/spike")
    ap.add_argument("--midi", default="Ostinato Loopback")
    ap.add_argument("--no-ui", action="store_true")
    ap.add_argument("--start-frames", type=int, default=1536)
    ap.add_argument("--min-frames", type=int, default=1024)
    ap.add_argument("--fixed-frames", type=int, default=0)
    ap.add_argument("--ahead-frames", type=int, default=0)
    ap.add_argument("--gc", default="auto", choices=("auto", "freeze", "off"),
                    help="freeze: move everything built at start out of the collector's way and collect rarely")
    ap.add_argument("--full-redraw", action="store_true", help="redraw the whole screen every UI tick")
    ap.add_argument("--plant", default=None)
    ap.add_argument("--prio", default=None, choices=(None, "high", "above"))
    ap.add_argument("--width", type=int, default=0, help="default: maximised, the window's client area at scale 1")
    ap.add_argument("--height", type=int, default=0)
    a = ap.parse_args()
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)

    from songlink import model
    import runners

    with open(os.path.join(ROOT, "assets", "demo_song.json")) as f:
        project = json.load(f)
    model.migrate(project)
    model._bump_ids(project)
    live = model.track("melodic", "electric_piano", "Live")
    live["volume"] = 110
    project["tracks"].append(live)
    project["song_mode"] = True
    live_engine = len(project["tracks"]) - 1          # one engine per track: track i on engine i
    opts = {"start_frames": a.start_frames, "min_frames": a.min_frames, "fixed_frames": a.fixed_frames or None, "ahead_frames": a.ahead_frames,
            "plant": a.plant if a.plant == "drop" else None}
    runner = runners.make(a.engines, a.count, opts, plant=a.plant if a.plant in ("delay", "lose", "detune") else None, prio=a.prio)
    runner.start()
    if a.prio:
        runners._priority(a.prio)

    # -- MIDI: read once here, relayed to every engine with its stamp ----------------
    arrivals = []
    midi_stop = threading.Event()

    def midi_thread():
        from mixer import timer_resolution_1ms

        timer_resolution_1ms()
        import adafruit_midi
        from adafruit_midi.control_change import ControlChange  # noqa: F401
        from adafruit_midi.note_off import NoteOff  # noqa: F401
        from adafruit_midi.note_on import NoteOn  # noqa: F401
        from adafruit_midi.pitch_bend import PitchBend  # noqa: F401
        from midiports import windows

        ins = windows.find(a.midi, "in")
        if not ins:
            print("no MIDI input matching", a.midi)
            return
        port = windows.open_port(ins[0])
        midi = adafruit_midi.MIDI(midi_in=port, in_buf_size=64)
        print("MIDI in:", ins[0].name, flush=True)
        try:
            while not midi_stop.is_set():
                if not port.any() and not midi._in_buf:
                    time.sleep(0.0005)
                    continue
                stamp = time.perf_counter_ns()
                while True:
                    msg = midi.receive()
                    if msg is None:
                        break
                    if getattr(msg, "_STATUS", None) is None:
                        continue
                    d = bytes(msg)
                    if len(d) < 2 or d[0] >= 0xF0:
                        continue
                    d2 = d[2] if len(d) > 2 else 0
                    runner.midi(d[0], d[1], d2, stamp)
                    arrivals.append((stamp, d[0], d[1], d2))
        finally:
            port.close()

    mt = threading.Thread(target=midi_thread, name="midi", daemon=True)

    state = {"phase": "boot", "t0": None, "stats": [], "frames": []}

    def finish():
        runner.mixer({"t": "tap", "on": False, "folder": out})
        closed = runners.wait_for(runner, lambda m: m.get("t") == "tap_closed", 20)
        midi_stop.set()
        runner.mixer({"t": "stats"})
        last = runners.wait_for(runner, lambda m: m.get("t") == "mixer", 5)
        info = {"engines": a.engines, "python": sys.version, "gil": getattr(sys, "_is_gil_enabled", lambda: True)(),
                "seconds": a.seconds, "live_engine": live_engine, "ui": not a.no_ui,
                "arrivals": arrivals, "mixer": last, "stats": state["stats"], "tap_closed": bool(closed),
                "lv_frames": state["frames"][-20000:], "plant": a.plant, "opts": opts,
                "full_redraw": getattr(a, "full_redraw", False), "gc_mode": a.gc,
                "window": state.get("window")}
        with open(os.path.join(out, "run.json"), "w") as f:
            json.dump(info, f)
        print("run written", out, "mixer", json.dumps(last)[:400], flush=True)

    def orchestrate(engines_link, ui_app=None):
        """Called about 30 times a second: load, wait for built, play, tap, stop."""
        now = time.monotonic()
        ph = state["phase"]
        if ph == "boot":
            state["phase"] = "loading"
            state["since"] = now
            if ui_app is not None:
                ui_app.load_project(project)
                ui_app.select_track(live["id"])
            else:
                engines_link.load(project)
                engines_link.select(live["id"])
        elif ph == "loading":
            if engines_link.settled() and now - state["since"] > 1.0:
                state["phase"] = "playing"
                runner.mixer({"t": "tap", "on": True, "folder": out, "lane": live_engine})
                if ui_app is not None:
                    ui_app.toggle_play()
                else:
                    engines_link.play()
                mt.start()
                if a.gc != "auto":
                    import gc

                    gc.collect()
                    gc.freeze()
                    if a.gc == "off":
                        gc.disable()
                    else:
                        gc.set_threshold(50000, 50, 1000)
                state["t0"] = now
                print("playing", flush=True)
                with open(os.path.join(out, "ready"), "w") as f:
                    f.write(str(time.perf_counter_ns()))
            elif now - state["since"] > 60:
                print("engines never settled", engines_link.status)
                state["phase"] = "done"
                return True
        elif ph == "playing":
            m = engines_link.master_status
            if m and int(now) != state.get("last_s"):
                state["last_s"] = int(now)
                state["stats"].append({"t": now - state["t0"], "target": m.get("target"),
                                       "underruns": m.get("underruns"), "lane": m.get("lane_underruns"),
                                       "late": m.get("late"),
                                       "loads": [round(x, 3) if x else x for x in engines_link.loads()]})
            if now - state["t0"] >= a.seconds:
                state["phase"] = "done"
                finish()
                return True
        return False

    try:
        if a.no_ui:
            import engines as link

            link.runner = runner
            e = link.Engines(a.count, model.new_project())
            while True:
                e.poll()
                if orchestrate(e):
                    break
                time.sleep(1 / 30)
        else:
            run_ui(a, runner, orchestrate, state)
    finally:
        midi_stop.set()
        runner.stop()


def run_ui(a, runner, orchestrate, state):
    """The shared Ostinato UI (app/ui), in a native window, as the browser's UI worker runs it."""
    import asyncio
    import time
    import types

    if a.width and a.height:
        os.environ["PYDEVICES_WIDTH"] = str(a.width)
        os.environ["PYDEVICES_HEIGHT"] = str(a.height)
        os.environ["PYDEVICES_SCALE"] = "1"
    else:
        os.environ["PYDEVICES_MAXIMIZED"] = "1"
    os.environ["ENGINES"] = str(a.count)
    import engines as link
    import page

    link.runner = runner
    page.config["fresh"] = True              # no autosave, no first-run guide
    page.config["data"] = os.path.join(os.path.abspath(a.out), "data")
    # The shared UI without its last lines (which build it and run the loop):
    # this harness adds its own timer before the loop runs.
    path = os.path.join(ROOT, "app", "ui", "main.py")
    src = open(path).read()
    cut = src.index("\npage.run(start())")
    mod = types.ModuleType("ostinato_ui")
    mod.__file__ = path
    sys.modules["ostinato_ui"] = mod
    exec(compile(src[: cut + 1], path, "exec"), mod.__dict__)
    asyncio.run(mod.start())
    state["window"] = {"width": mod.W, "height": mod.H}
    print("LVGL %dx%d at scale 1" % (mod.W, mod.H), flush=True)
    ui = mod.ostinato
    import display_driver
    import lvgl as lv

    last = [time.perf_counter()]

    scr = lv.screen_active()

    disp = lv.display_get_default()

    def tick(_t):
        if a.full_redraw:
            # The whole screen, drawn now and timed: a full redraw 30 times a second.
            scr.invalidate()
            t0 = time.perf_counter()
            lv.refr_now(disp)
            state["frames"].append(round((time.perf_counter() - t0) * 1000, 2))
        else:
            loop = display_driver.event_loop.current_instance()
            if loop is not None:
                state["frames"].append(round(loop.last_pass_ms, 2))
        if orchestrate(ui.engines, ui):
            mod.app_host.request_quit()

    keep = lv.timer_create(tick, 33, None)
    state["keep"] = keep
    mod.app_host.run()


if __name__ == "__main__":
    main()
