"""A knob: drag up or down to turn it; its name under it, its value inside.

Dragging is vertical, as in most music software: up turns it up, one step
every couple of pixels, or finer with Shift held. A touch doesn't move it
(an arc that follows the pointer's angle jumped to wherever you pressed).
A double-click puts it back to the patch's value.
"""

import lvgl as lv

import theme
from theme import px


class Knob:
    def __init__(self, parent, x, y, size, name, colour, on_change, bipolar=False, default=None, app=None):
        self.on_change = on_change
        self.default = default
        self.app = app
        import fonts

        lh = fonts.t(px(14)).line_height
        self.root = theme.box(parent, x, y, size, size + 2 * lh)
        self.root.add_flag(lv.OBJ_FLAG.CLICKABLE)
        self.arc = lv.arc(self.root)
        self.arc.set_size(size - px(8), size - px(8))
        self.arc.set_pos(px(4), 0)
        self.arc.set_range(0, 127)
        self.arc.set_bg_angles(135, 45)
        if bipolar:
            self.arc.set_mode(lv.arc.MODE.SYMMETRICAL)
        self.arc.remove_flag(lv.OBJ_FLAG.CLICKABLE)      # the knob's box takes the drag
        self.arc.set_style_arc_width(px(6), lv.PART.MAIN)
        self.arc.set_style_arc_width(px(6), lv.PART.INDICATOR)
        self.arc.set_style_arc_color(theme.c(theme.LINE), lv.PART.MAIN)
        self.arc.set_style_arc_color(theme.c(colour), lv.PART.INDICATOR)
        self.arc.set_style_bg_color(theme.c(theme.TEXT), lv.PART.KNOB)
        self.arc.set_style_pad_all(px(3), lv.PART.KNOB)
        # The ring a few pixels inside the arc's box: the handle sits on the
        # ring, and at the top (the centre position) it would otherwise reach
        # past the box and be cut flat.
        self.arc.set_style_pad_all(px(5), lv.PART.MAIN)
        self.value = theme.label(self.arc, "", "muted")
        self.value.center()
        # The name, on two lines if it needs them.
        self.name = theme.label(self.root, name, "muted")
        self.name.set_width(size)
        self.name.set_height(2 * lh)
        self.name.set_long_mode(lv.label.LONG_MODE.WRAP)
        self.name.set_style_text_align(lv.TEXT_ALIGN.CENTER, 0)
        self.name.set_pos(0, size - px(6))
        self.on_touch = None
        self.drag = None
        self.root.add_event_cb(lambda e: self._pressed(), lv.EVENT.PRESSED, None)
        self.root.add_event_cb(lambda e: self._pressing(), lv.EVENT.PRESSING, None)
        self.root.add_event_cb(lambda e: self._released(), lv.EVENT.RELEASED, None)
        self.root.add_event_cb(lambda e: self.reset(), lv.EVENT.DOUBLE_CLICKED, None)

    def _y(self):
        pt = lv.point_t()
        lv.indev_active().get_point(pt)
        return pt.y

    def _pressed(self):
        self.drag = {"y": self._y(), "v": self.arc.get_value()}
        if self.on_touch:
            self.on_touch(self)

    def _pressing(self):
        d = self.drag
        if d is None:
            return
        fine = self.app is not None and self.app.shift
        step = px(8) if fine else max(1, px(2))
        v = max(0, min(127, d["v"] + (d["y"] - self._y()) // step))
        if v != self.arc.get_value():
            self.set(v)
            self.on_change(v)

    def _released(self):
        self.drag = None

    def reset(self):
        """Back to the patch's value (a double-click)."""
        if self.default is not None and int(self.default) != self.arc.get_value():
            self.set(self.default)
            self.on_change(int(self.default))

    def set(self, v):
        self.arc.set_value(int(v))
        self.value.set_text(str(int(v)))

    def armed(self, on):
        self.root.set_style_border_width(px(2) if on else 0, 0)
        self.root.set_style_border_color(theme.c(theme.ACCENT), 0)
        self.root.set_style_radius(px(8), 0)

    def bound(self, on):
        """A small dot by the name: a keyboard control is learned to this knob."""
        if on and getattr(self, "dot", None) is None:
            self.dot = theme.box(self.root, px(2), 0, px(8), px(8), theme.ACCENT, px(4))
            self.dot.remove_flag(lv.OBJ_FLAG.CLICKABLE)
