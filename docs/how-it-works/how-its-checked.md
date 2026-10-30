# How it is checked

Every check in studio has been shown to fail on a fault planted on purpose.
`tests/engine_contract.py` keeps a `PLANTS` table, one broken-on-purpose
variant per check, and the suite runs the contract once per plant and expects
it to fail. A check that has only ever passed proves nothing, so a new check
comes with its plant.

## The unit checks

`python -m unittest discover -s tests` runs the sound module's contract on
CPython, unix MicroPython and unix CircuitPython, and Vamp's checks on CPython
and unix MicroPython, on a small SoundFont bank the tests write themselves:

- [`tests/engine_contract.py`](../../tests/engine_contract.py): the module's
  behaviour from MIDI in to sound out.
- [`tests/vamp_check.py`](../../tests/vamp_check.py): Vamp's engine, for
  timing, mutes, chokes and effects.
- [`tests/vamp_link_contract.py`](../../tests/vamp_link_contract.py): two
  panels editing a playing box.

Ostinato's tests run from its own folder.

## The gates

Two checks run the module against real MIDI sources, on every interpreter in
turn. They need a Windows machine as well as WSL.

- [`tools/gate.py`](../../tools/gate.py) has REAPER play a five-part song
  ([`tools/song.py`](../../tools/song.py)) to the module over a loopback
  MIDI port. It passes an interpreter when every part arrived with its own
  notes, bank and program, each part's instrument is the one that played,
  every part's entry is audible in the WAV, and the render never fell more
  than `--max-lag` (250 ms by default) behind the clock.
- [`tools/gm_gate.py`](../../tools/gm_gate.py) renders Standard MIDI Files
  offline on all four interpreters and measures the WAVs: the pitch of bent
  and tuned notes, levels after each reset, the reverb's tail, and that
  nothing is left sounding. Its own two songs come from
  [`tools/gm_songs.py`](../../tools/gm_songs.py), with a sine bank so a pitch
  reads straight off the WAV. It also plays the General MIDI files in
  [`tests/midi`](../../tests/midi/README.md) on your own bank, and `--plants`
  shows each check fail on a fault planted for it.

## Ostinato in a real browser

Ostinato's timing is measured in Chrome, because headless Chromium's audio
clock is not reliable under load. Its checks, which cover MIDI-to-sound
latency, the editing workflows, file export and offline start, are listed in
the [Ostinato README](../../apps/ostinato/README.md#checks).

To run them yourself, see [CONTRIBUTING.md](../../CONTRIBUTING.md).
