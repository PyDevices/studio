# Roadmap

The first application is a sound module: a box that plays whatever a DAW,
sequencer or keyboard sends it over MIDI, the way a hardware tone module does.
It grows one part at a time, on the desktop interpreters first.

## Sound module

- Sixteen parts, one per MIDI channel, playing audiocomponents' instruments:
  Bank Select and Program Change pick the sound, each macro is a CC, and the
  module answers the MIDI Identity Request.
- Patch and macro names that DAWs can read, from the catalog files
  audiocomponents publishes with each release.
- SoundFont playback: bring your own `.sf2` and it plays as a General MIDI set
  on bank 0, with at least the 24 voices GM requires. No bank ships with it.
- The rest of General MIDI: reverb and chorus sends, pitch-bend range, GM
  System On and the reset messages, so ordinary GM song files play correctly.
- A SysEx request that returns the module's instruments, patches, effects and
  controls, shaped after MIDI-CI Property Exchange.
- On an ESP32-P4 as a USB MIDI device, with 24 voices at 48 kHz stereo.
- Over BLE MIDI.
