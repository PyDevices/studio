"""Fonts: Montserrat from lvgl-bindings, Material icons from the app.

Text fonts come from lvgl-bindings' ``fonts/`` folder, at the tag pinned for
the running LVGL's major.minor (the same rule as pydevices-examples'
lv_dynamic_fonts): the .bin format follows LVGL's version. The service
worker caches them after the first visit, so they load offline too (the
desktop app keeps its own copy: desktop/ui/page.py). Until they arrive,
LVGL's built-in Montserrat stands in.
"""

import lvgl as lv

import icons
import page

FONT_REFS = {(9, 5): "v9.5.22"}
TEXT_URL = "https://raw.githubusercontent.com/PyDevices/lvgl-bindings/%s/fonts/montserrat_%d.bin"
ICON_URL = "assets/icons_%d.bin"
TEXT_SIZES = (12, 14, 16, 20, 24, 28, 36, 48)

_keep = []          # font buffers must outlive the fonts made from them
text = {}           # size -> font
icon = {}           # size -> font


def _fallback(size):
    for s in (16, 14):
        f = getattr(lv, "font_montserrat_%d" % s, None)
        if f is not None and s <= size:
            return f
    return lv.font_montserrat_14


async def _load(url):
    data = await page.fetch_bytes(url)
    _keep.append(data)
    return lv.binfont_create_from_buffer(data, len(data))


async def load(base):
    """Fetch every font; returns a list of what failed (empty when all loaded)."""
    failed = []
    ref = FONT_REFS.get((lv.version_major(), lv.version_minor()))
    for size in TEXT_SIZES:
        text[size] = _fallback(size)
        if ref:
            try:
                text[size] = await _load(TEXT_URL % (ref, size))
            except Exception as e:
                failed.append(str(e))
    for size in icons.SIZES:
        try:
            icon[size] = await _load(base + ICON_URL % size)
            # Letters in an icon label (an icon and its caption) come from
            # the text font of the same size.
            icon[size].fallback = t(int(size * 0.7))
        except Exception as e:
            failed.append(str(e))
            icon[size] = _fallback(size)
    return failed


def t(size):
    """The text font nearest ``size`` (rounded down)."""
    best = TEXT_SIZES[0]
    for s in TEXT_SIZES:
        if s <= size:
            best = s
    return text.get(best) or _fallback(size)


def i(size):
    best = icons.SIZES[0]
    for s in icons.SIZES:
        if s <= size:
            best = s
    return icon.get(best) or _fallback(size)
