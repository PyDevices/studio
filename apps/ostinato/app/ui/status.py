"""The status bar: the project's name and whether it's saved, a word when the
audio drops out, and on the right either a message or the last MIDI."""

import lvgl as lv

import theme
from theme import px


class StatusBar:
    def __init__(self, parent, rect):
        self.root = theme.panel(parent, rect)
        self.left = theme.label(self.root, "", "muted")
        self.left.align(lv.ALIGN.LEFT_MID, px(16), 0)
        self.alert = theme.label(self.root, "", "muted", theme.REC)
        self.right = theme.label(self.root, "", "muted")
        self.right.align(lv.ALIGN.RIGHT_MID, -px(16), 0)
        self.shown = None

    def show(self, name, unsaved, dropped, extra="", message=False, loads=None):
        left = name + ("   unsaved changes" if unsaved else "")
        if loads is not None:                 # ?debug: the engines' load too
            left += "      engines " + "  ".join("%d%%" % round(l * 100) if l is not None else "--" for l in loads)
        state = (left, dropped, extra, message)
        if state == self.shown:
            return
        self.shown = state
        self.left.set_text(left)
        self.left.update_layout()
        self.alert.set_text("Audio dropped out" if dropped else "")
        self.alert.set_pos(px(16) + self.left.get_width() + px(24), 0)
        self.alert.align(lv.ALIGN.LEFT_MID, px(16) + self.left.get_width() + px(24), 0)
        self.right.set_text(extra)
        # A message reads in the text colour; the MIDI readout stays muted.
        self.right.set_style_text_color(theme.c(theme.TEXT if message else theme.MUTED), 0)
