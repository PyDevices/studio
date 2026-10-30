"""Tooltips: what an icon-only button does, shown when the mouse rests on it.

LVGL hovers a released pointer (HOVER_OVER, HOVER_LEAVE) when its input
driver reports the mouse with the button up, which pydevices' display_driver
now does. After DELAY_MS over a control, a small label appears beside it; it
goes as soon as the pointer leaves or presses. On a touch screen there is no
hover, so nothing appears and nothing gets in the way.
"""

import lvgl as lv

import theme
from theme import px

DELAY_MS = 500


class Tips:
    def __init__(self, screen):
        self.screen = screen
        self.bubble = None
        self.timer = None            # kept, or the collector can stop it before it fires
        self.pending = None
        self.text = None             # what's showing, for tests

    def add(self, obj, text):
        obj.add_event_cb(lambda e: self._over(obj, text), lv.EVENT.HOVER_OVER, None)
        obj.add_event_cb(lambda e: self.hide(), lv.EVENT.HOVER_LEAVE, None)
        obj.add_event_cb(lambda e: self.hide(), lv.EVENT.PRESSED, None)
        return obj

    def _over(self, obj, text):
        self.hide()
        self.pending = (obj, text)
        self.timer = lv.timer_create(lambda t: self._due(), DELAY_MS, None)
        self.timer.set_repeat_count(1)

    def _due(self):
        if self.pending is not None:
            obj, text = self.pending
            self.show(obj, text)
        self.timer = None

    def show(self, obj, text):
        """The label under ``obj`` (above it, near the bottom), kept on screen."""
        self.hide_bubble()
        top = lv.layer_top()
        b = lv.obj(top)
        b.remove_style_all()
        b.set_style_bg_color(theme.c(theme.TEXT), 0)
        b.set_style_bg_opa(240, 0)
        b.set_style_radius(px(6), 0)
        b.set_style_pad_hor(px(10), 0)
        b.set_style_pad_ver(px(6), 0)
        b.remove_flag(lv.OBJ_FLAG.CLICKABLE)
        b.set_size(lv.SIZE_CONTENT, lv.SIZE_CONTENT)
        lb = theme.label(b, text, "text", theme.BG)
        b.update_layout()
        a = lv.area_t()
        obj.get_coords(a)
        w, h = b.get_width(), b.get_height()
        sw, sh = self.screen.get_width(), self.screen.get_height()
        x = (a.x1 + a.x2) // 2 - w // 2
        y = a.y2 + px(8)
        if y + h > sh - px(4):
            y = a.y1 - h - px(8)
        x = max(px(4), min(sw - w - px(4), x))
        b.set_pos(x, y)
        self.bubble = b
        self.text = text

    def hide_bubble(self):
        if self.bubble is not None:
            self.bubble.delete()
            self.bubble = None
        self.text = None

    def hide(self):
        self.pending = None
        self.hide_bubble()
