// P0 spike host: wires the page to the engine worker and the AudioWorklet,
// relays Web MIDI to the engine, and exposes window.__ostinato for tests.
//
//   page (this file + PyScript/LVGL) --midi port--> engine worker (Pyodide)
//   engine worker --audio port (Int16 blocks)--> AudioWorklet --> speakers
//
// Nothing here runs Python: a MIDI message goes from Web MIDI's callback
// straight onto the engine's port.

const BASE = new URL("../", import.meta.url).href;

const state = {
  ctx: null, node: null, worker: null, midiOut: null,
  ready: null, midiLog: [], tapChunks: [], lag: [], inputs: [],
  workerStats: null, workletStats: null,
};

function b64(u8) {
  let s = "";
  for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000));
  return btoa(s);
}

function lagMonitor() {
  // How late the page's event loop runs a 5 ms timer: a MIDI message
  // waits exactly this long behind whatever the page is doing.
  let expected = performance.now() + 5;
  setInterval(() => {
    const now = performance.now();
    state.lag.push(now - expected);
    if (state.lag.length > 200000) state.lag.splice(0, 100000);
    expected = now + 5;
  }, 5);
}

function onMidi(ev) {
  const now = performance.now();
  const ctx = state.ctx;
  // Context time when the message really happened (ev.timeStamp), not when
  // the page got round to it.
  const at = ctx.currentTime - (now - ev.timeStamp) / 1000;
  state.midiOut.postMessage({ d: ev.data, at });
  state.midiLog.push({ d: Array.from(ev.data), stamp: ev.timeStamp, dispatched: now, ctxAt: at });
}

async function connectMidi() {
  if (!navigator.requestMIDIAccess) return [];
  const access = await navigator.requestMIDIAccess({ sysex: false });
  const attach = () => {
    state.inputs = [];
    for (const input of access.inputs.values()) {
      input.onmidimessage = onMidi;
      state.inputs.push(input.name);
    }
  };
  attach();
  access.onstatechange = attach;
  return state.inputs;
}

async function start(opts = {}) {
  const target = opts.target || 768;
  const ctx = new AudioContext({ latencyHint: "interactive", sampleRate: opts.sampleRate || 48000 });
  state.ctx = ctx;
  await ctx.audioWorklet.addModule(new URL("engine/worklet.js", BASE));
  const node = new AudioWorkletNode(ctx, "ostinato-out", {
    numberOfInputs: 0, outputChannelCount: [1], processorOptions: { target, plant: opts.plant || "" },
  });
  node.connect(ctx.destination);
  state.node = node;
  node.port.onmessage = (e) => {
    const m = e.data;
    if (m.type === "tap") {
      state.tapChunks.push([m.start, b64(new Uint8Array(m.pcm.buffer))]);
    } else if (m.type === "stats") state.workletStats = m;
  };

  const useRing = opts.transport === "sab";
  if (useRing && !self.crossOriginIsolated) throw new Error("the SAB transport needs a cross-origin isolated page (serve.py --isolate)");
  const sab = useRing ? new SharedArrayBuffer(8 + 2 * 16384) : null;
  const worker = new Worker(new URL("engine/worker.js", BASE), { type: "module" });
  state.worker = worker;
  const audio = new MessageChannel();
  const midi = new MessageChannel();
  state.midiOut = midi.port2;
  const ready = new Promise((resolve, reject) => {
    worker.onmessage = (e) => {
      const m = e.data;
      if (m.type === "ready") resolve(m);
      else if (m.type === "error") reject(new Error(m.error));
      else if (m.type === "stats") state.workerStats = m;
    };
  });
  worker.postMessage({
    type: "init", base: BASE, sampleRate: ctx.sampleRate,
    voice: opts.voice || "mono_synth", parse: opts.parse || "adafruit", plant: opts.plant || "", leadMs: opts.leadMs || 0,
    wheels: opts.wheels, libs: opts.libs, sab, target,
  }, [audio.port1, midi.port1]);
  const info = await ready;
  if (useRing) node.port.postMessage({ type: "ring", sab });
  else node.port.postMessage({ type: "engine" }, [audio.port2]);
  if (ctx.state !== "running") await ctx.resume();
  const inputs = await connectMidi();
  lagMonitor();
  return { ...info, inputs, sampleRate: ctx.sampleRate,
    baseLatency: ctx.baseLatency, outputLatency: ctx.outputLatency };
}

async function stats() {
  state.worker.postMessage({ type: "stats" });
  state.node.port.postMessage({ type: "stats" });
  await new Promise((r) => setTimeout(r, 50));
  return { worker: state.workerStats, worklet: state.workletStats,
    outputLatency: state.ctx.outputLatency, baseLatency: state.ctx.baseLatency };
}

function takeLag() {
  const got = state.lag;
  state.lag = [];
  return got;
}

function takeTap() {
  return state.tapChunks.splice(0);
}

function takeMidiLog() {
  const got = state.midiLog;
  state.midiLog = [];
  return got;
}

window.__ostinato = {
  start, stats, takeLag, takeMidiLog, takeTap,
  tap: (on) => state.node.port.postMessage({ type: "tap", on }),
  setTarget: (frames) => state.node.port.postMessage({ type: "target", frames }),
  get inputs() { return state.inputs; },
};
