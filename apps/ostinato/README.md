# Ostinato

A groovebox and a playable instrument, in Python, in your browser. Play a
synth from a MIDI keyboard, put drums on a step grid, record loops and fix
them in a piano roll, chain patterns into a song, put effects on each track,
and export a WAV or a MIDI file. It installs as an app and works offline.

It's built from PyDevices: LVGL draws the interface, audiocomponents' 55
instruments and 44 effects make the sound, and `midiports` and
`adafruit_midi` read the keyboard, all running in Pyodide in Web Workers.

## Run it

Every command here runs from this folder, `apps/ostinato` in studio.

```bash
.venv/bin/python scripts/fetch_wheels.py      # the PyDevices wheels it runs on
.venv/bin/python scripts/fetch_midi.py        # midiports, adafruit_midi, the board config
bash scripts/vendor_pyscript.sh               # Pyodide
.venv/bin/python tools/gen_boot.py            # what each worker loads
.venv/bin/python tools/serve.py               # http://127.0.0.1:8765/
```

Open http://127.0.0.1:8765/ in Chrome or Edge, click once to start the sound,
allow MIDI when the browser asks, and use the install button in the address
bar to install it. The ? button in the app explains everything else.

## Run it on the desktop

Ostinato also runs as a native Windows app on Python 3.14 or later, without a browser.
It opens maximised, plays through WASAPI, and reads every MIDI input it finds.

Install Python 3.14 from python.org, then make a virtual environment with the
released PyDevices stack:

```bat
py -3.14 -m venv %USERPROFILE%\ostinato-venv
%USERPROFILE%\ostinato-venv\Scripts\python -m pip install --pre --no-cache-dir ^
  --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple ^
  pydevices pydevices-desktop pydevices-audiodsp pydevices-lvgl ^
  pydevices-audioinstruments pydevices-audioeffects numpy
```

Then, from a checkout (after `scripts/fetch_midi.py`, which brings midiports):

```bat
%USERPROFILE%\ostinato-venv\Scripts\python -m ostinato
```

From WSL, `tools/win_run.sh` does the same: it copies the checkout to
`C:\temp\ostinato-desktop` and starts `python.exe -m ostinato` there, with the
venv above unless `PY315` names another interpreter.

- `--engines processes` (the default) runs each track's engine in a process
  of its own, which keeps the audio steady while the screen redraws.
  `--engines threads` runs them as threads: on the free-threaded build
  (`python3.15t -X gil=0`) they run in parallel; on the regular build it's a
  debugging aid.
- `--midi NAME` opens only the MIDI inputs whose names contain NAME.
- `--fresh` starts without the last session.
- `--split` runs the engines and the song in a process of their own and the
  window as its front panel, over TCP on port 9100 (`--port`). Open
  http://127.0.0.1:9100/ in a browser (or the PC's address from a phone on the
  same network) and you get a second front panel: tempo, Play and Stop, each
  track's mute and volume, and a step grid. An edit on either shows on the other.
  `--device` is that process alone, with no window, and `--connect HOST:PORT`
  is the window alone, joining one that's already running. That one can be
  Vamp, studio's headless groovebox: the window is one of its front panels.

