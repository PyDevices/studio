"""Drive the real app in Chrome and record what it plays.

    .venv/bin/python tools/app_play.py --cdp http://127.0.0.1:9222 \
        --click metronome,play --seconds 300 --out runs/p2-metronome

Loads index.html, clicks the start gate, clicks the named on-screen controls
(at the positions the layout gives them at 1920x1080), records every frame
the AudioWorklet plays for --seconds, samples the worklet's stats every 10 s,
and writes tap.wav, run.json and a screenshot into --out. With --fake-midi
the fake DMK-25 keyboard (tools/fake_webmidi.js) is installed and
--scenario plays through it.
"""

import argparse
import base64
import json
import pathlib
import subprocess
import sys
import time

import numpy as np
import soundfile
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Centres of controls at 1920x1080 (scale 1), from app/ui/transport.py's layout.
CONTROLS = {
    "play": (203, 36), "stop": (277, 36), "rec": (351, 36), "metronome": (845, 36),
    "tempo-": (619, 36), "tempo+": (773, 36),
    "track1": (150, 150), "track2": (150, 242), "track3": (150, 334), "track4": (150, 426),
}

CLOCK_LOG = """
window.__clock = []; window.__midiLog = [];
(function poll() {
  const o = window.__ostinato;
  if (o && o.audio) window.__clock.push([performance.now(), o.audio.context.currentTime]);
  if (window.__clock.length > 200000) window.__clock.splice(0, 100000);
  setTimeout(poll, 20);
})();
const _rma = navigator.requestMIDIAccess && navigator.requestMIDIAccess.bind(navigator);
if (_rma) navigator.requestMIDIAccess = (opts) => _rma(opts).then((access) => {
  const tap = (input) => input.addEventListener("midimessage", (e) => {
    window.__midiLog.push({ d: Array.from(e.data), stamp: e.timeStamp });
  });
  for (const i of access.inputs.values()) tap(i);
  return access;
});
"""

PUSH_TAP = """() => {
  // Post each tap chunk to the test server as it arrives (raw float32).
  const a = window.__ostinato.audio;
  setInterval(() => {
    for (const c of a.tapChunks.splice(0)) {
      const send = (tries) => fetch(`/__tap?start=${c.start}&channels=${c.channels}`, { method: "POST", body: c.pcm.buffer })
        .then((r) => { if (!r.ok) throw new Error(r.status); })
        .catch(() => { if (tries > 0) setTimeout(() => send(tries - 1), 200); });
      send(5);
    }
  }, 500);
}"""

