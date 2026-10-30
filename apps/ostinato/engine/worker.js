// The engine worker: a second Pyodide that owns all sound.
//
// The page sends one "init" message carrying two MessagePorts:
//   audio - to the AudioWorklet, which asks for blocks ({need}) and gets
//           Int16 PCM back, transferred, never copied;
//   midi  - from the page, carrying raw MIDI bytes as they arrive.
// Both are serviced on this thread's event loop, so a MIDI message is
// always fed to the engine before the next block is rendered.

import { loadPyodide } from "../vendor/pyscript/pyodide/pyodide.mjs";

let engine = null;
let pyodide = null;
let audioPort = null;
const stats = { renders: 0, blocks: 0, renderMs: 0, maxRenderMs: 0, midi: 0, late: 0 };

async function init(msg, ports) {
  const t0 = performance.now();
  const py = await loadPyodide({ indexURL: new URL("../vendor/pyscript/pyodide/", import.meta.url).href });
  pyodide = py;
  const tPy = performance.now();
  for (const w of msg.wheels) await py.loadPackage(new URL(w, msg.base).href);
  for (const f of msg.libs) {
    const text = await (await fetch(new URL("vendor/lib/" + f, msg.base))).text();
    const path = "/home/pyodide/" + f;
    py.FS.mkdirTree(path.slice(0, path.lastIndexOf("/")));
    py.FS.writeFile(path, text);
  }
  const src = await (await fetch(new URL("./engine.py", import.meta.url))).text();
  py.FS.writeFile("/home/pyodide/engine.py", src);
  const mod = py.pyimport("engine");
  engine = mod.Engine(msg.sampleRate, msg.voice, msg.parse, msg.plant || "", msg.leadMs || 0);
  const tReady = performance.now();

  audioPort = ports[0];
  const midiPort = ports[1];
  midiPort.onmessage = (e) => {
    engine.feed(e.data.d, e.data.at);
    stats.midi++;
  };
  audioPort.onmessage = (e) => {
    if (e.data.offset !== undefined) engine.set_offset(e.data.offset);
    if (e.data.need) render(e.data.need, e.data.asked);
  };
  if (msg.sab) ringLoop(msg.sab, msg.target);
  self.postMessage({ type: "ready", loadMs: { pyodide: tPy - t0, total: tReady - t0 } });
}

function render(blocks, asked) {
  const t = performance.now();
  const view = engine.render(blocks);
  const buf = view.getBuffer("i16");
  const pcm = buf.data.slice();
  buf.release();
  view.destroy();
  const dt = performance.now() - t;
  stats.renders++;
  stats.blocks += blocks;
  stats.renderMs += dt;
  if (dt > stats.maxRenderMs) stats.maxRenderMs = dt;
  audioPort.postMessage({ pcm, asked }, [pcm.buffer]);
}

// SharedArrayBuffer transport: keep `target` frames written ahead of what
// the worklet has read. Atomics.waitAsync leaves this thread's event loop
// free, so MIDI messages are still fed between renders.
async function ringLoop(sab, target) {
  const hdr = new Int32Array(sab, 0, 2);
  const ring = new Int16Array(sab, 8);
  const size = ring.length;
  for (;;) {
    const r = Atomics.load(hdr, 1);
    let w = Atomics.load(hdr, 0);
    const space = target - (w - r);
    if (space >= 256) {
      const blocks = Math.floor(space / 256);
      const t = performance.now();
      const view = engine.render(blocks);
      const buf = view.getBuffer("i16");
      const pcm = buf.data;
      for (let k = 0; k < pcm.length; k++) ring[(w + k) % size] = pcm[k];
      buf.release();
      view.destroy();
      const dt = performance.now() - t;
      stats.renders++; stats.blocks += blocks; stats.renderMs += dt;
      if (dt > stats.maxRenderMs) stats.maxRenderMs = dt;
      Atomics.store(hdr, 0, w + pcm.length);
      // Let queued MIDI messages run before deciding again.
      await new Promise((res) => setTimeout(res, 0));
      continue;
    }
    const res = Atomics.waitAsync(hdr, 1, r);
    if (res.async) await res.value;
  }
}

self.onmessage = (e) => {
  const msg = e.data;
  if (msg.type === "init") {
    init(msg, e.ports).catch((err) => self.postMessage({ type: "error", error: String(err && err.stack || err) }));
  } else if (msg.type === "stats") {
    const applied = engine ? engine.take_applied().toJs() : [];
    if (engine) stats.late = engine.late;
    if (pyodide) stats.heapMB = pyodide._module.HEAP8.buffer.byteLength / 1048576;
    self.postMessage({ type: "stats", stats: { ...stats }, applied });
  }
};
