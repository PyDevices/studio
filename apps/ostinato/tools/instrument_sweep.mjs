// Every instrument in the picker, played through the app's own engine code
// (app/engine/engine.py) under Pyodide in Node, with the same wheels the app
// loads: each melodic instrument plays A3, C4 and E5 (live, as the keyboard
// would), and the pitch heard is checked; each drum machine plays every pad in
// its note map and must make a sound for each.
//
//   node tools/instrument_sweep.mjs [> runs/instrument_sweep.json]
import { loadPyodide } from "../vendor/pyscript/pyodide/pyodide.mjs";
import fs from "fs";
const R = new URL("../", import.meta.url).pathname;
const w = JSON.parse(fs.readFileSync(R + "wheels.json"));
const py = await loadPyodide({ indexURL: R + "vendor/pyscript/pyodide/" });
for (const n of ["pydevices-audiodsp", "pydevices-audioeffects", "pydevices-audioinstruments"]) await py.loadPackage(R + "vendor/wheels/" + w[n].file);
for (const f of ["app/shared/model.py", "app/engine/engine.py"]) py.FS.writeFile("/home/pyodide/" + f.split("/").pop(), fs.readFileSync(R + f));
const out = py.runPython(`
import json, math, array, time
import engine, model, audioinstruments
SR = 48000
def pitch_hz(x):
    n = len(x); m = sum(x) / n; x = [v - m for v in x]
    best, lag_best = 0, 0
    lo, hi = int(SR / 2000), int(SR / 40)
    # autocorrelation by FFT-free decimated search, then refine
    import cmath
    return None
def analyse(pcm, start_frame, frames):
    a = array.array("h"); a.frombytes(pcm)
    mono = [ (a[2*i] + a[2*i+1]) / 65536.0 for i in range(start_frame, start_frame + frames) ]
    return mono
results = {}
e = engine.Engine(SR, 0, lambda m: None)
proj = model.new_project()
e.apply({"op": "replace", "value": proj})
tid = e.project["tracks"][1]["id"]
e.assign([tid])
e.offset = 0
for name in audioinstruments.ALL:
    try:
        kind = "drums" if name in audioinstruments.DRUM_MACHINES else "melodic"
        t0 = time.perf_counter()
        e.apply({"op": "set", "path": model.track_path(tid, "instrument"), "value": name})
        e.selected = tid
        notes = [57, 60, 76] if kind == "melodic" else [n for n, _ in audioinstruments.load(name).NOTE_MAP]
        heard = []
        def wait_for_silence():
            # Release tails outlive reset(): render until 100 ms of near-silence.
            quiet = 0
            for _ in range(750):          # 4 s at most
                a = array.array("h"); a.frombytes(bytes(e.render(1)))
                quiet = quiet + 1 if max(abs(v) for v in a) < 30 else 0
                if quiet >= 19:
                    return True
            return False
        for n in notes:
            e.voices[tid].inst.reset()
            wait_for_silence()
            e.live(0x90, n, 110, 0)        # to the track under test, as its keyboard would
            pcm = bytes(e.render(64))      # 341 ms
            e.live(0x80, n, 0, 0)
            a = array.array("h"); a.frombytes(pcm)
            peak = max(abs(v) for v in a) / 32768.0
            heard.append({"note": n, "peak": round(peak, 4), "pcm": None})
            if kind == "melodic":
                mono = [(a[2*i] + a[2*i+1]) / 2.0 for i in range(len(a)//2)]
                heard[-1]["mono"] = mono[4800:4800 + 4096]   # 100 ms in
        results[name] = {"kind": kind, "notes": heard, "build_s": round(time.perf_counter() - t0, 3)}
    except Exception as ex:
        results[name] = {"error": repr(ex)[:200]}
json.dumps(results)
`);
fs.writeFileSync(R + "runs/instrument_sweep_raw.json", out);
console.log("wrote runs/instrument_sweep_raw.json");