TAKE_TAP = """() => {
  const chunks = window.__ostinato.audio.tapChunks.splice(0);
  return chunks.map((c) => {
    const u8 = new Uint8Array(c.pcm.buffer);
    let s = "";
    for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
    return [c.start, c.channels, btoa(s)];
  });
}"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdp", default="")
    ap.add_argument("--port", type=int, default=8774)
    ap.add_argument("--click", default="", help="comma-separated controls to click, in order")
    ap.add_argument("--track", type=int, default=None, help="select this track (0-3) before playing")
    ap.add_argument("--instrument", default="", help="put this instrument on the selected track (Keys if none)")
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--engines", type=int, default=0)
    ap.add_argument("--minframes", type=int, default=0, help="the worklet queue's floor (the page's default if 0)")
    ap.add_argument("--chrome-arg", action="append", default=[], metavar="FLAG",
                    help="an extra flag for the browser it launches (repeatable), e.g. --disable-audio-output "
                         "for Chromium's 10 ms silent output instead of the system's")
    ap.add_argument("--fake-midi", action="store_true")
    ap.add_argument("--debug", action="store_true", help="engines print what live MIDI does")
    ap.add_argument("--real-midi", default="", metavar="PORT", help="send from Windows Python into this port")
    ap.add_argument("--scenario", default="", choices=("", "canon", "scale", "knob", "transport"),
                    help="play this through the keyboard (fake or real) after the clicks")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    import os

    for f in ("tap.raw", "tap.idx"):
        (out / f).unlink(missing_ok=True)
    server = subprocess.Popen([sys.executable, str(ROOT / "tools" / "serve.py"), "--port", str(a.port)],
                              stdout=subprocess.DEVNULL, env={**os.environ, "TAP_DIR": str(out)})
    time.sleep(1)
    tap, stats, logs = [], [], []
    try:
        with sync_playwright() as pw:
            browser = (pw.chromium.connect_over_cdp(a.cdp) if a.cdp
                       else pw.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"] + a.chrome_arg))
            # Over CDP, use the real window (no emulated viewport): a forced
            # 1920x1080 page overflows a window on a scaled display.
            ctx = (browser.new_context(no_viewport=True) if a.cdp
                   else browser.new_context(viewport={"width": a.width, "height": a.height}))
            if a.fake_midi:
                ctx.add_init_script(path=str(ROOT / "tools" / "fake_webmidi.js"))
            if a.real_midi:
                ctx.grant_permissions(["midi", "midi-sysex"], origin="http://127.0.0.1:%d" % a.port)
            # Pair performance.now() with the audio clock every 20 ms, and log
            # every MIDI message the page receives, so key-to-sound can be
            # measured against the tap afterwards (tools/tap_check.py).
            ctx.add_init_script(script=CLOCK_LOG)
            page = ctx.new_page()
            page.on("console", lambda m: logs.append(m.text[:300]))
            url = "http://127.0.0.1:%d/index.html" % a.port
            q = []
            if a.engines:
                q.append("engines=%d" % a.engines)
            if a.minframes:
                q.append("minframes=%d" % a.minframes)
            if a.debug:
                q.append("debug=1")
            if q:
                url += "?" + "&".join(q)
            t0 = time.time()
            page.goto(url)
            page.wait_for_function("window.__appReady || window.__appError", timeout=120_000)
            err = page.evaluate("window.__appError || ''")
            if err:
                raise SystemExit("app failed: " + err)
            ready_s = time.time() - t0
            vw, vh, dpr = page.evaluate("[innerWidth, innerHeight, devicePixelRatio]")
            # Controls are at design positions (1920x1080) times the layout's
            # scale, in canvas pixels; the mouse works in CSS pixels.
            scale = max(0.6, min(vw * dpr / 1920, vh * dpr / 1080)) / dpr
            page.mouse.click(vw // 2, vh // 2)      # the start gate
            time.sleep(3.0)
            page.evaluate(PUSH_TAP)
            page.evaluate("window.__ostinato.audio.tap(true)")
            if a.track is not None:
                page.evaluate("i => window.__ostinato.ui.send({kind: 'select', index: i})", a.track)
            if a.instrument:
                page.evaluate("([i, n]) => window.__ostinato.ui.send({kind: 'instrument', index: i, name: n})",
                              [2 if a.track is None else a.track, a.instrument])
                time.sleep(3)
                time.sleep(0.5)
            for name in [c for c in a.click.split(",") if c]:
                x, y = CONTROLS[name]
                page.mouse.click(x * scale, y * scale)
                time.sleep(0.4)
            sent = []
            if a.scenario:
                sys.path.insert(0, str(ROOT / "tools"))
                import midi_play

                canon = ROOT / "tools" / "scenarios" / "canon_in_d.yaml"
                if a.scenario == "canon":
                    events = midi_play.scenario_canon(a.seconds / 60.0, canon)
                elif a.scenario == "knob":
                    # Sweep the DMK-25's bank A knob 2 (CC 31) up, then down, while A3
                    # repeats every 250 ms. A macro reaches a note when it starts
                    #, so each new note carries the knob.
                    span = (a.seconds - 2) * 1000
                    events = []
                    for k in range(129):
                        events.append([800 + span / 2 * k / 128, [0xB0, 31, k]])
                    for k in range(128, -1, -1):
                        events.append([800 + span / 2 + span / 2 * (128 - k) / 128, [0xB0, 31, k]])
                    t = 900.0
                    while t < 800 + span:
                        events.append([t, [0x90, 57, 100]])
                        events.append([t + 200, [0x80, 57, 0]])
                        t += 250
                    events.sort(key=lambda e: e[0])
                elif a.scenario == "transport":
                    # The DMK-25's Play (CC 19) at 1 s, Stop (CC 18) at 6 s, Play again at 9 s, Stop at 12 s.
                    events = [[1000, [0xB0, 19, 127]], [1050, [0xB0, 19, 0]], [6000, [0xB0, 18, 127]],
                              [6050, [0xB0, 18, 0]], [9000, [0xB0, 19, 127]], [9050, [0xB0, 19, 0]],
                              [12000, [0xB0, 18, 127]], [12050, [0xB0, 18, 0]]]
                else:
                    events = midi_play.scenario_notes(a.seconds / 60.0)
                if a.real_midi:
                    stage = pathlib.Path("/mnt/c/temp/ostinato-midi")
                    (stage / "events.json").write_text(json.dumps(events))
                    (stage / "win_send.py").write_text((ROOT / "tools" / "win_send.py").read_text())
                    import shutil
                    sender = subprocess.Popen([shutil.which("python.exe") or "python.exe", "win_send.py",
                                               a.real_midi, "events.json", "sent.json"], cwd=str(stage),
                                              stdout=subprocess.DEVNULL)
                else:
                    page.evaluate("ev => window.__fakeMidi.play(ev)", events)
            # Record until the last note has had time to sound: a scenario
            # starts a second in and runs --seconds, so stopping at --seconds
            # would cut off its last notes, which the checks then count as
            # never heard.
            seconds = a.seconds
            if a.scenario:
                seconds = max(seconds, max((e[0] for e in events), default=0) / 1000 + 1.0)
            start = time.time()
            while time.time() - start < seconds:
                time.sleep(min(10.0, seconds - (time.time() - start)))
                st = page.evaluate("window.__ostinato.audio.stats()")
                st["t"] = time.time() - start
                stats.append(st)
                print("t=%4.0fs underruns=%d target=%d" % (st["t"], st["underruns"], st["target"]), flush=True)
            page.evaluate("window.__ostinato.audio.tap(false)")
            time.sleep(1.5)
            clock = page.evaluate("window.__clock")
            midi_log = page.evaluate("window.__midiLog")
            if a.scenario and a.fake_midi:
                sent = page.evaluate("window.__fakeMidi.takeSent()")
            if a.scenario and a.real_midi:
                sender.wait(timeout=60)
                sent = [{"stamp": None, "d": e["d"]} for e in
                        json.loads(pathlib.Path("/mnt/c/temp/ostinato-midi/sent.json").read_text())]
            page.screenshot(path=str(out / "screen.png"))
            ctx.close()
            if not a.cdp:
                browser.close()
    finally:
        server.terminate()
    raw = (out / "tap.raw").read_bytes() if (out / "tap.raw").exists() else b""
    for line in ((out / "tap.idx").read_text().split("\n") if (out / "tap.idx").exists() else []):
        if line.strip():
            s, ch, off, n = (int(v) for v in line.split())
            tap.append((s, ch, np.frombuffer(raw[off:off + n], dtype=np.float32)))
    tap.sort(key=lambda c: c[0])
    holes = []
    for (s0, c0, d0), (s1, _, _) in zip(tap, tap[1:]):
        if s0 + len(d0) // c0 < s1:
            holes.append([s0 + len(d0) // c0, s1])
    ch = tap[0][1] if tap else 2
    first = tap[0][0] if tap else 0
    frames = (tap[-1][0] - first + len(tap[-1][2]) // ch) if tap else 0
    pcm = np.zeros((frames, ch), dtype=np.float32)
    for s, c, data in tap:
        d = data.reshape(-1, c)
        pcm[s - first : s - first + len(d)] = d
    soundfile.write(out / "tap.wav", pcm, 48000, subtype="FLOAT")
    # Each received message's context time, from the clock pairs. The
    # context's currentTime moves in steps of one audio callback (8 to 11 ms
    # in Chrome on Windows) and holds between them, so a pair is behind the
    # audio clock by however long ago the last step was. The pairs taken just
    # after a step are on it, so the clock is where the pairs within 300 ms
    # of the message (about 30 of them) reach furthest ahead of
    # performance.now().
    if clock:
        cp = np.array([c[0] for c in clock])
        ahead = np.array([c[1] for c in clock]) - cp / 1000
        for m in midi_log:
            near = np.abs(cp - m["stamp"]) <= 300
            m["ctxAt"] = float(m["stamp"] / 1000 + (ahead[near].max() if near.any() else np.interp(m["stamp"], cp, ahead)))
    snapshots = [{"t": s_["t"], "worklet": {"underruns": s_["underruns"]}} for s_ in stats]
    (out / "run.json").write_text(json.dumps({"args": vars(a), "ready_s": ready_s, "tap_first_frame": first,
                                              "stats": stats, "logs": logs[-200:], "sent": sent, "tap_holes": holes,
                                              "midi_log": midi_log, "snapshots": snapshots,
                                              "info": {"outputLatency": 0, "baseLatency": 0},
                                              "lag_ms": [], "page_frame_ms": []}))
    print("wrote", out, "%.1f s of audio, ready in %.1f s" % (frames / 48000, ready_s))


if __name__ == "__main__":
    main()
