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
    ("Analog poly", ("andromeda", "cs80", "jupiter8", "juno106", "obxa", "polysix", "prophet5")),
    ("Virtual analog", ("jp8000", "ms2000", "nord_lead", "virus")),
    ("Mono synths", ("arp2600", "minimoog", "ms20", "music_easel", "odyssey", "sh101", "taurus", "tb303", "wasp")),
    ("Digital and FM", ("cz101", "d50", "dx7", "fs1r", "microwave", "ppg_wave", "prophet_vs")),
    ("Electric pianos and keys", ("clavinet", "cp70", "pianet", "rhodes", "wurlitzer")),
    ("Organs", ("b3", "farfisa", "vox_continental")),
    ("Strings and choirs", ("mellotron", "solina", "vp330")),
    ("Samplers and others", ("soundfont", "emulator2", "fairlight", "k2600", "karplus", "vl1")),
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
