"""The song both of studio's groovebox applications edit, and what carries it
to a front panel.

- :mod:`songlink.model`: the project, plain JSON data changed only by ops that
  every side applies the same way. Imports on CPython, Pyodide and
  MicroPython.
- :mod:`songlink.link`: the project as an ``hmilink`` model, so any screen can
  be a front panel (needs ``hmilink``, from pydevices).
- ``page.html``: the browser front panel a device serves on its hmilink port.

Ostinato (``apps/ostinato``) and Vamp (``lib/vamp``) both use it.
"""



def page():
    """The path of ``page.html``, beside this file."""
    here = __file__
    for sep in ("/", "\\"):
        if sep in here:
            return here.rsplit(sep, 1)[0] + sep + "page.html"
    return "page.html"
