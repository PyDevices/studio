// A Web Worker that runs Python (Pyodide) for a page built with host.js.
//
// The page sends one "init" message: what to install, which files to put in
// the worker's file system, which module to run, and any MessagePorts and an
// OffscreenCanvas to hand over. Python sees them as `js.ports.<name>` and
// `js.display_canvas` (board_configs/workerdisplay reads the latter).
// Pointer, wheel and key events the page relays go to
// displaydev.workerdisplay once the app has imported it. Messages of type
// "app" go to the Python function set as js.self.onPageMessage.

import { loadPyodide } from "../vendor/pyscript/pyodide/pyodide.mjs";

self.ports = {};
let py = null;
let display = null;   // displaydev.workerdisplay, once imported
let modules = null;   // sys.modules, to notice that import
const early = [];     // input that arrived before the display existed

function relayInput(msg) {
  if (!display && modules && modules.has("displaydev.workerdisplay")) {
    display = modules.get("displaydev.workerdisplay");
  }
  if (!display) {
    if (early.length < 64) early.push(msg);
    return;
  }
  while (early.length) send(early.shift());
  send(msg);
}

function send(msg) {
  if (msg.type === "input") display.feed(py.toPy(msg.event));
  else if (msg.type === "resize") display.resize(msg.width, msg.height);
}

async function init(msg, ports) {
  const t0 = performance.now();
  const name = msg.name || "python";
  py = await loadPyodide({
    indexURL: new URL("../vendor/pyscript/pyodide/", import.meta.url).href,
    stdout: (s) => console.log(`[${name}] ${s}`),
    stderr: (s) => console.warn(`[${name}] ${s}`),
  });
  for (const pkg of msg.packages || []) await py.loadPackage(new URL(pkg, msg.base).href);
  for (const [src, dest] of Object.entries(msg.files || {})) {
    const resp = await fetch(new URL(src, msg.base));
    if (!resp.ok) throw new Error(`${src}: ${resp.status}`);
    const path = "/home/pyodide/" + dest;
    py.FS.mkdirTree(path.slice(0, path.lastIndexOf("/")) || "/home/pyodide");
    py.FS.writeFile(path, new Uint8Array(await resp.arrayBuffer()));
  }
  if (msg.env) {
    const env = py.toPy(msg.env);
    py.pyimport("os").environ.update(env);
    env.destroy();
  }
  // MessagePorts arrive in event.ports, in the order of msg.portNames.
  for (const [i, portName] of (msg.portNames || []).entries()) self.ports[portName] = ports[i];
  if (msg.canvas) self.display_canvas = msg.canvas;
  modules = py.pyimport("sys").modules;
  const loaded = performance.now();
  self.postMessage({ type: "loaded", ms: loaded - t0 });
  if (msg.main) py.pyimport(msg.main);
  self.postMessage({ type: "ready", ms: performance.now() - t0, loadMs: loaded - t0 });
}

self.onmessage = (e) => {
  const msg = e.data;
  if (msg.type === "init") {
    init(msg, e.ports)
      .catch((err) => self.postMessage({ type: "error", error: String((err && err.stack) || err) }));
  } else if (msg.type === "input" || msg.type === "resize") {
    relayInput(msg);
  } else if (msg.type === "app" && typeof self.onPageMessage === "function") {
    // Anything else the page sends for the app: Python sets
    // js.self.onPageMessage to receive it, and answers with
    // js.self.postMessage({type: "app", ...}), which arrives in the page's
    // handle.onmessage.
    self.onPageMessage(msg);
  }
};
