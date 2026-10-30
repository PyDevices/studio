// Every effect in the picker, through the app's engine under Pyodide in Node:
// a TB-303 phrase rendered dry, with the effect on, and with it bypassed.
// "On" must differ from dry; "bypassed" must equal dry byte for byte.
//   node tools/effect_sweep.mjs
import { loadPyodide } from "../vendor/pyscript/pyodide/pyodide.mjs";
import fs from "fs";
const R = new URL("../", import.meta.url).pathname;
const w = JSON.parse(fs.readFileSync(R + "wheels.json"));
const py = await loadPyodide({ indexURL: R + "vendor/pyscript/pyodide/" });
for (const n of ["pydevices-audiodsp", "pydevices-audioeffects", "pydevices-audioinstruments"]) await py.loadPackage(R + "vendor/wheels/" + w[n].file);
for (const f of ["app/shared/model.py", "app/engine/engine.py"]) py.FS.writeFile("/home/pyodide/" + f.split("/").pop(), fs.readFileSync(R + f));
py.runPython(`
import hashlib, audioeffects, engine, model, array
SR = 48000
def take(rack):
    e = engine.Engine(SR, 0, lambda m: None)
    p = model.new_project()
    t = p["tracks"][1]
    t["rack"] = rack
    t["patterns"][0]["notes"] = [[0, 48, 45, 110], [96, 48, 52, 100], [192, 96, 57, 120]]
    e.apply({"op": "replace", "value": p})
    e.assign([t["id"]])
    e.offset = 0
    e.play(0, 0.0)
    pcm = b"".join(bytes(e.render(16)) for _ in range(30))   # 2.5 s
    return pcm
import audioinstruments
audioinstruments_src = audioinstruments.create("karplus", SR, channel_count=2)
dry = take([])
rows, bad = [], []
for name in audioeffects.ALL:
    try:
        on = take([{"name": name, "options": {}, "bypass": False, "macros": {}}])
        off = take([{"name": name, "options": {}, "bypass": True, "macros": {}}])
        a, b = array.array("h", on), array.array("h", dry)
        diff = sum(abs(x - y) for x, y in zip(a[::7], b[::7])) / (len(a) // 7) / 32768
        changed, transparent = on != dry, off == dry
        note = ""
        if not changed and name != "Rack":
            # Neutral by default (a flat EQ): push every knob to the top and listen again.
            n = len(type(audioeffects.create(name, audioinstruments_src.output, SR)).MACRO_LABELS)
            pushed = take([{"name": name, "options": {}, "bypass": False, "macros": {str(k): 127 for k in range(n)}}])
            changed = pushed != dry
            note = " (neutral by default; knobs at the top: %s)" % ("changes" if changed else "STILL NO CHANGE")
        rows.append("%-20s on: %-9s (mean diff %.4f)  bypassed: %s%s" % (name, "changes" if on != dry else "NO CHANGE", diff, "identical" if transparent else "DIFFERS", note))
        if not (changed and transparent) and name != "Rack": bad.append(name)
    except Exception as ex:
        rows.append("%-20s ERROR %s" % (name, repr(ex)[:80])); bad.append(name)
print("\\n".join(rows))
print("\\n%d effects, problems: %s" % (len(rows), bad))
`);
