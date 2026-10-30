# Play the sound module from a DAW

The sound module plays whatever MIDI reaches it, the way a hardware tone
module does. You set nothing up on it: each part's Bank Select and Program
Change pick the sound, so a DAW's own instrument-selection UI is enough. The
[reference](../reference/sound-module-midi.md) lists the banks.

You need [studio installed](../get-started/install.md), a SoundFont, and a
virtual MIDI port your DAW can send to, such as a loopback port on Windows.

## Run it on a port

With `lib/`, audiocomponents' `lib/` and pydevices' `midiports` on the path,
name the port by any part of its name:

```bash
python -m soundmodule.run --port "Sound module device side" --play
```

`--play` sends the sound to your sound card through `audiodev`, where the
interpreter has it. To record instead of play, write a WAV:

```bash
python -m soundmodule.run --port "Sound module device side" --wav take.wav --idle 3
```

`--idle 3` stops three seconds after the last MIDI byte. The run also stops
after `--seconds` (600 by default), or after `--wait` seconds if no MIDI ever
arrives, and then renders `--tail` seconds more so the reverb dies away in
the file. When it stops it prints what each channel played, and it exits
non-zero if no MIDI arrived.

Point your DAW's output at the other end of the port and press play.

## From MicroPython or CircuitPython under WSL

The unix interpreters under WSL cannot open a Windows MIDI port. Run
[`tools/bridge.py`](../../tools/bridge.py) on the Windows side to carry the
port through two files, and read them with `--pipe`:

```bash
python.exe tools/bridge.py --port "Sound module device side" --pipe \\wsl.localhost\Ubuntu\tmp\sm
micropython -m soundmodule.run --pipe /tmp/sm --wav song.wav --idle 4
```

The bridge stops when a file named `stop` appears in the folder, or after
`--seconds`.

## Ask the module what it is

Send MIDI's Identity Request (`F0 7E <id> 06 01 F7`) and the module answers
with an Identity Reply, so a DAW can find it. The reply's fields are in the
[reference](../reference/sound-module-midi.md#system-exclusive).
