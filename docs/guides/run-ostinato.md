# Run Ostinato

Ostinato is a pattern sequencer and song editor. It runs in a browser, where
you can install it from the address bar as an app that works offline, and as
a native window on the desktop. It lives in [`apps/ostinato`](../../apps/ostinato).

The steps are in its [README](../../apps/ostinato/README.md), and every
command there runs from that folder:

- **In a browser.** Fetch the PyDevices wheels it runs on, vendor PyScript
  once, generate what each worker loads, then serve the folder with
  `tools/serve.py` and open `http://127.0.0.1:8765/` in Chrome or Edge.
- **On the desktop.** Python 3.14 or later, a virtual environment with the
  released PyDevices stack, and `python -m ostinato`.

Both read the song model in [`lib/songlink`](../../lib/songlink), which
Vamp uses too. `tools/serve.py` serves that folder from this repository, so
run it from a studio checkout.

## Use a SoundFont in Ostinato

The two SoundFont instruments, **SOUNDFONT** and **SOUNDFONT DRUMS**, play
whatever `.sf2` they find: the `SOUNDFONT` environment variable, else the
first one in `%USERPROFILE%\soundfonts` (`~/soundfonts` elsewhere). Without
one the track stays silent and the status line says so.
[`apps/ostinato/songs/sketches/soundfont_check.json`](../../apps/ostinato/songs/sketches/soundfont_check.json)
plays all four tracks on them.

## Give it a second panel

On the desktop, `python -m ostinato --split` runs the engines and the song in
a process of their own, with the window as its front panel, over TCP on port
9100. A browser at `http://127.0.0.1:9100/` is a second panel for the same
song: an edit on either shows on the other. `--connect HOST:PORT` opens just
the window, joining a song that is already playing, such as one on
[Vamp](put-vamp-on-a-board.md).
