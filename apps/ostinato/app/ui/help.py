"""The guide: how to play Ostinato, inside the app (it never links out).

Shown once on the first run, and any time from the ? button.
"""

import lvgl as lv

import theme
from theme import px

PAGES = [
    ("Play", [
        "Press keys on your MIDI keyboard (or the one on screen) to play the selected track.",
        "Click a track on the left to play it instead. The pads play the Drums track's rows, pad 1 the top one.",
        "The DMK-25's knobs turn the first twelve knobs on the right; its sliders set volumes (bank A) and pans (B).",
        "Its Rewind goes to the top, Forward to the next scene, Loop repeats the scene that's playing.",
    ]),
    ("Patterns", [
        "A to H are each track's eight patterns; Shift-click picks one for every track. While playing, it changes at the next bar.",
        "Drums: click a square for a hit, again for an accent, again to clear it. Click a name to hear it.",
        "Other tracks: click to add a note, drag to move or lengthen it. Shift-drag a note or a hit: velocity.",
        "Copy puts this track's pattern in its next empty slot. Delete removes the selected note.",
    ]),
    ("Record", [
        "Pick a track, press record, then play: a bar of clicks counts you in, then the pattern loops.",
        "Beside record: the grid button snaps notes to sixteenths; the layers button overdubs (off: a take replaces).",
        "Stop ends the take; undo removes it in one step. In a song, takes go to the scene that's playing.",
        "The keyboard's Rec, Play and Stop buttons work too.",
    ]),
    ("Song", [
        "Song turns patterns into an arrangement: each row is a scene.",
        "Click a track's letter to choose its pattern; hold it to edit that pattern as the song plays.",
        "A scene's number plays from there; its buttons loop it, duplicate it, or remove it.",
        "Drag the tempo or swing value, hold + or -, or tap the tempo in time to set it.",
    ]),
    ("Sound", [
        "Choose an instrument (by family, or step with the arrows) and a patch; drag a knob up or down, Shift for fine.",
        "Double-click a knob to put it back. Each track has a pan slider above its volume.",
        "Effects go under the knobs; click one to turn its knobs, move it with its arrows, bypass it with power.",
        "Learn: switch it on, touch a knob or slider, move a control on the keyboard. Master: effects on the whole mix.",
    ]),
    ("Files", [
        "Click Ostinato (top left) for New, Demo song, Open, Save, Save as, and export to WAV or MIDI.",
        "Ctrl+S saves to the same file after the first time. The bottom bar says when there are unsaved changes.",
        "Your work is also saved as you go. Ostinato asks before New or Open throws away unsaved changes.",
        "Space plays and stops, R arms recording, Ctrl+Z and Ctrl+Y undo and redo.",
    ]),
]


class Help:
    def __init__(self, app):
        self.app = app
        self.box = None
        self.page = 0

    def toggle(self, first_run=False):
        if self.box is not None:
            self.close()
        else:
            self.open(first_run)

    def close(self):
        if self.box is not None:
            self.box.delete()
            self.box = None

    def open(self, first_run=False):
        scr = self.app.screen
        w, h = px(900), px(560)
        sw, sh = scr.get_width(), scr.get_height()
        shade = theme.box(scr, 0, 0, sw, sh, 0x000000)
        shade.set_style_bg_opa(150, 0)
        shade.add_flag(lv.OBJ_FLAG.CLICKABLE)
        self.box = shade
        card = theme.box(shade, (sw - w) // 2, (sh - h) // 2, w, h, theme.PANEL, px(14))
        card.set_style_border_width(1, 0)
        card.set_style_border_color(theme.c(theme.LINE), 0)
        title = theme.label(card, "Welcome to Ostinato" if first_run else "How to play", "big", theme.ACCENT)
        title.set_pos(px(32), px(24))
        close = theme.button(card, "Start playing" if first_run else "Close", w=px(170), h=px(42))
        close.obj.align(lv.ALIGN.BOTTOM_RIGHT, -px(24), -px(20))
        close.on(lv.EVENT.CLICKED, self.close)
        self.tabs = []
        tx = px(32)
        for i, (name, _) in enumerate(PAGES):
            b = theme.button(card, name, w=px(118), h=px(36), checkable=True)
            b.obj.set_pos(tx + i * px(126), px(84))
            b.on(lv.EVENT.CLICKED, (lambda k: lambda: self.show(k))(i))
            self.tabs.append(b)
        self.body_w = w - px(64)
        self.body = theme.box(card, px(32), px(140), self.body_w, h - px(220))
        self.show(self.page)

    def show(self, index):
        self.page = index
        for i, b in enumerate(self.tabs):
            b.checked = i == index
        self.body.clean()
        y = 0
        for line in PAGES[index][1]:
            dot = theme.label(self.body, "-", "text", theme.ACCENT)
            dot.set_pos(0, y)
            lb = theme.label(self.body, line, "text")
            lb.set_width(self.body_w - px(28))
            lb.set_long_mode(lv.label.LONG_MODE.WRAP)
            lb.set_pos(px(24), y)
            lb.update_layout()
            y += max(px(30), lb.get_height() + px(14))
