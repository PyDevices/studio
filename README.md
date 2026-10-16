# studio

Music applications built on the PyDevices audio and MIDI stack. Each one runs
on CPython, MicroPython and CircuitPython, on the desktop and on boards.

The libraries they're built from live in their own repos: `midiports` for MIDI
transports, `adafruit_midi` for MIDI messages, `audiodsp` for the DSP kernels,
and `audiocomponents` for the instruments and effects. When two applications
need the same piece, that piece moves down into one of those libraries.

Nothing here is settled yet: names, APIs and layout may all change.
