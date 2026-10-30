# Put Vamp on a board

Vamp plays Ostinato's songs: the sound and the song on one MicroPython, an
ESP32-P4 DEV-KIT or a desktop interpreter, with the screens somewhere else.
Ostinato's window and a browser page edit the song it plays, live, over
[hmilink](https://github.com/PyDevices/pydevices/tree/main/hmilink). A MIDI
keyboard on the board plays the selected track.

You need [studio installed](../get-started/install.md), a General MIDI
SoundFont, [mpftp](https://github.com/PyDevices/mpftp) installed, and a clone
of [pydevices](https://github.com/PyDevices/pydevices) beside studio, where
`tools/vamp_files.py` finds hmilink (`--hmilink DIR` names it otherwise).

## On a board

Copy the files, with the board's port or address:

```bash
python tools/vamp_files.py COM4
```

That puts `vamp`, `songlink` and `hmilink` into `/lib` and a song at
`/vamp_song.json`; `--song FILE` picks a different song. Put the SoundFont on
the SD card at `/sd/soundfonts/GeneralUser-GS.sf2`. Then, on the board:

```python
import vamp; vamp.run()
```

## On a desktop MicroPython

With studio's `lib/`, hmilink, audiocomponents' `lib/` and pydevices' `lib/`
on `MICROPYPATH`:

```bash
micropython -X heapsize=96M -m vamp.main --bank GeneralUser-GS.sf2 --song apps/ostinato/songs/sketches/soundfont_check.json
```

## Open a panel

Browse to `http://HOST:9100/` for the browser panel, or join with Ostinato's
window, from `apps/ostinato`:

```bash
python -m ostinato --connect HOST:9100
```

Every panel sees the same song, and an edit on one shows on the others while
the box keeps playing. The options (`--port`, `--midi`, `--lookahead-ms`,
`--seconds`) are listed at the top of [`lib/vamp/main.py`](../../lib/vamp/main.py).

## Check that a box is behaving

[`tools/vamp_link_check.py`](../../tools/vamp_link_check.py) drives a running
box, on a board or the desktop, the way a panel does. With `--browser` it
drives the page in Chromium too.
