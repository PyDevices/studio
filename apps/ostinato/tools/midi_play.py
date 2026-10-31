"""Play a scenario into Ostinato from the fake keyboard and record what came out.

    .venv/bin/python tools/midi_play.py --minutes 10 --stress 1 --out runs/p0-a

Starts the local server, opens the P0 spike page in headless Chromium with
the fake Web MIDI keyboard installed (tools/fake_webmidi.js), starts audio,
turns the tap on, sets the LVGL stress level, and plays the scenario in real
time. Writes to ``--out``:

    tap.wav     every frame the AudioWorklet played (float32 mono)
    run.json    what was sent, when the page saw it, what the engine
                applied, worklet/worker stats, page event-loop lag and
                LVGL frame times, load times

tools/tap_check.py reads both.
"""

import argparse
import base64
import json
import pathlib
import shutil
import subprocess
import sys
import time

import numpy as np
import soundfile
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
FAKE = ROOT / "tools" / "fake_webmidi.js"


def scenario_notes(minutes, period_ms=400, length_ms=150, clock=False):
    """A note every ``period_ms``, cycling up and down two octaves from C3."""
    events = []
    pitches = list(range(48, 73)) + list(range(71, 48, -1))
    t, i = 1000.0, 0
    end = minutes * 60_000 + 1000
    while t + length_ms < end:
        p = pitches[i % len(pitches)]
        vel = 60 + (i * 13) % 60
        events.append([t, [0x90, p, vel]])
        events.append([t + length_ms, [0x80, p, 0]])
        t += period_ms
        i += 1
    if clock:
        c = 1000.0 + 7.3   # 24 ppqn at 120 bpm, out of phase with the notes
        while c < end:
            events.append([c, [0xF8]])
            c += 500 / 24
    events.sort(key=lambda e: e[0])
    return events


SCALE = {"major": [0, 2, 4, 5, 7, 9, 11], "minor": [0, 2, 3, 5, 7, 8, 10]}
PITCH_CLASS = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5, "F#": 6, "Gb": 6,
               "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}


