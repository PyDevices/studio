"""A stereo segment meter: left and right columns of lit segments.

Levels arrive about 30 times a second as peak dB since the last reading.
The meter falls at FALL_DB a second rather than jumping, and the highest
segment reached stays lit for HOLD_S, the way a hardware meter shows a peak.
Only segments that change are restyled, so a still meter costs nothing.
"""

import time

import lvgl as lv

import theme
from theme import px

# Each segment lights at its dB, bottom to top.
STEPS = (-54, -48, -42, -36, -30, -24, -18, -12, -9, -6, -3, 0)
GREEN, YELLOW, RED = 0x3FBF6A, 0xE8C547, 0xE5484D
FALL_DB = 24.0
HOLD_S = 1.0
DIM = 45                     # an unlit segment's opacity


def colour(db):
    return RED if db >= 0 else (YELLOW if db >= -6 else GREEN)


class StereoMeter:
    def __init__(self, parent, x, y, w, h):
        self.root = theme.box(parent, x, y, w, h)
        gap = max(1, px(2))
        col_w = (w - gap) // 2
        seg_h = (h - gap * (len(STEPS) - 1)) / len(STEPS)
        self.columns = []
        for c in range(2):
            segs = []
            for i, db in enumerate(STEPS):
                top = round(h - (i + 1) * seg_h - i * gap)
                seg = theme.box(self.root, c * (col_w + gap), top, col_w, max(1, round(seg_h)), colour(db), max(1, px(1)))
                seg.set_style_bg_opa(DIM, 0)
                segs.append(seg)
            self.columns.append({"segs": segs, "lit": 0, "level": -100.0, "hold": -100.0, "hold_until": 0.0, "shown_hold": -1})
        self.last = time.monotonic()

    def show(self, levels):
        """``levels``: [left, right] peak dB since the last call, or None for no news."""
        now = time.monotonic()
        dt = now - self.last
        self.last = now
        for c, col in enumerate(self.columns):
            new = levels[c] if levels else -100.0
            col["level"] = max(new, col["level"] - FALL_DB * dt)
            if col["level"] >= col["hold"] or now > col["hold_until"]:
                col["hold"] = col["level"]
                col["hold_until"] = now + HOLD_S
            lit = sum(1 for db in STEPS if col["level"] >= db)
            hold = max((i for i, db in enumerate(STEPS) if col["hold"] >= db), default=-1)
            if lit != col["lit"] or hold != col["shown_hold"]:
                for i, seg in enumerate(col["segs"]):
                    on = i < lit or i == hold
                    was = i < col["lit"] or i == col["shown_hold"]
                    if on != was:
                        seg.set_style_bg_opa(255 if on else DIM, 0)
                col["lit"] = lit
                col["shown_hold"] = hold
