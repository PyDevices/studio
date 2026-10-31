// Write assets/catalog.json: every instrument and effect in the pinned wheels,
// with what the UI shows (kind, macro labels and modes, patch names, drum
// note names). Run after scripts/fetch_wheels.py:
//
//   node scripts/make_catalog.mjs
//
// It uses the vendored Pyodide and the same wheels the app loads, so the UI
// never needs the audio stack to know what's in it.
import { loadPyodide } from "../vendor/pyscript/pyodide/pyodide.mjs";
import fs from "fs";
const root = new URL("../", import.meta.url).pathname;
const wheels = JSON.parse(fs.readFileSync(root + "wheels.json", "utf8"));
const py = await loadPyodide({ indexURL: root + "vendor/pyscript/pyodide/" });
for (const n of ["numpy", "omnipython-audiodsp", "omnipython-audioeffects", "omnipython-audioinstruments"]) {
  await py.loadPackage(root + "vendor/wheels/" + wheels[n].file);
}
const out = py.runPython(`
import json, array, audiocore, audioinstruments, audioeffects
SR = 48000
def audible_pads(name, patch, notes):
    """The pads that make a sound for this patch (a multi-kit's map lists pads
    its chosen machine doesn't have)."""
    i = audioinstruments.create(name, SR, channel_count=2)
    i.program_change(patch)
    out = []
    for n in notes:
        i.note_on(n, 127)
        peak = 0
        for _ in range(47):            # 250 ms
            a = array.array("h"); a.frombytes(bytes(audiocore.get_buffer(i.output)[1]))
            peak = max(peak, max(abs(v) for v in a))
        i.note_off(n)
        if peak >= 60:
            out.append(n)
        for _ in range(20):
            audiocore.get_buffer(i.output)
    i.deinit()
    return out
def labels(comp, mod):
    cls = type(comp)
    ml = [pretty(l) for l in (getattr(comp, "macro_labels", None) or getattr(cls, "MACRO_LABELS", ()) or getattr(mod, "MACRO_LABELS", ()) or ())]
    modes = getattr(cls, "MACRO_MODES", None) or getattr(mod, "MACRO_MODES", {}) or {}
    return ml, [modes.get(i, "UNIPOLAR") for i in range(len(ml))]
inst = {}
def pretty(label):
    """'vcf_freq' as 'VCF Freq': the rack names its knobs in snake case."""
    keep = {"vcf", "vco", "vca", "lfo", "dco", "hpf", "lpf", "adsr", "eg", "env", "bd", "sd", "oh", "cy", "hh", "pwm",
            "fm", "pm", "mg", "fx", "iv", "db", "bpm", "sub", "osc"}
    if "_" not in label and not label.islower():
        return label
    import re
    label = re.sub(r"^db(\\d+|_iv)$", lambda m: "drawbar_" + (m.group(1).replace("_", "") or ""), label)
    words = label.split("_")
    return " ".join(w.upper() if w in keep and w not in ("sub", "osc", "env") else w.capitalize() for w in words)
drums = set(audioinstruments.DRUM_MACHINES)
for name in audioinstruments.ALL:
    mod = audioinstruments.load(name)
    comp = audioinstruments.create(name, SR, channel_count=2)
    ml, modes = labels(comp, mod)
    patches = getattr(mod, "PATCHES", {}) or {}
    inst[name] = {
        "kind": "drums" if name in drums else "melodic",
        "title": getattr(mod, "TITLE", None) or name.upper().replace("_", " "),
        "macros": ml, "modes": modes,
        "patches": {str(k): v[0] for k, v in sorted(patches.items())},
        "patch_values": {str(k): list(v[1]) for k, v in sorted(patches.items()) if len(v) > 1},
        "notes": [[n, label] for n, label in getattr(mod, "NOTE_MAP", ())],
        "voices": getattr(comp, "voices", None),
    }
    if name in drums:
        notes = [n for n, _ in getattr(mod, "NOTE_MAP", ())]
        inst[name]["pads"] = {str(k): audible_pads(name, k, notes) for k in sorted(patches) or [0]}
    comp.deinit()
eff = {}
src = audioinstruments.create("electric_piano", SR, channel_count=2)
for name in audioeffects.ALL:
    try:
        comp = audioeffects.create(name, src.output, SR)
    except Exception as e:
        continue
    mod = __import__(type(comp).__module__, None, None, ("x",))
    ml, modes = labels(comp, mod)
    patches = getattr(type(comp), "PATCHES", None) or getattr(mod, "PATCHES", {}) or {}
    vals = {str(k): list(v[1]) for k, v in sorted(patches.items()) if isinstance(v, tuple) and len(v) > 1} if isinstance(patches, dict) else {}
    if not vals and ml:
        vals = {"0": [round(comp.get_macro(i)) for i in range(len(ml))]}
    eff[name] = {"macros": ml, "modes": modes, "patch_values": vals,
                 "patches": {str(k): (v[0] if isinstance(v, tuple) else str(k)) for k, v in sorted(patches.items())} if isinstance(patches, dict) else {}}
    comp.deinit()
json.dumps({"instruments": inst, "effects": eff,
            "versions": {"audioinstruments": audioinstruments.__dict__.get("__version__", ""),
                         "audioeffects": audioeffects.__dict__.get("__version__", "")}})
`);
// The SoundFont instruments aren't in ALL (their patches are whatever bank is
// on the machine, which Pyodide can't see): scripts/make_soundfont_catalog.py
// writes them from a real bank, and they're carried over here.
const c = JSON.parse(out);
try {
  const old = JSON.parse(fs.readFileSync(root + "assets/catalog.json", "utf8"));
  for (const n of ["soundfont", "soundfont_drums"]) if (old.instruments[n]) c.instruments[n] = old.instruments[n];
  if (old.soundfont_bank) c.soundfont_bank = old.soundfont_bank;
} catch (e) {}
py.globals.set("merged", JSON.stringify(c));
fs.writeFileSync(root + "assets/catalog.json", py.runPython("json.dumps(json.loads(merged))"));
console.log("instruments", Object.keys(c.instruments).length, "effects", Object.keys(c.effects).length);
