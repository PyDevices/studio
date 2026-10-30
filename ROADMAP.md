# Roadmap

Three applications live here: a sound module, Ostinato the sequencer, and
Vamp, which plays Ostinato's songs on a board. The first is a sound module: a box that plays whatever a DAW,
sequencer or keyboard sends it over MIDI, the way a hardware tone module does.
It grows one part at a time, on the desktop interpreters first.

## Sound module

- Patch and macro names that DAWs can read, from the catalog files
  audiocomponents publishes with each release.
- A SysEx request that returns the module's instruments, patches, effects and
  controls, shaped after MIDI-CI Property Exchange.
- On an ESP32-P4 as a USB MIDI device, with 24 voices at 48 kHz stereo.
- Over BLE MIDI.
- Reverb and chorus as true sends per channel, once the sampler can mix its
  voices into a send bus. Today CC91 and CC93 set one master level, the mean
  of the channels in use.

## Vamp

- A MIDI keyboard such as the DMK-25 on the board's USB host port, playing
  and recording into the selected track.
- Undo in the window that knows about edits made on other panels.
- The timed voice pool that schedules notes ahead of the audio moving into
  audioinstruments, once a second player needs it.

## Ostinato

- A published copy of the app on the web, so it installs without running a
  local server.
