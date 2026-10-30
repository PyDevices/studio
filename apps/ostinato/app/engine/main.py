"""Entry point of an engine worker.

Ports from the page (workers/host.js): ``audio`` to the AudioWorklet,
``midi`` from the Web MIDI relay, ``ui`` to the UI worker. ENGINE_INDEX and
PAGE_TIME_ORIGIN come in the environment.
"""

import os

import adafruit_midi

# adafruit_midi parses only the message types whose classes have been
# imported (importing registers them); anything else comes back as an
# unknown event with no status byte.
from adafruit_midi.control_change import ControlChange  # noqa: F401
from adafruit_midi.note_off import NoteOff  # noqa: F401
from adafruit_midi.note_on import NoteOn  # noqa: F401
from adafruit_midi.pitch_bend import PitchBend  # noqa: F401
from adafruit_midi.channel_pressure import ChannelPressure  # noqa: F401
from js import Object, performance, ports
from pyodide.ffi import create_proxy, to_js

from audiodev.worklet_audio import WorkletOutput
from midiports import webmidi

from songlink import model  # noqa: F401
from enginehost import EngineHost, drain_midi as _drain

SR = 48000
INDEX = int(os.environ.get("ENGINE_INDEX", "0"))
PAGE_ORIGIN = float(os.environ.get("PAGE_TIME_ORIGIN", "0"))
WORKER_ORIGIN = float(performance.timeOrigin)


def post(msg):
    msg.setdefault("engine", INDEX)
    ports.ui.postMessage(to_js(msg, dict_converter=Object.fromEntries))


def pause(on):
    ports.audio.postMessage(to_js({"pause": bool(on)}, dict_converter=Object.fromEntries))


def go_live():
    if ready[0]:
        return
    ready[0] = True
    while held:
        out.handle(held.pop(0))
    engine.streaming = True


host = EngineHost(SR, INDEX, post, pause, on_assign=lambda: go_live())
engine = host.engine
out = WorkletOutput(ports.audio, channels=2, sample_rate=SR, listen=False)
midi_in, midi_out = webmidi.relay_ports(ports.midi)
if os.environ.get("OSTINATO_DEBUG"):
    def _raw(event):
        print("engine %d port got" % INDEX, event.data.data.to_py() if event.data.data else None, "any", midi_in.any())
    ports.midi.addEventListener("message", create_proxy(_raw))
midi = adafruit_midi.MIDI(midi_in=midi_in, midi_out=midi_out, in_buf_size=64)


def key_frame(stamp):
    """Context frame a MIDI message happened at, from the page's timestamp
    (midiports' whole microseconds on the page's performance.now() clock)."""
    if stamp is None:
        return engine.asked
    now_page_ms = WORKER_ORIGIN + float(performance.now()) - PAGE_ORIGIN
    ago = max(0.0, now_page_ms - float(stamp) / 1000.0) / 1000.0
    return engine.asked - int(ago * SR)


_errors = [0]


def drain_midi():
    try:
        _drain(midi, midi_in, host, key_frame)
    except Exception as e:
        # The render path must never fail silently: say so, a few times.
        _errors[0] += 1
        if _errors[0] <= 5:
            import traceback
            print("engine %d: MIDI error: %s" % (INDEX, "".join(traceback.format_exception(e))))


def source(blocks):
    return host.render(blocks, drain_midi, out.offset)


out.source = source


# Until this worker has built its first tracks, audio requests wait here:
# building an instrument blocks the worker for longer than the worklet's
# queue lasts, so answering early would only buy a burst of dropouts.
held = []
ready = [False]


def on_audio(event):
    data = event.data
    asked = getattr(data, "asked", None)
    if asked is not None:
        engine.asked = int(asked)
    if not ready[0] and getattr(data, "need", None) is not None:
        held.append(data)
        return
    out.handle(data)


ports.audio.onmessage = create_proxy(on_audio)


def _heap_mb():
    import pyodide_js

    return round(pyodide_js._module.HEAP8.length / 1048576, 1)


def _status_extra():
    return {"underruns": out.underruns, "heap_mb": _heap_mb(), "time": float(performance.now())}


def on_ui(event):
    host.handle(event.data.to_py(), _status_extra)


ports.ui.onmessage = create_proxy(on_ui)


def on_page(msg):
    """Messages from the page (workers/host.js handle.send): file export."""
    data = msg.to_py() if hasattr(msg, "to_py") else msg
    if data.get("kind") == "render":
        from js import Int16Array, self as worker

        def send(seq, pcm):
            arr = Int16Array.new(len(pcm) // 2)
            arr.assign(memoryview(pcm).cast("h"))
            worker.postMessage(to_js({"type": "app", "kind": "pcm", "engine": INDEX, "seq": seq, "pcm": arr},
                                     dict_converter=Object.fromEntries), to_js([arr.buffer]))

        n = engine.render_offline(int(data["frames"]), int(data.get("chunk", 96000)), send)
        worker.postMessage(to_js({"type": "app", "kind": "rendered", "engine": INDEX, "chunks": n},
                                 dict_converter=Object.fromEntries))


from js import self as _worker  # noqa: E402

_worker.onPageMessage = create_proxy(on_page)
post({"t": "hello", "sample_rate": SR})
print("engine %d ready" % INDEX)
