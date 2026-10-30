"""The top bar: transport, position, tempo, swing, metronome, patterns."""

import lvgl as lv

import fonts
from songlink import model
import theme
from theme import px

PATTERN_SLOTS = 8


def swing_percent(swing):
    """The project's swing (how far off-beat sixteenths are late, in percent
    of a sixteenth) as drum machines show it: the share of each pair of
    sixteenths the first one takes. 50% is straight, 66% a triplet feel."""
    return round(50 + swing / 2)


class TransportBar:
    def __init__(self, parent, rect, app):
        self.app = app
        x, y, w, h = rect
        self.root = theme.panel(parent, rect)
        bh = h - px(20)
        cy = (h - bh) // 2

        self.file = theme.button(self.root, "Ostinato", icon="expand_more", w=px(140), h=bh)
        self.file.obj.set_pos(px(12), cy)
        self.file.obj.set_style_text_color(theme.c(theme.ACCENT), 0)
        self.file.on(lv.EVENT.CLICKED, lambda: app.files.toggle_menu(self.file.obj))

        at = px(170)
        self.play = theme.button(self.root, icon="play_arrow", w=bh + px(14), h=bh)
        self.play.obj.set_pos(at, cy)
        self.play.on(lv.EVENT.CLICKED, app.toggle_play)
        app.tips.add(self.play.obj, "Play / stop  (Space)")
        at += bh + px(22)
        self.stop = theme.button(self.root, icon="stop", w=bh + px(14), h=bh)
        self.stop.obj.set_pos(at, cy)
        self.stop.on(lv.EVENT.CLICKED, app.stop)
        app.tips.add(self.stop.obj, "Stop, back to the start")
        at += bh + px(22)
        self.rec = theme.button(self.root, icon="fiber_manual_record", w=bh + px(14), h=bh, checkable=True)
        self.rec.obj.set_pos(at, cy)
        self.rec.label.set_style_text_color(theme.c(theme.REC), 0)
        # Armed, it's red with a white dot, not the orange every other
        # checked button (and Play) uses; it pulses while recording.
        self.rec.obj.set_style_bg_color(theme.c(theme.REC), lv.STATE.CHECKED)
        self.rec.obj.set_style_bg_color(theme.c(0xEF6A6E), lv.STATE.CHECKED | lv.STATE.HOVERED)

        self.rec.on(lv.EVENT.CLICKED, app.toggle_record)
        app.tips.add(self.rec.obj, "Record  (R): arm, then Play counts in a bar")
        at += bh + px(18)
        # Quantise: recorded notes snap to sixteenths (on) or keep the timing played.
        small = bh - px(6)
        self.quant = theme.button(self.root, icon="grid_on", w=small, h=bh, checkable=True)
        self.quant.obj.set_pos(at, cy)
        self.quant.on(lv.EVENT.CLICKED, app.toggle_quantise)
        app.tips.add(self.quant.obj, "Quantise recording to sixteenths")
        at += small + px(6)
        # Overdub: a take adds to the pattern (on) or replaces it (off).
        self.overdub = theme.button(self.root, icon="layers", w=small, h=bh, checkable=True)
        self.overdub.obj.set_pos(at, cy)
        self.overdub.on(lv.EVENT.CLICKED, app.toggle_overdub)
        app.tips.add(self.overdub.obj, "Overdub: takes add to the pattern (off: a take replaces it)")
        at += small + px(20)

        self.position = theme.label(self.root, "1 . 1 . 1", "big")
        self.position.set_pos(at, (h - px(34)) // 2)
        self.position.set_width(px(160))
        at += px(170)

        self.tempo = self.stepper(at, cy, px(178), bh, app.nudge_tempo, tap=app.tap_tempo,
                                  tip="Tempo: drag up or down, or tap in time")
        at += px(188)
        self.swing = self.stepper(at, cy, px(200), bh, app.nudge_swing,
                                  tip="Swing: 50% is straight; drag up or down")
        at += px(210)

        self.metronome = theme.button(self.root, icon="timer", w=bh + px(14), h=bh, checkable=True)
        self.metronome.obj.set_pos(at, cy)
        self.metronome.on(lv.EVENT.CLICKED, app.toggle_metronome)
        app.tips.add(self.metronome.obj, "Metronome")
        at += bh + px(30)

        theme.label(self.root, "Pattern", "muted").set_pos(at, (h - px(18)) // 2)
        at += px(68)
        self.slots = []
        self.dots = []
        for i in range(PATTERN_SLOTS):
            b = theme.button(self.root, "ABCDEFGH"[i], w=px(40), h=bh, checkable=True)
            b.obj.set_pos(at, cy)
            b.on(lv.EVENT.CLICKED, (lambda k: lambda: app.choose_pattern(k))(i))
            app.tips.add(b.obj, "Pattern %s for this track  (Shift: every track)" % "ABCDEFGH"[i])
            # A dot under the letter when this track's pattern has notes.
            dot = theme.box(b.obj, (px(40) - px(6)) // 2, bh - px(9), px(6), px(6), theme.TEXT, px(3))
            dot.remove_flag(lv.OBJ_FLAG.CLICKABLE)
            dot.add_flag(lv.OBJ_FLAG.HIDDEN)
            self.dots.append(dot)
            self.slots.append(b)
            at += px(45)

        at += px(14)
        self.song = theme.button(self.root, "Song", icon="queue_music", w=px(120), h=bh, checkable=True)
        self.song.obj.set_pos(at, cy)
        self.song.on(lv.EVENT.CLICKED, app.toggle_song)
        at += px(134)
        self.undo = theme.button(self.root, icon="undo", w=bh, h=bh)
        self.undo.obj.set_pos(at, cy)
        self.undo.on(lv.EVENT.CLICKED, app.undo)
        app.tips.add(self.undo.obj, "Undo  (Ctrl+Z)")
        at += bh + px(8)
        self.redo = theme.button(self.root, icon="redo", w=bh, h=bh)
        self.redo.obj.set_pos(at, cy)
        self.redo.on(lv.EVENT.CLICKED, app.redo)
        app.tips.add(self.redo.obj, "Redo  (Ctrl+Y)")

        self.helpbtn = theme.button(self.root, icon="help_outline", w=bh, h=bh)
        self.helpbtn.obj.align(lv.ALIGN.RIGHT_MID, -px(12), 0)
        self.helpbtn.on(lv.EVENT.CLICKED, lambda: app.help.toggle())
        app.tips.add(self.helpbtn.obj, "How to play")
        self.midi = theme.label(self.root, "", "muted")
        self.midi.align(lv.ALIGN.RIGHT_MID, -px(20) - bh, 0)
        self.refresh()

    def stepper(self, x, y, w, h, nudge, tap=None, tip=None):
        """A value between - and + buttons; returns its label.

        The buttons repeat while held; dragging the value up or down changes
        it one step per few pixels; with ``tap``, a click on the value that
        doesn't drag calls ``tap()`` (tap tempo)."""
        box = theme.box(self.root, x, y, w, h, theme.RAISED, px(6))
        minus = theme.button(box, icon="remove", w=h - px(8), h=h - px(8))
        minus.obj.set_pos(px(4), px(4))
        minus.on(lv.EVENT.SHORT_CLICKED, lambda: nudge(-1))
        minus.on(lv.EVENT.LONG_PRESSED_REPEAT, lambda: nudge(-1))
        label = theme.label(box, "", "title")
        label.align(lv.ALIGN.CENTER, 0, 0)
        plus = theme.button(box, icon="add", w=h - px(8), h=h - px(8))
        plus.obj.align(lv.ALIGN.RIGHT_MID, -px(4), 0)
        plus.on(lv.EVENT.SHORT_CLICKED, lambda: nudge(1))
        plus.on(lv.EVENT.LONG_PRESSED_REPEAT, lambda: nudge(1))
        # The middle of the box drags (and taps).
        grip = theme.box(box, h, 0, w - 2 * h, h)
        grip.add_flag(lv.OBJ_FLAG.CLICKABLE)
        state = {"y": 0, "done": 0, "moved": False}
        step_px = px(8)

        def where():
            pt = lv.point_t()
            lv.indev_active().get_point(pt)
            return pt.y

        def pressed(e):
            state.update(y=where(), done=0, moved=False)

        def pressing(e):
            steps = (state["y"] - where()) // step_px       # up is more
            if steps != state["done"]:
                nudge(steps - state["done"])
                state["done"] = steps
                state["moved"] = True

        def released(e):
            if not state["moved"] and tap is not None:
                tap()

        grip.add_event_cb(pressed, lv.EVENT.PRESSED, None)
        grip.add_event_cb(pressing, lv.EVENT.PRESSING, None)
        grip.add_event_cb(released, lv.EVENT.RELEASED, None)
        if tip:
            self.app.tips.add(grip, tip)
        return label

    def refresh(self):
        p = self.app.project
        self.tempo.set_text("%d BPM" % round(p["tempo"]))
        self.swing.set_text("Swing %d%%" % swing_percent(p.get("swing", 0)))
        self.metronome.checked = p.get("metronome", False)
        self.quant.checked = p.get("quantise", True)
        dim = bool(p.get("song_mode")) and self.app.song_shown   # the song picks the patterns
        t = model.find_track(p, self.app.selected)
        current = t.get("current", 0) if t else 0
        self.pending = t["next"]["pattern"] if t and t.get("next") else None
        for i, b in enumerate(self.slots):
            b.checked = i == current and not dim
            b.obj.set_style_opa(90 if dim else 255, 0)
            b.obj.set_style_border_width(0, 0)
            full = bool(t) and i < len(t["patterns"]) and bool(t["patterns"][i]["notes"])
            if full:
                self.dots[i].remove_flag(lv.OBJ_FLAG.HIDDEN)
            else:
                self.dots[i].add_flag(lv.OBJ_FLAG.HIDDEN)
        self.overdub.checked = p.get("overdub", True)
        self.play.checked = self.app.engines.playing
        self.song.checked = bool(p.get("song_mode"))
        self.rec.checked = self.app.rec_armed
        # The label doesn't share the button's checked state: colour it here.
        self.rec.label.set_style_text_color(theme.c(0xFFFFFF if self.app.rec_armed else theme.REC), 0)

    def show_history(self, can_undo, can_redo):
        """Undo and redo dim when there's nothing to take back (or bring back)."""
        state = (can_undo, can_redo)
        if state == getattr(self, "history_state", None):
            return
        self.history_state = state
        self.undo.obj.set_style_opa(255 if can_undo else 90, 0)
        self.redo.obj.set_style_opa(255 if can_redo else 90, 0)

    def blink_pending(self):
        """A pattern waiting for the bar line blinks its outline until it lands."""
        import time

        pending = getattr(self, "pending", None)
        if pending is None:
            return
        on = int(time.monotonic() * 4) % 2 == 0
        b = self.slots[pending].obj
        b.set_style_border_color(theme.c(theme.ACCENT), 0)
        b.set_style_border_width(px(2) if on else 0, 0)

    def pulse(self, recording):
        """While recording, the armed button breathes; otherwise it's steady."""
        import time

        opa = 255
        if recording:
            opa = 255 if int(time.monotonic() * 2) % 2 == 0 else 120
        self.rec.obj.set_style_bg_opa(opa, lv.STATE.CHECKED)

    def show_position(self, tick):
        if tick is None:
            self.position.set_text("1 . 1 . 1")
            return
        if tick < 0:                               # the count-in: its beats, 1 to 4
            self.position.set_text("- %d -" % (int((tick + model.BAR) // model.PPQ) + 1))
            return
        tick = int(tick)
        bar = tick // model.BAR + 1
        beat = (tick % model.BAR) // model.PPQ + 1
        step = (tick % model.PPQ) // model.STEP + 1
        self.position.set_text("%d . %d . %d" % (bar, beat, step))

    def show_midi(self, text, ok):
        self.midi.set_text(text)
        self.midi.set_style_text_color(theme.c(theme.TEXT if ok else theme.MUTED), 0)
