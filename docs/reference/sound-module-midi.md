# The sound module's MIDI

What the sound module does with the MIDI it receives. It has sixteen parts,
one per MIDI channel, mixed through one chorus and one reverb. The source of
truth is the docstring at the top of
[`lib/soundmodule/engine.py`](../../lib/soundmodule/engine.py).

## Banks and programs

Bank Select (CC0, the MSB, and CC32, the LSB) then Program Change choose what
a part plays. A part that has had no Bank Select plays General MIDI, as a GM
module's parts do.

| Bank (CC0) | Program Change *n* plays | Bank LSB (CC32) |
|---|---|---|
| 0-79 | General MIDI program *n* from your SoundFont | the SoundFont's variation bank |
| 80 | the *n*-th of audiocomponents' instruments (`engine.INSTRUMENTS`) | the instrument's patch |
| 81 | the *n*-th drum machine (`engine.DRUM_MACHINES`) | the drum machine's patch |

Channel 10 plays the SoundFont's drums. The lists `INSTRUMENTS` and
`DRUM_MACHINES` are in `engine.py`; new entries go on the end, so a song's
program numbers keep their sound.

## Controllers

| Controller | Effect |
|---|---|
| CC102 to CC117 | Macro 0 to 15 of the part's instrument, on the instrument's own 0-127 scale |
| CC7 volume, CC10 pan | Every part. A SoundFont part plays volume as General MIDI's square law |
| CC64 sustain | Every part |
| CC91 reverb, CC93 chorus | Each part's send. There is one reverb and one chorus on the master, so its level is the mean of the sends of the parts in use |
| CC120, CC121, CC123 | All Sound Off, Reset All Controllers, All Notes Off, as General MIDI says |

Any other controller reaches the instrument's `control_change` as it is.

## Registered parameters

RPN (CC101 and CC100, then Data Entry on CC6 and CC38, or Increment and
Decrement on CC96 and CC97) sets, per part:

| RPN | Parameter | Power-on |
|---|---|---|
| 0 | Pitch bend sensitivity, up to 24 semitones | 2 semitones |
| 1 | Channel fine tuning, a hundred cents either way | centred |
| 2 | Channel coarse tuning, in semitones | centred |

RPN 127/127 (null) or any NRPN stops Data Entry reaching them. A SoundFont
part plays all three. The synths and drum machines do not bend, so nothing
reaches them.

## System Exclusive

GM System On, GM2 On, GS Reset and XG System On each put every part back to
its power-on state.

The Identity Request is answered with an Identity Reply: manufacturer `0x7D`
(the ID MIDI sets aside for non-commercial use), family 1, model 1.

## `soundmodule.run` options

| Option | Meaning |
|---|---|
| `--port NAME` | Open the first MIDI input and output whose names contain NAME (default `Sound module device side`) |
| `--pipe FOLDER` | Read and write the two files `tools/bridge.py` keeps, for the unix ports under WSL |
| `--wav PATH` | Write the rendered sound to a 16-bit WAV |
| `--play` | Play through `audiodev`, where there is one |
| `--bank SF2` | The SoundFont to open |
| `--seconds S` | Stop after S seconds (600 by default) |
| `--idle S` | Stop S seconds after the last MIDI byte |
| `--wait S` | Stop S seconds after starting if no MIDI came |
| `--tail S` | Render S more seconds at the end, 2 by default |
