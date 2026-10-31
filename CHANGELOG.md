# Changelog

## Unreleased

- The sound module: sixteen parts on one pool of voices, General MIDI from a
  SoundFont, audioinstruments' instruments on bank 80 and drum machines on
  bank 81, RPN pitch bend and tuning, reverb and chorus sends, and the
  Identity Reply. It runs headless on CPython, MicroPython and CircuitPython.
- Ostinato, the pattern sequencer and song editor, and Vamp, which plays its
  songs on a board, move into this repository.
- The instruments on banks 80 and 81 are audioinstruments' rack set. A
  program an earlier set had and this one doesn't plays nothing, and the
  programs after it keep their numbers. `Module.fill()` moves the
  instruments' notes and knobs into the sound: call it before each pull of
  `output` (`soundmodule.run` and `render_mid.py` do). MicroPython needs ulab
  built in as well as audiodsp.
- Ostinato plays audioinstruments' 33 instruments. Its new-project tracks, demo
  song and sketches use the rack's names, knob labels and drum notes; the
  browser app loads numpy for them. `tools/sketch_render.py` renders each
  song's tracks and checks every one makes sound.
- A gate that plays ordinary General MIDI files through the module and
  measures the result.
