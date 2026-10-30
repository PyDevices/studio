"""P0: the stress screen with LVGL in its own worker, drawing to an OffscreenCanvas.

Same widgets and the same stress levels as page.py, but nothing here runs on
the page's main thread, which is left with Web MIDI and input only. ``blit``
is a JS function that converts RGB565 and puts it on the OffscreenCanvas.
"""

import time

import lvgl as lv

W = H = 0
blit = None
disp = None
cells = []
stress = 0
step = 0
pass_ms = []
tick_ms = []
_last = None


def setup(width, height, blit_fn):
    global W, H, blit, disp
    W, H, blit = width, height, blit_fn
    lv.init()
    disp = lv.display_create(W, H)
    disp.set_color_format(lv.COLOR_FORMAT.RGB565)
    b1 = lv.draw_buf_create(W, H // 10, lv.COLOR_FORMAT.RGB565, 0)
    b2 = lv.draw_buf_create(W, H // 10, lv.COLOR_FORMAT.RGB565, 0)
    globals()["_bufs"] = (b1, b2)   # keep the draw buffers alive
    disp.set_flush_cb(_flush)
    disp.set_draw_buffers(b1, b2)
    disp.set_render_mode(lv.DISPLAY_RENDER_MODE.PARTIAL)
    scr = lv.screen_active()
    scr.set_style_bg_color(lv.color_hex(0x101418), 0)
    cols, rows = 16, 12
    cw, ch = W // cols, H // rows
    for r in range(rows):
        for c in range(cols):
            box = lv.obj(scr)
            box.set_size(cw - 4, ch - 4)
            box.set_pos(c * cw + 2, r * ch + 2)
            bar = lv.bar(box)
            bar.set_size(cw - 24, 10)
            bar.align(lv.ALIGN.BOTTOM_MID, 0, 0)
            label = lv.label(box)
            label.align(lv.ALIGN.TOP_LEFT, 0, 0)
            cells.append((bar, label))
    lv.timer_create(_tick, 16, None)


def _flush(d, area, color_p):
    w = area.x2 - area.x1 + 1
    h = area.y2 - area.y1 + 1
    blit(color_p.__dereference__(w * h * 2), area.x1, area.y1, w, h)
    disp.flush_ready()


def _tick(_t):
    global step
    if not stress:
        return
    t = time.perf_counter()
    step += 1
    for i, (bar, label) in enumerate(cells):
        v = (step * 3 + i * 7) % 100
        bar.set_value(v, 0)  # lv_anim_enable_t is a bool: 0 = no animation
        label.set_text(str(v))
    tick_ms.append((time.perf_counter() - t) * 1000)


def run():
    """One LVGL pass; returns ms until LVGL wants the next one."""
    global _last
    now = time.perf_counter()
    if _last is not None:
        lv.tick_inc(int((now - _last) * 1000))
    _last = now
    nxt = lv.timer_handler()
    pass_ms.append((time.perf_counter() - now) * 1000)
    if len(pass_ms) > 4000:
        del pass_ms[:2000]
    return 1 if stress == 2 else max(1, min(16, nxt if nxt is not None and nxt >= 0 else 16))


def take():
    global tick_ms
    got = (step, list(pass_ms[-2000:]), tick_ms)
    tick_ms = []
    return got
