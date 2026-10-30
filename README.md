# studio

Music applications built on the PyDevices audio and MIDI stack, on the
desktop and on boards:

- **soundmodule** plays whatever a DAW, sequencer or keyboard sends it over
  MIDI, like a hardware tone module.
- **Ostinato** is a pattern sequencer and song editor that runs in the
  browser, installable as an app, and as a desktop window.
- **Vamp** plays Ostinato's songs on a board with no screen of its own: the
  window and a browser page are its panels, over the network.

They're built from libraries kept elsewhere: `midiports` and `hmilink` (in
`pydevices`) for MIDI transports and the link between a device and its panels, Adafruit's `adafruit_midi` for MIDI messages, `audiodsp` for
the DSP kernels, and `audiocomponents` for the instruments and effects. When two applications
need the same piece, that piece moves down: into `lib/` here when it's about
songs, or into one of those libraries.

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
work on every part, and so do the bend range and the tuning by RPN, the
reverb and chorus sends, and GM System On. The module answers the MIDI Identity Request with
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

## Ostinato

Ostinato lives in [`apps/ostinato`](apps/ostinato), and every command in its
[README](apps/ostinato/README.md) runs from that folder. It shares the song
model with Vamp through [`lib/songlink`](lib/songlink): the song as plain
data, the edits panels send each other, and the browser panel's page.

To run it in your browser, vendor PyScript once and serve the folder:

    cd apps/ostinato
    ./scripts/vendor_pyscript.sh
    python tools/serve.py

then open `http://127.0.0.1:8765/` and install it from the address bar if you
want it as an app. The server also serves `lib/songlink` from this repository,
which the page loads.

## Vamp

Vamp is the sound and the song on one MicroPython, an ESP32-P4 DEV-KIT or a
desktop interpreter, with the screens somewhere else. Ostinato's window and a
browser page edit the song it plays, live, over
[hmilink](https://github.com/PyDevices/pydevices/tree/main/hmilink); a MIDI
keyboard on the board plays the selected track.

To put it on a board, with mpftp installed and the board's port or address:

    python tools/vamp_files.py COM4

That copies `vamp`, `songlink` and `hmilink` into `/lib` and a song to
`/vamp_song.json`. A GM SoundFont goes on the SD card at
`/sd/soundfonts/GeneralUser-GS.sf2`. Then, on the board:

    import vamp; vamp.run()

On desktop MicroPython, with studio's `lib/`, hmilink, audiocomponents' `lib/`
and pydevices' `lib/` on `MICROPYPATH`:

    micropython -X heapsize=96M -m vamp.main --bank GeneralUser-GS.sf2 --song apps/ostinato/songs/sketches/soundfont_check.json

Open `http://HOST:9100/` for the browser panel, or join with the window from
`apps/ostinato`:

    python -m ostinato --connect HOST:9100

Every panel sees the same song, and an edit on one shows on the others while
the box keeps playing. [`lib/vamp/main.py`](lib/vamp/main.py) lists the rest
of the options.

## Tests

    python -m unittest discover -s tests

runs [`tests/engine_contract.py`](tests/engine_contract.py) on CPython, unix
MicroPython and unix CircuitPython, and once per planted fault. It runs Vamp's
checks too, on CPython and unix MicroPython, on a small bank it writes itself:
[`tests/vamp_check.py`](tests/vamp_check.py) for the engine's timing, mutes,
chokes and effects, and [`tests/vamp_link_contract.py`](tests/vamp_link_contract.py)
for two panels editing a playing box. Each has faults planted to prove it
catches them. hmilink comes from `$HMILINK_PATH` or a pydevices checkout beside
this one.

Ostinato's own tests run from its folder:

    cd apps/ostinato && python -m unittest discover -s tests

[`tools/vamp_link_check.py`](tools/vamp_link_check.py) drives a running box,
on a board or the desktop, the way a panel does, and with `--browser` drives
its page in Chromium too.

[`tools/gate.py`](tools/gate.py) is the end-to-end check, run under WSL with
REAPER on Windows: REAPER plays a five-part song
([`tools/song.py`](tools/song.py)) to the module over a loopback MIDI port, on
each interpreter in turn, and it checks every part arrived and was heard. Its
docstring lists what it needs on Windows.

[`tools/gm_gate.py`](tools/gm_gate.py) plays Standard MIDI Files through the
module offline ([`tools/render_mid.py`](tools/render_mid.py)) on all four
interpreters and measures the WAVs: the pitch of bent and tuned notes, levels
after each reset, the reverb's tail, and that nothing is left sounding.
Its own two songs come from [`tools/gm_songs.py`](tools/gm_songs.py), with a
sine bank so a pitch reads straight off the WAV. It also plays the ordinary
GM files in [`tests/midi`](tests/midi/README.md) on your own bank
(`--songs FOLDER` for others), and `--plants` shows each check
fail on a fault planted for it.
