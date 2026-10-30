# Install

studio is used from a clone, beside the libraries it builds on. The sound
module and Vamp need audiodsp, which provides the audio nodes, and
audiocomponents, which provides the instruments and effects:

```bash
git clone https://github.com/PyDevices/studio
git clone https://github.com/PyDevices/audiocomponents
git clone https://github.com/PyDevices/audiodsp
pip install ./audiodsp numpy
pip install --no-deps ./audiocomponents/lib/audioinstruments ./audiocomponents/lib/audioeffects
```

## A SoundFont

The sound module plays General MIDI from a SoundFont, which studio does not
ship. Download a General MIDI `.sf2`, such as
[GeneralUser GS](https://schristiancollins.com/generaluser.php), and either
pass it with `--bank`, set the `SOUNDFONT` environment variable, or put it in
`~/soundfonts` (`%USERPROFILE%\soundfonts` on Windows). Without one, the
General MIDI parts stay silent and the run prints `no SoundFont found`.

## What else a program needs

- **MIDI ports** (the sound module running live): `midiports`, from the
  [pydevices](https://github.com/PyDevices/pydevices) repository.
- **Ostinato** in a browser or a desktop window, and **Vamp**: see their
  guides, [Run Ostinato](../guides/run-ostinato.md) and
  [Put Vamp on a board](../guides/put-vamp-on-a-board.md). Vamp also needs
  `hmilink`, from pydevices.

Once the pip lines have run, [render a MIDI file](first-program.md).
