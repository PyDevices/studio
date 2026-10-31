"""What instruments and effects exist, from assets/catalog.json.

scripts/make_catalog.mjs writes it from the same wheels the engines load,
so the UI knows every name, knob label, patch and drum pad without loading
the audio stack itself.
"""

import json

import page

data = {"instruments": {}, "effects": {}}


async def load(base):
    data.update(json.loads(await page.fetch_text(base + "assets/catalog.json")))


def instrument(name):
    return data["instruments"].get(name, {"kind": "melodic", "title": name, "macros": [], "modes": [],
                                          "patches": {}, "notes": []})


def pad_notes(track):
    """A drum track's sounds in the step grid's order: the kit's notes that
    sound on its patch. The DMK-25's pads play these, pad 1 the first."""
    info = instrument(track["instrument"])
    audible = info.get("pads", {}).get(str(track.get("patch", 0)))
    return [n for n, _ in info["notes"] if audible is None or n in audible] or [36, 38]


# Melodic instruments by family, in the order the list shows them.
FAMILIES = [
    ("Analog poly", ("poly106", "poly5", "poly6", "poly8", "poly80", "polya6", "polyxa")),
    ("Mono synths", ("acid303", "bass_pedals", "duo20", "duosynth", "hybrid_mono", "mono101", "mono_one", "mono_synth",
                     "patch2600", "state_variable_mono", "west_coast_synth")),
    ("Electric pianos and keys", ("clavichord", "electric_grand70", "electric_piano", "reed_piano", "reed_piano200a")),
    ("Organs", ("combo_organ", "compact_organ", "tonewheel3")),
    ("Strings and choirs", ("string_ensemble", "vocoder330")),
    ("Samplers", ("soundfont",)),
]


def grouped(kind):
    """The instrument list with family headings: [(heading or None, name or None)].
    A heading row has no name; drum machines come as one plain list."""
    names = instruments(kind)
    if kind == "drums":
        return [(None, n) for n in names]
    out, seen = [], set()
    for family, members in FAMILIES:
        here = [n for n in members if n in names]
        if here:
            out.append((family, None))
            out += [(None, n) for n in here]
            seen.update(here)
    rest = [n for n in names if n not in seen]
    if rest:
        out.append(("Other", None))
        out += [(None, n) for n in rest]
    return out


def instruments(kind):
    return sorted(n for n, v in data["instruments"].items() if v["kind"] == kind)


def effect(name):
    return data["effects"].get(name, {"macros": [], "modes": [], "patches": {}})


def effects():
    # "Rack" is the mechanism behind a chain, not an effect to add on its own.
    return sorted(n for n in data["effects"] if n != "Rack")
