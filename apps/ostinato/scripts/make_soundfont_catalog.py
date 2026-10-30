"""Add the SoundFont instruments to assets/catalog.json.

    python scripts/make_soundfont_catalog.py [--bank FILE.sf2]

scripts/make_catalog.mjs walks audioinstruments.ALL under Pyodide, and the
SoundFont instruments aren't in ALL: their patches are whatever bank is on
the machine, which Pyodide can't see. This writes the same shape for
``soundfont`` and ``soundfont_drums`` from a real bank, naming the patches
from it, and leaves every other entry alone. Run it after make_catalog.mjs,
with a Python that has audiodsp (with audiosampler) and the audioinstruments
that carries soundfont.py.

The bank is the one soundfont.find_bank() would use (``--bank``, else
$SOUNDFONT, else the first .sf2 in ~/soundfonts); the catalog records which.
"""

import argparse
import array
import json
import os
import re
import sys

import audiocore
import audioinstruments

SR = 48000
NAMES = ("soundfont", "soundfont_drums")
HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.join(HERE, "..", "assets", "catalog.json")


def audible_pads(inst, patch, notes):
    """The pads that make a sound for this patch, as make_catalog.mjs measures them."""
    inst.program_change(patch)
    out = []
    for n in notes:
        inst.note_on(n, 127)
        peak = 0
        for _ in range(47):            # 250 ms
            a = array.array("h")
            a.frombytes(bytes(audiocore.get_buffer(inst.output)[1]))
            peak = max(peak, max(abs(v) for v in a))
        inst.note_off(n)
        if peak >= 60:
            out.append(n)
        for _ in range(20):
            audiocore.get_buffer(inst.output)
    return out


def labels(comp, mod):
    cls = type(comp)
    ml = list(getattr(comp, "macro_labels", None) or getattr(cls, "MACRO_LABELS", ()) or getattr(mod, "MACRO_LABELS", ()) or ())
    modes = getattr(cls, "MACRO_MODES", None) or getattr(mod, "MACRO_MODES", {}) or {}
    return ml, [modes.get(i, "UNIPOLAR") for i in range(len(ml))]


def max_voices(mod):
    try:
        m = re.search(r"\bMAX_VOICES\s*=\s*(\d+)", open(mod.__file__).read())
    except (OSError, AttributeError):
        return None
    return int(m.group(1)) if m else None


def entry(name, bank):
    mod = audioinstruments.load(name)
    comp = audioinstruments.create(name, SR, channel_count=2, bank=bank)
    ml, modes = labels(comp, mod)
    patches = comp.patches                 # the bank's presets, not the module's literal
    kind = "drums" if "NOTE_MAP" in vars(mod) else "melodic"
    out = {
        "kind": kind,
        "title": getattr(mod, "TITLE", None) or name.upper().replace("_", " "),
        "macros": ml, "modes": modes,
        "patches": {str(k): v[0] for k, v in sorted(patches.items())},
        "patch_values": {str(k): list(v[1]) for k, v in sorted(patches.items()) if len(v) > 1},
        "notes": [[n, label] for n, label in getattr(mod, "NOTE_MAP", ())],
        "voices": max_voices(mod) or max_voices(audioinstruments.load("soundfont")),
    }
    if kind == "drums":
        notes = [n for n, _ in mod.NOTE_MAP]
        out["pads"] = {str(k): audible_pads(comp, k, notes) for k in sorted(patches) or [0]}
    comp.deinit()
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bank", default=None, help="the .sf2 to read the patch names from")
    a = ap.parse_args()
    from audioinstruments import soundfont

    bank = soundfont.find_bank(a.bank)
    with open(CATALOG) as f:
        catalog = json.load(f)
    for name in NAMES:
        catalog["instruments"][name] = entry(name, bank)
    catalog["soundfont_bank"] = os.path.basename(bank)
    with open(CATALOG, "w") as f:
        json.dump(catalog, f)
    for name in NAMES:
        e = catalog["instruments"][name]
        print(name, e["kind"], len(e["patches"]), "patches", "pads" if "pads" in e else "", file=sys.stderr)


if __name__ == "__main__":
    main()
