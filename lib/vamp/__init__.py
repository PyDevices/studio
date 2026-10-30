"""Vamp: a headless groovebox. One MicroPython holds the song, runs the
sequencer and makes the sound; any screen in the room is its front panel.

- :mod:`vamp.engine`: the sound, every SoundFont track on one sampler, with
  notes sequenced ahead so they land on their exact frame.
- :mod:`vamp.device`: the song as an hmilink device, which Ostinato's window
  and the browser page drive over TCP.
- :mod:`vamp.main`: the program, on a board or desktop MicroPython.

The song is :mod:`songlink.model`'s, the same one Ostinato edits.
"""


def run(*args):
    """Start the box, with ``vamp.main``'s options as strings."""
    from vamp import main

    return main.main(list(args))