def canon_line(path, track="Violin I"):
    """One track of a reaper-composer song as [(beat, length, midi_note)].

    Same degree/octave rule as mpvst's composer (theory.py):
    12 + octave*12 + key + scale step.
    """
    import yaml
    doc = yaml.safe_load(pathlib.Path(path).read_text())
    key, steps = PITCH_CLASS[doc.get("key", "C")], SCALE[doc.get("scale", "major")]
    tr = next(t for t in doc["tracks"] if t["name"] == track)
    first_bar = min(p["bar"] for p in tr["play"])
    notes = []
    for place in tr["play"]:
        base = (place["bar"] - first_bar) * 4
        for rep in range(place.get("repeat", 1)):
            for beat, length, degree, octave in doc["patterns"][place["pattern"]]:
                d = degree - 1
                midi = 12 + (octave + d // 7) * 12 + key + steps[d % 7]
                notes.append((base + rep * 16 + beat, length, midi))
    return doc["tempo"], notes


def scenario_canon(minutes, path, staccato_ms=150, clock=False):
    """Violin I of the Canon in D, at the piece's tempo, every note cut to
    ``staccato_ms`` so each onset rises out of silence and can be timed."""
    tempo, line = canon_line(path)
    beat_ms = 60_000 / tempo
    span = max(b + l for b, l, _ in line) + 1
    events, t0, end, i = [], 1000.0, minutes * 60_000 + 1000, 0
    while True:
        for beat, length, midi in line:
            t = t0 + beat * beat_ms
            if t + staccato_ms >= end:
                events.sort(key=lambda e: e[0])
                return events
            vel = 70 + (i * 11) % 50
            events.append([t, [0x90, midi, vel]])
            events.append([t + min(staccato_ms, length * beat_ms * 0.8), [0x80, midi, 0]])
            i += 1
        t0 += span * beat_ms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=1)
    ap.add_argument("--stress", type=int, default=1)
    ap.add_argument("--target", type=int, default=768, help="frames the worklet keeps queued")
    ap.add_argument("--parse", default="adafruit", choices=("adafruit", "raw"))
    ap.add_argument("--voice", default="mono_synth")
    ap.add_argument("--clock", action="store_true", help="interleave MIDI clock with the notes")
    ap.add_argument("--scenario", default="canon", choices=("canon", "scale"),
                    help="canon: Violin I of tools/scenarios/canon_in_d.yaml, staccato; scale: two octaves up and down")
    ap.add_argument("--plant", default="", help="fault to plant (for proving tap_check): drop-block, delay, detune, lose-note")
    ap.add_argument("--no-tap", action="store_true")
    ap.add_argument("--real-midi", default="", metavar="PORT",
                    help="no fake keyboard: send from Windows Python into this Windows MIDI port "
                         "(e.g. 'Ostinato Loopback'); needs --cdp to a Windows Chrome")
    ap.add_argument("--page", default="index.html", help="spike page: index.html (LVGL on the page) or "
                    "ui_worker.html (LVGL in its own worker)")
    ap.add_argument("--lead", type=float, default=0, help="ms: play every message this long after its key press, "
                    "at the sample (0 = as soon as it arrives)")
    ap.add_argument("--transport", default="port", choices=("port", "sab"),
                    help="engine to worklet: transferred MessagePort blocks, or a SharedArrayBuffer ring")
    ap.add_argument("--cdp", default="", help="drive an already running Chrome (e.g. http://127.0.0.1:9222, "
                    "Windows Chrome from tools/win_chrome.sh) instead of headless Chromium")
    ap.add_argument("--out", required=True)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    a = ap.parse_args()

    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    server = subprocess.Popen([sys.executable, str(ROOT / "tools" / "serve.py"), "--port", str(a.port)]
                              + (["--isolate"] if a.transport == "sab" else []),
                              stdout=subprocess.DEVNULL)
    time.sleep(1.0)
    tap = []

    try:
        with sync_playwright() as pw:
            if a.cdp:
                browser = pw.chromium.connect_over_cdp(a.cdp)
            else:
                browser = pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
            ctx = browser.new_context(viewport={"width": a.width, "height": a.height})
            if a.real_midi:
                ctx.grant_permissions(["midi", "midi-sysex"], origin="http://127.0.0.1:%d" % a.port)
            else:
                ctx.add_init_script(path=str(FAKE))
            page = ctx.new_page()
            page.on("console", lambda m: print("[page]", m.text, flush=True) if m.type in ("error", "warning") else None)
            page.on("pageerror", lambda e: print("[pageerror]", e, flush=True))
            url = "http://127.0.0.1:%d/spike/%s" % (a.port, a.page)
            t0 = time.time()
            page.goto(url)
            page.wait_for_function("window.__pageReady === true || !!window.__pageError", timeout=90_000)
            if page.evaluate("window.__pageError || ''"):
                raise SystemExit("page failed: " + page.evaluate("window.__pageError"))
            page_ready_s = time.time() - t0
            print("page ready in %.1fs" % page_ready_s, flush=True)
            info = page.evaluate("""async ([target, parse, voice, plant, transport, leadMs]) => {
                const cfg = (await import('/spike/spike_config.js')).default;
                return await window.__ostinato.start({...cfg, target, parse, voice, plant, transport, leadMs});
            }""", [a.target, a.parse, a.voice, a.plant, a.transport, a.lead])
            print("engine ready:", json.dumps(info), flush=True)
            if not a.no_tap:
                page.evaluate("window.__ostinato.tap(true)")
            page.evaluate("s => { window.__stress = s; }", a.stress)
            time.sleep(1.0)

            if a.scenario == "canon":
                events = scenario_canon(a.minutes, ROOT / "tools" / "scenarios" / "canon_in_d.yaml")
            else:
                events = scenario_notes(a.minutes, clock=a.clock)
            sender = None
            if a.real_midi:
                inputs = page.evaluate("window.__ostinato.inputs")
                if not any(a.real_midi in n for n in inputs):
                    raise SystemExit("Chrome doesn't see %r (inputs: %s)" % (a.real_midi, inputs))
                stage = pathlib.Path("/mnt/c/temp/ostinato-midi")
                (stage / "events.json").write_text(json.dumps(events))
                (stage / "win_send.py").write_text((ROOT / "tools" / "win_send.py").read_text())
                sender = subprocess.Popen([shutil.which("python.exe") or "python.exe", "win_send.py", a.real_midi, "events.json", "sent.json"],
                                          cwd=str(stage), stdout=subprocess.PIPE, text=True)
            else:
                page.evaluate("ev => window.__fakeMidi.play(ev)", events)
            duration = events[-1][0] / 1000 + 1.5
            midi_log, sent, applied, lag, snapshots = [], [], [], [], []
            start = time.time()
            while True:
                left = duration - (time.time() - start)
                time.sleep(max(0.0, min(10.0, left)))
                for start_frame, b64 in page.evaluate("window.__ostinato.takeTap()"):
                    tap.append((int(start_frame), np.frombuffer(base64.b64decode(b64), dtype=np.float32)))
                st = page.evaluate("window.__ostinato.stats()")
                midi_log += page.evaluate("window.__ostinato.takeMidiLog()")
                if not a.real_midi:
                    sent += page.evaluate("window.__fakeMidi.takeSent()")
                lag += page.evaluate("window.__ostinato.takeLag()")
                applied += (st.get("worker") or {}).get("applied") or []
                snapshots.append({"t": time.time() - start, "worklet": st.get("worklet"),
                                  "worker": (st.get("worker") or {}).get("stats")})
                w = st.get("worklet") or {}
                print("t=%5.0fs underruns=%s minQueued=%s rt avg/max=%s/%s ms midi=%d" % (
                    time.time() - start, w.get("underruns"), w.get("minQueued"),
                    round(w["rtAvg"], 1) if w.get("rtAvg") else None, round(w.get("rtMax") or 0, 1), len(midi_log)), flush=True)
                if left <= 0:
                    break
            if sender:
                sender.wait(timeout=60)
                sent = [{"stamp": None, "d": e["d"]} for e in
                        json.loads(pathlib.Path("/mnt/c/temp/ostinato-midi/sent.json").read_text())]
            page.evaluate("window.__ostinato.tap(false)")
            time.sleep(0.6)
            for start_frame, b64 in page.evaluate("window.__ostinato.takeTap()"):
                tap.append((int(start_frame), np.frombuffer(base64.b64decode(b64), dtype=np.float32)))
            frame_ms = page.evaluate("window.__pageFrameMs || ''")
            lv_pass = page.evaluate("window.__lvPassMs || ''")
            ui_heap = page.evaluate("window.__uiHeapMB || null")
            ctx.close()
            if not a.cdp:
                browser.close()
    finally:
        server.terminate()

    tap.sort(key=lambda c: c[0])
    first = tap[0][0] if tap else 0
    total = (tap[-1][0] + len(tap[-1][1]) - first) if tap else 0
    pcm = np.zeros(total, dtype=np.float32)
    for s, chunk in tap:
        pcm[s - first : s - first + len(chunk)] = chunk
    soundfile.write(out / "tap.wav", pcm, int(info["sampleRate"]), subtype="FLOAT")
    run = {"args": vars(a), "info": info, "page_ready_s": page_ready_s, "tap_first_frame": first,
           "sent": sent, "midi_log": midi_log, "applied": applied, "snapshots": snapshots,
           "lag_ms": lag, "page_frame_ms": [float(x) for x in frame_ms.split(",") if x],
           "lv_pass_ms": [float(x) for x in lv_pass.split(",") if x], "ui_heap_mb": ui_heap}
    (out / "run.json").write_text(json.dumps(run))
    print("wrote", out, "tap seconds %.1f" % (total / info["sampleRate"]), flush=True)


if __name__ == "__main__":
    main()
