# studio

Music applications built on the PyDevices audio and MIDI stack. Each one runs
on CPython, MicroPython and CircuitPython, on the desktop and on boards.

They're built from libraries kept elsewhere: `midiports` (in `pydevices`) for
MIDI transports, Adafruit's `adafruit_midi` for MIDI messages, `audiodsp` for
the DSP kernels, and `audiocomponents` for the instruments and effects. When two applications
need the same piece, that piece moves down into one of those libraries.

Nothing here is settled yet: names, APIs and layout may all change.

## The sound module

`soundmodule` plays whatever a DAW, sequencer or keyboard sends it over MIDI,
the way a hardware tone module does. You set nothing up on it: each part's
Bank Select and Program Change choose its sound.

| Bank (CC0) | Program Change *n* plays | Bank LSB (CC32) |
|---|---|---|
| 0-79 | General MIDI program *n* from your SoundFont | the SoundFont's variation bank |
| 80 | the *n*-th of audiocomponents' instruments (`engine.INSTRUMENTS`) | the instrument's patch |
| 81 | the *n*-th drum machine (`engine.DRUM_MACHINES`) | the drum machine's patch |

Macro *i* of a part's instrument is CC 102 + *i*. Volume (CC7) and pan (CC10)
work on every part. The module answers the MIDI Identity Request with
manufacturer `7D`, family 1, model 1.

To run it headless on a MIDI port, with `lib/`, audiocomponents' `lib/` and
pydevices' `midiports` on the path:

    python -m soundmodule.run --port "Sound module device side" --play
    python -m soundmodule.run --port "Sound module device side" --wav take.wav --idle 3

It prints what each channel played when it stops. `--pipe FOLDER` reads MIDI
from a file instead, for the unix ports under WSL, which can't open a Windows
MIDI port (`tools/bridge.py` fills the file from the port). The module's own
docstrings, in [`lib/soundmodule/engine.py`](lib/soundmodule/engine.py) and
[`lib/soundmodule/run.py`](lib/soundmodule/run.py), have the rest.

midiEngine, the DEV-KIT's GM player, is a face on this engine.

## Tests

    python -m unittest discover -s tests

runs [`tests/engine_contract.py`](tests/engine_contract.py) on CPython, unix
MicroPython and unix CircuitPython, and once per planted fault.

[`tools/gate.py`](tools/gate.py) is the end-to-end check, run under WSL with
REAPER on Windows: REAPER plays a five-part song
([`tools/song.py`](tools/song.py)) to the module over a loopback MIDI port, on
each interpreter in turn, and it checks every part arrived and was heard. Its
docstring lists what it needs on Windows.
