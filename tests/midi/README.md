# MIDI files

Ordinary General MIDI songs that `tools/gm_gate.py` plays start to finish
(its `--songs` default), kept so a later change can be checked against the
same files. All three are CC0 (public domain dedication), from OpenGameArt.

| File | What it exercises | Source | Author | Licence |
|---|---|---|---|---|
| `B12 - Tensali ex Daschege.mid` | 147 s, eleven channels, 1774 notes, pads whose release runs about seven seconds | [Naranoiston Djonisya + Naranoiston Tenazalina](https://opengameart.org/content/naranoiston-djonisya-naranoiston-tenazalina) | Djsedj | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `BIFF_e1m2.mid` | 90 s, 1309 notes, a 12-semitone bend range set by RPN 0 on three channels | [Indiscriminate MIDI Music](https://opengameart.org/content/indiscriminate-midi-music) (`midis_2.zip`) | blitzdoughnuts | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |
| `musicbox1_spooky_waltz.mid` | 53 s, reverb and chorus sends (CC91, CC93) and Reset All Controllers | [4 Music Box Tracks](https://opengameart.org/content/4-music-box-tracks) (`4_music_box_tracks_mid.zip`) | Aureolus_Omicron | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) |

None of them sends GM System On or moves the pitch wheel. The gate's own
songs cover those.

## gate/

`bends.mid` and `resets.mid`, with the `.json` of what the gate measures in
each, are written by [`tools/gm_songs.py`](../../tools/gm_songs.py); the gate
writes them afresh on every run. These copies are for reference and for
playing in a DAW. They're meant for the sine bank `gm_songs.py` also writes,
so a pitch or a level reads straight off the WAV, but they play on any GM bank.
They are part of this repository and share its licence.
