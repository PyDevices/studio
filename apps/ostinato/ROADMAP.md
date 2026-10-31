# Roadmap

- A layout for 1280x720 and one for phones: the layout is a table of
  regions, so each is a new table.
- Resizing without a reload: today a resize saves and reloads at the new size.
- Filter sweeps over held notes, once the instruments' macros reach
  sounding notes.
- More than four tracks: each track is an engine worker, and the audio thread
  asks every stream for audio each quantum, so it's measured before it grows.
- Time signatures other than 4/4: a bar is four beats everywhere, so a 7/4
  riff or a 6/8 bridge can't be written.
- Knob changes per scene: a scene picks patterns only, so the organ's Leslie
  can't speed up for the chorus and a pad can't swell between sections.
- Pitch bend in patterns: a note is start, length, pitch and velocity, so a
  lead can glide between notes but can't bend one.
- Feel per track: swing is one number for the song, on off-beat sixteenths, so
  hats that push while the snare lays back need a per-track swing or nudge.
- SoundFont patch names from the bank you play: the catalog is static, so the
  names it shows follow the bank it was made with (FluidR3_GM), not the `.sf2`
  on your machine; `scripts/make_soundfont_catalog.py` remakes it from yours.
- A song other screens can edit while it plays: `--split` puts the song in the
  engines' process and lets a browser edit it beside the window. Next: more of
  the app on the page, undo that knows about the other screen, and a phone app's OSC.
