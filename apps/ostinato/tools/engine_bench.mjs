// Render cost of the engine under Pyodide in Node (the browser's wasm, no browser):
//   node tools/engine_bench.mjs
// Prints % of real time for idle tracks and a cProfile of the render loop.
import { loadPyodide } from "../vendor/pyscript/pyodide/pyodide.mjs";
import fs from "fs";
const R = new URL("../", import.meta.url).pathname;
const w = JSON.parse(fs.readFileSync(R + "wheels.json"));
const py = await loadPyodide({ indexURL: R + "vendor/pyscript/pyodide/" });
for (const n of ["pydevices-audiodsp","pydevices-audioeffects","pydevices-audioinstruments"]) await py.loadPackage(R + "vendor/wheels/" + w[n].file);
// songlink (the song model) is in studio's lib/, two folders up.
py.FS.mkdirTree("/home/pyodide/songlink");
for (const f of ["__init__.py", "model.py"]) py.FS.writeFile("/home/pyodide/songlink/" + f, fs.readFileSync(R + "../../lib/songlink/" + f));
py.FS.writeFile("/home/pyodide/engine.py", fs.readFileSync(R + "app/engine/engine.py"));
py.runPython(`
import time, engine, cProfile, pstats, io
from songlink import model
e = engine.Engine(48000, 0, lambda m: None)
p = model.new_project()
e.apply({"op":"replace","value":p})
ids=[t["id"] for t in e.project["tracks"]]
def bench(label, ids_, blocks=400):
    e.assign(ids_)
    e.render(4)
    t=time.perf_counter()
    for _ in range(blocks//4): e.render(4)
    dt=time.perf_counter()-t
    print("%-22s %.0f%% of real time" % (label, dt/(blocks*256/48000)*100))
bench("idle, no tracks", [])
bench("idle, drums", ids[:1])
bench("idle, bass", ids[1:2])
bench("idle, keys", ids[2:3])
bench("idle, all four", ids)
e.assign([])
pr=cProfile.Profile(); pr.enable()
for _ in range(100): e.render(4)
pr.disable(); s=io.StringIO(); pstats.Stats(pr,stream=s).sort_stats("cumtime").print_stats(8); print(s.getvalue()[:1800])
`);
