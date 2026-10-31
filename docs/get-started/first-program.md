# Your first program

[`tools/render_mid.py`](../../tools/render_mid.py) plays a Standard MIDI File
through the sound module and writes the result to a WAV. It needs no MIDI
hardware and no sound card, and it is the same engine that plays live MIDI.
You need [studio installed](install.md) and a SoundFont.

From the studio folder:

```bash
python tools/render_mid.py tests/midi/musicbox1_spooky_waltz.mid take.wav --bank GeneralUser-GS.sf2 --path lib
```

`--bank` names your SoundFont, and `--path lib` puts this repository's `lib/`
on the import path, where the `soundmodule` package is. The file is a
music-box piece of 53 seconds, one of the three General MIDI songs in
[`tests/midi`](../../tests/midi/README.md).

It writes `take.wav`, a 48 kHz stereo WAV, and prints what it measured:

```
notes 261
played 261
held 0
sounding 0
song 53.310
render 0.947
reverb 0
chorus 0
soundmodule: cpython 3.12.3 (linux): 0 bytes of MIDI, 0 Identity Requests answered, render behind by at most 0 ms while playing (0 ms before the first note)
  channel  1:  157 notes, bank 0:0 program 8, Celesta
  channel  2:   56 notes, bank 0:0 program 8, Celesta
  channel  3:   32 notes, bank 0:0 program 8, Celesta
  channel 16:   16 notes, bank 0:0 program 8, Celesta
```

- `notes` is how many note-ons the file has, and `played` how many the
  module's parts took. They match when nothing was lost.
- `held` and `sounding` are notes and voices still on after the song and its
  tail: both should be 0.
- `song` is the music's length in seconds and `render` the time the render
  took. Yours will differ.
- Each `channel` line is one MIDI channel: its notes, its Bank Select and
  Program Change, and the General MIDI name of the program.

Open `take.wav` in any player. The same run works on a MicroPython or
CircuitPython that has audiodsp built in: `micropython -X heapsize=64M
tools/render_mid.py ...`, with audioinstruments' and audioeffects' `lib/` on `MICROPYPATH`.

Next: [play the module from a DAW](../guides/play-the-sound-module.md).
