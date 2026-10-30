// P0: a worker that runs the LVGL stress screen on an OffscreenCanvas.
import { loadPyodide } from "../vendor/pyscript/pyodide/pyodide.mjs";

let ui = null;
let ctx = null;
let pyodide = null;

function blit(src, x, y, w, h) {
  const bytes = src.toJs ? src.toJs() : src;
  const img = ctx.createImageData(w, h);
  const out = new Uint32Array(img.data.buffer);
  for (let i = 0, j = 0, n = w * h; j < n; i += 2, j++) {
    const v = bytes[i] | (bytes[i + 1] << 8);
    const r = (v >> 8) & 0xF8, g = (v >> 3) & 0xFC, b = (v << 3) & 0xF8;
    out[j] = 0xFF000000 | ((b | (b >> 5)) << 16) | ((g | (g >> 6)) << 8) | (r | (r >> 5));
  }
  ctx.putImageData(img, x, y);
  if (src.destroy) src.destroy();
}

function loop() {
  const wait = ui.run();
  setTimeout(loop, wait);
}

self.onmessage = (e) => handle(e).catch((err) =>
  self.postMessage({ type: "error", error: String((err && err.stack) || err) }));

async function handle(e) {
  const m = e.data;
  if (m.type === "init") {
    ctx = m.canvas.getContext("2d");
    const py = await loadPyodide({ indexURL: new URL("../vendor/pyscript/pyodide/", import.meta.url).href });
    await py.loadPackage(new URL(m.lvglWheel, m.base).href);
    py.FS.writeFile("/home/pyodide/ui_worker.py", await (await fetch(new URL("./ui_worker.py", import.meta.url))).text());
    pyodide = py;
    ui = py.pyimport("ui_worker");
    ui.setup(m.width, m.height, blit);
    loop();
    self.postMessage({ type: "ready" });
  } else if (!ui) {
    return;            // still loading
  } else if (m.type === "stress") {
    ui.stress = m.level;
  } else if (m.type === "take") {
    const [frames, passes, ticks] = ui.take().toJs();
    self.postMessage({ type: "frames", frames, passes, ticks, heapMB: pyodide._module.HEAP8.buffer.byteLength / 1048576 });
  }
}
