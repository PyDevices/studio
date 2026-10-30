studio is a set of music applications built on the PyDevices audio and MIDI stack, on the desktop and on boards: a sound module that plays whatever a DAW or keyboard sends it, Ostinato, a pattern sequencer and song editor in your browser, and Vamp, which plays Ostinato's songs on a board.

## Install

studio is used from a clone, beside the libraries it builds on:

```bash
git clone https://github.com/PyDevices/studio
git clone https://github.com/PyDevices/audiocomponents
git clone https://github.com/PyDevices/audiodsp
pip install ./audiodsp numpy
pip install --no-deps ./audiocomponents/lib/audioinstruments ./audiocomponents/lib/audioeffects
```

## Try it

Render a MIDI file through the sound module to a WAV, with no MIDI hardware:

```bash
cd studio
python tools/render_mid.py tests/midi/musicbox1_spooky_waltz.mid take.wav --path lib
```

It writes `take.wav`, 53 seconds of celesta, and prints what it measured:

```
notes 261
played 261
held 0
sounding 0
song 53.310
...
  channel  1:  157 notes, bank 0:0 program 8, Celesta
```

[Docs](docs/guide.md) | [Examples](apps/ostinato) | [Roadmap](ROADMAP.md)

Made by [PyDevices](https://pydevices.com).
