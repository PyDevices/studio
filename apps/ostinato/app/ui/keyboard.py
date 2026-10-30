"""The on-screen keyboard: shows what's playing, plays the selected track."""

import lvgl as lv

import theme
from theme import px

WHITE = (0, 2, 4, 5, 7, 9, 11)
BLACK = {1: 0, 3: 1, 6: 3, 8: 4, 10: 5}   # pitch class -> white key it sits after


class Keyboard:
    """Four octaves; the buttons at its ends move it an octave, and each C is labelled."""

    def __init__(self, parent, rect, app, low=36, octaves=4):
        self.app = app
        self.low = low
        self.octaves = octaves
        self.rect = rect
        self.root = theme.panel(parent, rect)
        self.build()

    def shift(self, octaves):
        self.low = max(12, min(84, self.low + 12 * octaves))
        self.build()
        self.app.say("On-screen keys: C%d to C%d" % (self.low // 12 - 1, (self.low + 12 * self.octaves) // 12 - 1), 2.0)

    def build(self):
        self.root.clean()
        x, y, w, h = self.rect
        low, octaves = self.low, self.octaves
        pad = px(10)
        bw = px(40)
        self.shift_buttons = []
        for k, (icon, d, tip) in enumerate((("chevron_left", -1, "Keys an octave down"),
                                             ("chevron_right", 1, "Keys an octave up"))):
            b = theme.button(self.root, icon=icon, w=bw, h=h - 2 * pad)
            b.obj.set_pos(pad if k == 0 else w - pad - bw, pad)
            b.on(lv.EVENT.CLICKED, (lambda dd: lambda: self.shift(dd))(d))
            self.app.tips.add(b.obj, tip)
            self.shift_buttons.append(b.obj)
        left = pad + bw + px(8)
        whites = octaves * 7
        kw = (w - 2 * left) // whites
        kh = h - 2 * pad
        self.keys = {}
        for o in range(octaves):
            for i, pc in enumerate(WHITE):
                pitch = low + o * 12 + pc
                k = self._key(left + (o * 7 + i) * kw, pad, kw - px(2), kh, 0xE8EAEE, pitch)
                self.keys[pitch] = (k, 0xE8EAEE)
                if pc == 0:
                    lb = theme.label(k, "C%d" % (pitch // 12 - 1), "muted", 0x5A6070)
                    lb.align(lv.ALIGN.BOTTOM_MID, 0, -px(6))
        for o in range(octaves):
            for pc, after in BLACK.items():
                pitch = low + o * 12 + pc
                bx = left + (o * 7 + after + 1) * kw - int(kw * 0.32)
                k = self._key(bx, pad, int(kw * 0.62), int(kh * 0.6), 0x20242C, pitch)
                self.keys[pitch] = (k, 0x20242C)

    def _key(self, x, y, w, h, colour, pitch):
        k = theme.box(self.root, x, y, w, h, colour, px(4))
        k.add_flag(lv.OBJ_FLAG.CLICKABLE)
        k.add_event_cb(lambda e: self.app.audition(pitch, 100), lv.EVENT.PRESSED, None)
        k.add_event_cb(lambda e: self.app.audition(pitch, 0), lv.EVENT.RELEASED, None)
        return k

    def show(self, pitch, on):
        key = self.keys.get(pitch)
        if key:
            k, colour = key
            k.set_style_bg_color(theme.c(theme.ACCENT if on else colour), 0)
