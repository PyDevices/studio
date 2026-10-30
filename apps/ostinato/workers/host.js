// Page side of a Python app that runs in Web Workers.
//
// The page keeps only what must stay on the main thread: the canvas element
// and its input events, Web MIDI, and the AudioContext. Everything Python
// runs in workers (python_worker.js), so a long redraw or a garbage
// collection never delays a MIDI message or the audio.
//
//   import { createAudio, createMidiRelay, startWorker, resumeOnGesture } from "./workers/host.js";
//
//   const audio = await createAudio({ channels: 1 });      // suspended until a gesture
//   const midi = createMidiRelay();
//   const ui = startWorker({
//     name: "ui", main: "ui_main", canvas: document.getElementById("display_canvas"),
//     packages: ["vendor/wheels/pydevices-....whl", "vendor/wheels/pydevices_lvgl-....whl"],
//     files: { "app/ui_main.py": "ui_main.py" },
//     ports: { midi: midi.port() },
//   });
//   const engine = startWorker({
//     name: "engine", main: "engine_main",
//     packages: [...], files: { "app/engine_main.py": "engine_main.py" },
//     ports: { audio: audio.workerPort, midi: midi.port() },
//   });
//   await Promise.all([ui.ready, engine.ready]);
//   resumeOnGesture(audio.context);
//
// In Python: `from js import ports` (ports.audio for audiodev.worklet_audio,
// ports.midi for midiports.webmidi.relay_ports), and board_configs/workerdisplay
// for the canvas.

const BASE = new URL("../", import.meta.url).href;

// -- Python workers -----------------------------------------------------------

export function startWorker({ name = "python", main, packages = [], files = {}, env = {}, canvas = null, ports = {} }) {
  const worker = new Worker(new URL("./python_worker.js", import.meta.url), { type: "module", name });
  const portNames = Object.keys(ports);
  const transfer = portNames.map((k) => ports[k]);
  let offscreen = null;
  if (canvas) {
    const rect = canvas.getBoundingClientRect();
    if (!canvas.width || !canvas.height) {
      canvas.width = Math.round(rect.width);
      canvas.height = Math.round(rect.height);
    }
    offscreen = canvas.transferControlToOffscreen();
    transfer.push(offscreen);
  }
  const handle = { worker, name, loaded: null, ready: null, error: null, onmessage: null,
    send: (msg, transfer) => worker.postMessage({ type: "app", ...msg }, transfer || []) };
  handle.loaded = new Promise((resolveLoaded) => {
    handle.ready = new Promise((resolve, reject) => {
      worker.onmessage = (e) => {
        const m = e.data;
        if (m.type === "loaded") resolveLoaded(m);
        else if (m.type === "ready") resolve(m);
        else if (m.type === "error") { handle.error = m.error; console.error(`[${name}] ${m.error}`); reject(new Error(m.error)); }
        else if (handle.onmessage) handle.onmessage(m);   // e.g. {type: "app", ...} from Python
      };
      worker.onerror = (e) => { handle.error = e.message; reject(new Error(e.message || "worker failed to load")); };
    });
  });
  worker.postMessage({ type: "init", name, base: BASE, main, packages, files, env, portNames, canvas: offscreen }, transfer);
  if (canvas) relayInput(canvas, worker);
  return handle;
}

// Pointer, wheel and key events on the canvas go to the worker, in the
// format displaydev.workerdisplay reads. The page prevents the browser
// defaults a worker can't (scrolling, the context menu).
const SCROLL_KEYS = new Set([" ", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "PageUp", "PageDown", "Home", "End", "Tab"]);

export function relayInput(canvas, worker) {
  canvas.tabIndex = 0;
  canvas.style.touchAction = "none";
  const send = (e) => {
    worker.postMessage({ type: "input", event: {
      type: e.type, x: e.offsetX, y: e.offsetY, movementX: e.movementX || 0, movementY: e.movementY || 0,
      button: e.button || 0, buttons: e.buttons || 0, pointerType: e.pointerType || "mouse", pointerId: e.pointerId || 0,
      deltaX: e.deltaX || 0, deltaY: e.deltaY || 0, key: e.key || "", location: e.location || 0, repeat: !!e.repeat,
      ctrlKey: !!e.ctrlKey, shiftKey: !!e.shiftKey, altKey: !!e.altKey, metaKey: !!e.metaKey,
    } });
  };
  canvas.addEventListener("pointerdown", (e) => { canvas.focus(); canvas.setPointerCapture(e.pointerId); send(e); });
  for (const t of ["pointerup", "pointercancel", "pointermove"]) canvas.addEventListener(t, send);
  canvas.addEventListener("wheel", (e) => { e.preventDefault(); send(e); }, { passive: false });
  canvas.addEventListener("contextmenu", (e) => e.preventDefault());
  for (const t of ["keydown", "keyup"]) {
    canvas.addEventListener(t, (e) => { if (SCROLL_KEYS.has(e.key)) e.preventDefault(); send(e); });
  }
  const resized = () => {
    const r = canvas.getBoundingClientRect();
    worker.postMessage({ type: "resize", width: r.width, height: r.height });
  };
  new ResizeObserver(resized).observe(canvas);
  resized();
  canvas.focus();
}

