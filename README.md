# studio

Music applications built on the PyDevices audio and MIDI stack. Each one runs
on CPython, MicroPython and CircuitPython, on the desktop and on boards.

They're built from libraries kept elsewhere: `midiports` (in `pydevices`) for
MIDI transports, Adafruit's `adafruit_midi` for MIDI messages, `audiodsp` for
the DSP kernels, and `audiocomponents` for the instruments and effects. When two applications
need the same piece, that piece moves down into one of those libraries.

Nothing here is settled yet: names, APIs and layout may all change.