The two SoundFont instruments, **SOUNDFONT** (General MIDI's 128 programs)
and **SOUNDFONT DRUMS** (its kits), play whatever `.sf2` they find: the
`SOUNDFONT` environment variable, else the first one in
`%USERPROFILE%\soundfonts` (`~/soundfonts` elsewhere). Without a bank the
track stays silent and the status line says so. `songs/sketches/soundfont_check.json`
plays all four tracks on them.

## The demo song

The first time Ostinato opens, it loads a demo song, and **Demo song** in the
Ostinato menu brings it back. It's a four-minute piece composed by
[SoundHelix](https://www.soundhelix.com), an algorithmic composer, laid out
the way you'd build it yourself: eight patterns a track, 36 scenes, and
SoundHelix's own swing.

`scripts/soundhelix.sh` makes it again. It downloads SoundHelix 0.9 (GPL v3),
generates a song from SoundHelix's Piano example, and writes
`assets/demo_song.json`. Give it a different song name for a different song:

```bash
scripts/soundhelix.sh "Another name"
```

SoundHelix writes seven parts and Ostinato has four tracks, so the parts
share: percussion on Drums, both bass lines on Bass, the chords and the pad
on Keys, the arpeggio and the melody on Lead. Each track's instrument has the
voices its parts need at once. A track with more than eight distinct
four-bar chunks keeps the eight it plays most and uses the closest of them
for the rest; the script says how many bars that changed.

## How it's put together

- `index.html` keeps only what has to be on the page: the canvas and its
  input, Web MIDI, and the AudioContext.
- `app/ui/` is the interface, in a worker. It draws LVGL on an
  OffscreenCanvas through pydevices' `displaydev.workerdisplay`.
- `app/engine/` makes the sound, one worker per track. Each worker renders
  its track and hands it to the master worker (`app/engine/master.py`),
  which sums them, runs the master effects, and is the one stream the
  AudioWorklet (`workers/audio_worklet.js`) plays.
- The project is `songlink.model`, in studio's `lib/songlink/`: plain data,
  changed only by ops that both sides apply. Vamp plays the same songs.
- `workers/` is pyscript-template's worker kit.

`songlink.model` imports on CPython, Pyodide and MicroPython. The engine and
UI around it haven't been tried on a board yet; on a board, the songs play on
Vamp instead (studio's `lib/vamp/`).

## Checks

Timing is measured in real Chrome (`tools/win_chrome.sh` starts one that the
tools drive); headless Chromium's audio clock isn't reliable under load.

Run them all in one app session, about four minutes:

```bash
.venv/bin/python tools/check_all.py --cdp http://127.0.0.1:9222
```

`--long` adds the whole demo song's export (about two minutes more), and
`--only master,demo` picks checks. Each check also runs on its own.

| Tool | Checks |
|---|---|
| `tools/app_play.py` + `tools/tap_check.py` | MIDI to sound: every note heard, pitch, key-to-sound latency, dropouts |
| `tools/groovebox_check.py` | step grid, piano roll, undo, loop recording, editing during playback |
| `tools/files_check.py` | song order, save and open, autosave, WAV and MIDI export |
| `tools/switch_check.py` | changing an instrument mid-song drops nothing |
| `tools/master_check.py` | master effects and volume reach the sound, live and exported; swing reaches the sound; the meters read each track and the mix; switching master effects while playing drops nothing |
| `tools/workflow_check.py` | every knob shown; pattern copy and bar repeat; confirm before discarding; play from a scene, loop it, edit from it; tempo hold, drag and tap; drum accents; count-in and quantise off; hardware knobs on the master; undo of sound changes; the record button |
| `tools/workflow2_check.py` | each track's own pattern, switching at the bar (Shift: every track); scene duplicate and picker; pads play the grid's rows; mute out of undo, pan and slider bank B; velocity by Shift-drag; overdub off replaces a take, and song takes land in the scene's pattern; effects reorder; Delete and wheel scrolling; knob drag, reset and instrument stepping; Save, Ctrl+S and Save first; the keyboard's Forward, Loop and Rewind, Learn, and octave keys |
| `tools/demo_check.py` | the demo song loads on the first run and from the menu, swing and the scene list work, it exports with every track heard |
| `tools/soundfont_check.py` | (desktop, also `--desktop split`) the SoundFont song opens, exports with every track heard and plays; `--no-bank`: without a `.sf2` the tracks stay silent and the status line says so |
| `tools/instrument_sweep.mjs`, `tools/effect_sweep.mjs` | every instrument's pitch, every effect on and bypassed |
| `tools/offline_check.py`, `tools/check_no_links.py` | starts offline; nothing navigates away |
| `tools/split_check.py` | (desktop, `--split`) the window and a browser on the engines' process: a song the window opens shows on the page with what its samples cost; the page's step, mute and tempo reach the window, the window's swing reaches the page; Play from the page plays through the mixer |
| `tools/link_check.py` | (`--split`, any Python with Playwright, no audio) the window and the browser page edit one song and agree with the engines' process: each other's edits, crossed edits to one pattern, Play from the page, a new song; two ops delivered in the wrong order are caught and the song taken again (`--defeat` turns that guard off and fails) |
| `tools/lookahead_check.py` | (CPython with the audio stack, no window) notes sequenced ahead play on their sample while the sequencer is paused 32 ms in 53, and an edit is heard as soon as without the lookahead (`--plant` fails); a SoundFont song's samples are read when it opens and on a program change |

Each check has been shown to fail on a fault planted on purpose.
