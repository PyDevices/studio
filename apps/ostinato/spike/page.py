"""P0 stress screen: full-window LVGL that redraws as hard as it's told to.

``window.__stress``: 0 idle, 1 every widget changes every 16 ms frame,
2 the same with LVGL's timer at 1 ms (as fast as the page can go).
The page counts frames in ``window.__pageFrames`` and the time each
frame's Python took in ``window.__pageFrameMs`` (the last 2000).
"""

from boarddev import env_set
from pyscript import document, window

W, H = int(window.innerWidth), int(window.innerHeight)
env_set("PYDEVICES_WIDTH", W)
env_set("PYDEVICES_HEIGHT", H)

import time

import appdev
import board_config

# P0: PSDisplay.blit_rect writes ImageData one pixel at a time through the
# JS bridge (4 FFI calls a pixel, ~2 s for a 1280x720 frame). Convert the
# RGB565 buffer in one JS call instead. The fix goes upstream in P1.
from displaydev import psdisplay
from pyodide.ffi import to_js
import js

_blit565 = js.Function.new("ctx", "src", "x", "y", "w", "h", '''
  const img = ctx.createImageData(w, h);
  const out = new Uint32Array(img.data.buffer);
  for (let i = 0, j = 0, n = w * h; j < n; i += 2, j++) {
    const v = src[i] | (src[i + 1] << 8);
    const r = (v >> 8) & 0xF8, g = (v >> 3) & 0xFC, b = (v << 3) & 0xF8;
    out[j] = 0xFF000000 | ((b | (b >> 5)) << 16) | ((g | (g >> 6)) << 8) | (r | (r >> 5));
  }
  ctx.putImageData(img, x, y);
''')


def _fast_blit(self, buf, x, y, w, h):
    if x < 0 or y < 0 or x + w > self.width or y + h > self.height:
        raise ValueError("The provided x, y, w, h values are out of range")
    if len(buf) != w * h * 2:
        raise ValueError("The source buffer is not the correct size")
    _blit565(self._buf_ctx, to_js(memoryview(buf)), x, y, w, h)
    self.render((x, y, w, h))
    return (x, y, w, h)


psdisplay.PSDisplay.blit_rect = _fast_blit

app = appdev.App.current() or appdev.App(board_config)

import display_driver  # noqa: F401  (wires LVGL to the canvas and the loop)
import lvgl as lv

# Time every LVGL pass (render + flush to the canvas): the page's real frame cost.
_handler = lv.timer_handler
pass_ms = []


def _timed_handler():
    t = time.perf_counter()
    r = _handler()
    pass_ms.append((time.perf_counter() - t) * 1000)
    if len(pass_ms) > 4000:
        del pass_ms[:2000]
    return r


lv.timer_handler = _timed_handler

scr = lv.screen_active()
scr.set_style_bg_color(lv.color_hex(0x101418), 0)
COLS, ROWS = 16, 12
cw, ch = W // COLS, H // ROWS
cells = []
for r in range(ROWS):
    for c in range(COLS):
        box = lv.obj(scr)
        box.set_size(cw - 4, ch - 4)
        box.set_pos(c * cw + 2, r * ch + 2)
        bar = lv.bar(box)
        bar.set_size(cw - 24, 10)
        bar.align(lv.ALIGN.BOTTOM_MID, 0, 0)
        label = lv.label(box)
        label.align(lv.ALIGN.TOP_LEFT, 0, 0)
        cells.append((bar, label))

window.__stress = 0
window.__pageFrames = 0
frame_ms = []
step = 0
fast = None


def tick(_t):
    try:
        _tick()
    except Exception as e:  # LVGL swallows callback errors; say them
        import traceback
        print('tick failed:', ''.join(traceback.format_exception(e)))
        window.console.error('tick failed: ' + str(e))


def _tick():
    global step, fast
    level = int(window.__stress)
    if level == 2 and fast is None:
        fast = lv.timer_create(tick, 1, None)
    elif level != 2 and fast is not None:
        fast.delete()
        fast = None
    if not level:
        return
    t = time.perf_counter()
    step += 1
    for i, (bar, label) in enumerate(cells):
        v = (step * 3 + i * 7) % 100
        bar.set_value(v, 0)  # lv_anim_enable_t is a bool: 0 = no animation
        label.set_text(str(v))
    frame_ms.append((time.perf_counter() - t) * 1000)
    if len(frame_ms) > 2000:
        del frame_ms[:1000]
    window.__pageFrames = step
    window.__pageFrameMs = ",".join("%.2f" % x for x in frame_ms[-2000:])
    window.__lvPassMs = ",".join("%.1f" % x for x in pass_ms[-2000:])


_heartbeat = lv.timer_create(tick, 16, None)
window.console.log('tick timer created')
document.querySelector("#status").textContent = "page ready %dx%d" % (W, H)
window.__pageReady = True
app.run()