// -- Audio ----------------------------------------------------------------------

// An AudioContext and the worklet that plays workers' blocks. Hand
// `workerPort` to the audio worker (ports: {audio: ...}); for more workers
// rendering in parallel, give each its own `addStream()` port and the worklet
// sums them.
export async function createAudio({ channels = 1, sampleRate = 48000, minFrames = 768, startFrames = 1536, maxFrames = 4096 } = {}) {
  const context = new AudioContext({ latencyHint: "interactive", sampleRate });
  await context.audioWorklet.addModule(new URL("./audio_worklet.js", import.meta.url));
  const node = new AudioWorkletNode(context, "python-worker-output", {
    numberOfInputs: 0, outputChannelCount: [channels],
    processorOptions: { channels, minFrames, startFrames, maxFrames },
  });
  node.connect(context.destination);
  // An exception in the worklet stops it for good, silently: say so.
  node.onprocessorerror = (e) => console.error("audio worklet failed:", e.message || e);
  const addStream = () => {
    const channel = new MessageChannel();
    node.port.postMessage({ type: "worker" }, [channel.port2]);
    return channel.port1;
  };
  let statsWaiter = null;
  const tapChunks = [];
  node.port.onmessage = (e) => {
    if (e.data.type === "stats" && statsWaiter) { statsWaiter(e.data); statsWaiter = null; }
    else if (e.data.type === "tap") tapChunks.push(e.data);
  };
  return {
    context, node, workerPort: addStream(), addStream, tapChunks,
    setGain: (gain) => node.port.postMessage({ type: "master", gain }),
    stats: () => new Promise((r) => { statsWaiter = r; node.port.postMessage({ type: "stats" }); }),
    tap: (on) => node.port.postMessage({ type: "tap", on }),
  };
}

// Browsers start audio only after the user touches the page.
export function resumeOnGesture(context, target = window) {
  if (context.state === "running") return;
  const resume = () => context.resume();
  for (const t of ["pointerdown", "keydown"]) target.addEventListener(t, resume, { once: true, capture: true });
}

// -- Web MIDI -------------------------------------------------------------------

// Web MIDI exists only on the page. Each call to port() makes a MessagePort
// for one worker: every input's messages go to every port as
// {data: Uint8Array, timeStamp}, and {data} a worker sends goes to the
// outputs (midiports.webmidi.relay_ports reads and writes this format).
export function createMidiRelay({ sysex = false } = {}) {
  const ports = [];
  const relay = { access: null, inputs: [], outputs: [], error: null, onchange: null };
  const forward = (e) => {
    const msg = { data: e.data, timeStamp: e.timeStamp };
    for (const p of ports) p.postMessage(msg);
  };
  const attach = () => {
    relay.inputs = [];
    relay.outputs = [];
    for (const input of relay.access.inputs.values()) {
      input.onmidimessage = forward;
      relay.inputs.push(input.name);
    }
    for (const output of relay.access.outputs.values()) relay.outputs.push(output);
    if (relay.onchange) relay.onchange(relay);
  };
  relay.ready = navigator.requestMIDIAccess
    ? navigator.requestMIDIAccess({ sysex }).then((access) => {
      relay.access = access;
      access.onstatechange = attach;
      attach();
      return relay;
    }).catch((err) => { relay.error = String(err); return relay; })
    : Promise.resolve(Object.assign(relay, { error: "Web MIDI isn't available in this browser" }));
  relay.port = () => {
    const ch = new MessageChannel();
    ch.port1.onmessage = (e) => {
      const data = e.data && e.data.data;
      if (data) for (const out of relay.outputs) out.send(data);
    };
    ports.push(ch.port1);
    return ch.port2;
  };
  return relay;
}
