# What is in studio

studio holds three applications that share a song model and an audio stack.

- **soundmodule** ([`lib/soundmodule`](../../lib/soundmodule)) is a MIDI
  tone module. Its engine has no face of its own: feed it MIDI bytes and pull
  its `output`, from a headless loop (`soundmodule.run`), a UI or a test.
- **Ostinato** ([`apps/ostinato`](../../apps/ostinato)) is the sequencer and
  song editor. LVGL draws its interface in a browser worker or a desktop
  window, and audioinstruments makes the sound.
- **Vamp** ([`lib/vamp`](../../lib/vamp)) plays Ostinato's songs on a
  MicroPython with no screen of its own. Its panels are the Ostinato window
  and a browser page, over the network.

The sound module also has a face elsewhere: midiEngine, the ESP32-P4
DEV-KIT's General MIDI player, runs this engine.

## The libraries underneath

- `midiports` and `hmilink`, in [pydevices](https://github.com/PyDevices/pydevices),
  give MIDI transports and the link between a device and its panels.
- Adafruit's `adafruit_midi` parses MIDI messages.
- [audiodsp](https://github.com/PyDevices/audiodsp) supplies the DSP kernels.
- [audioinstruments](https://github.com/PyDevices/audioinstruments) and
  [audioeffects](https://github.com/PyDevices/audioeffects) supply the
  instruments and effects.

When two applications need the same piece, it moves down: into `lib/` here
when it is about songs, or into one of those libraries. That is why
[`lib/songlink`](../../lib/songlink) is shared. It holds the song as plain
data, the edits panels send each other, and the browser panel's page. A song
changes only through ops that every side applies, which is how a window, a
page and a board edit one song at once. `songlink.model` imports on CPython,
Pyodide and MicroPython.

## Many voices, one pool

Every SoundFont part of the sound module reads the same open SoundFont and
plays on one pool of voices, as a hardware General MIDI module's parts do. A
busy part takes voices an idle one is not using, and the oldest releasing
voices are faded out to keep a few ready, so a new note never has to cut one
off with a click. An instrument part has its own voices, mixed in alongside.

## Shared reverb and chorus

Reverb (CC91) and chorus (CC93) are each part's send, but there is one reverb
and one chorus on the master, so the master's level is the mean of the
sends of the parts in use. A true send per part needs a bus in the sampler's
kernel, which it does not have yet; the [roadmap](../../ROADMAP.md) lists it.

How the pieces are tested is in [How it is checked](how-its-checked.md).
