"""The pattern editor: a step grid for drums, a piano roll for everything else.

Both edit the selected track's current pattern through the app's history, so
every change can be undone. The playhead moves across whichever is showing.
"""

import lvgl as lv

import catalog
import model
import theme
from theme import px

BARS = (1, 2, 4)
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


class Editor:
    def __init__(self, parent, rect, app):
        self.app = app
        self.rect = rect
        self.root = theme.panel(parent, rect)
        self.view = None
        self.track_id = None
        self.low = 48                 # piano roll's lowest visible pitch
        self.selected_note = None     # index into the pattern's notes
        self.selection = set()        # several selected notes (indices), for moves and the clipboard
        self.last_step = 0            # the last step clicked: where a paste lands when stopped

    def track(self):
        return model.find_track(self.app.project, self.track_id)

    def slot(self):
        """The pattern this editor shows: the track's own (where it's going, if switching)."""
        t = self.track()
        return model.shown_pattern(t) if t is not None else 0

    def pattern(self):
        t = self.track()
        if t is None:
            return None
        i = self.slot()
        return t["patterns"][i] if i < len(t["patterns"]) else None

    def pattern_path(self, *rest):
        return model.track_path(self.track_id, "patterns", self.slot(), *rest)

    def show(self, track_id):
        self.track_id = track_id
        self.selected_note = None
        self.selection = set()
        self.build()

    def build(self):
        x, y, w, h = self.rect
        self.root.clean()
        t = self.track()
        pat = self.pattern()
        if t is None or pat is None:
            return
        idx = self.app.project["tracks"].index(t)
        self.colour = theme.track_colour(idx)
        head_h = px(52)
        title = theme.label(self.root, "%s   pattern %s" % (t["name"], "ABCDEFGH"[self.slot()]),
                            "title")
        title.set_pos(px(16), (head_h - px(26)) // 2)
        if self.app.project.get("song_mode"):
            # Editing one of the song's patterns: a way back to the scenes.
            back = theme.button(self.root, "Song", icon="chevron_left", w=px(100), h=px(36))
            title.update_layout()
            back.obj.set_pos(px(16) + title.get_width() + px(16), (head_h - px(36)) // 2)
            back.on(lv.EVENT.CLICKED, lambda: (self.app.show_song(True), self.app.transport.refresh()))
        at = w - px(16)
        clear = theme.button(self.root, "Clear", icon="delete", w=px(104), h=px(36))
        at -= px(104)
        clear.obj.set_pos(at, (head_h - px(36)) // 2)
        clear.on(lv.EVENT.CLICKED, self.clear)
        copy = theme.button(self.root, "Copy", icon="content_copy", w=px(100), h=px(36))
        at -= px(106)
        copy.obj.set_pos(at, (head_h - px(36)) // 2)
        self.bar_buttons = {}
        copy.on(lv.EVENT.CLICKED, self.app.duplicate_pattern)
        self.copy_button = copy.obj
        for b in reversed(BARS):
            btn = theme.button(self.root, "%d bar%s" % (b, "" if b == 1 else "s"), w=px(78), h=px(36), checkable=True)
            at -= px(84)
            btn.obj.set_pos(at, (head_h - px(36)) // 2)
            btn.checked = pat["bars"] == b
            btn.on(lv.EVENT.CLICKED, (lambda n: lambda: self.set_bars(n))(b))
            self.bar_buttons[b] = btn.obj
        self.note_info = None
        if t["kind"] != "drums":
            self._roll_tools(at, head_h)
            # The selected note, in words: pitch, length, velocity.
            self.note_info = theme.label(self.root, "", "muted")
            title.update_layout()
            self.note_info.set_pos(px(16) + title.get_width() + px(130 if self.app.project.get("song_mode") else 20),
                                   (head_h - px(18)) // 2)
            self.show_note_info()
        body = (0, head_h, w, h - head_h)
        if t["kind"] == "drums":
            self.view = StepGrid(self, body)
        else:
            self.view = PianoRoll(self, body)

    def _roll_tools(self, at, head_h):
        y = (head_h - px(36)) // 2
        items = [(None, "expand_more", "Show lower notes (or the mouse wheel)", lambda: self.shift(-12)),
                 (None, "expand_less", "Show higher notes (or the mouse wheel)", lambda: self.shift(12)),
                 ("Vel -", None, None, lambda: self.velocity(-16)), ("Vel +", None, None, lambda: self.velocity(16))]
        for label, icon, tip, fn in reversed(items):
            b = theme.button(self.root, label or "", icon=icon, w=px(70) if label else px(44), h=px(36))
            at -= px(76) if label else px(50)
            b.obj.set_pos(at - px(20), y)
            b.on(lv.EVENT.CLICKED, fn)
            if tip:
                self.app.tips.add(b.obj, tip)
        rm = theme.button(self.root, icon="close", w=px(40), h=px(36))
        self.app.tips.add(rm.obj, "Delete the selected note")
        rm.obj.set_pos(at - px(70), y)
        rm.on(lv.EVENT.CLICKED, self.delete_selected)

    def show_note_info(self):
        if self.note_info is None:
            return
        pat = self.pattern()
        i = self.selected_note
        if len(self.selection) > 1:
            self.note_info.set_text("%d notes selected" % len(self.selection))
            return
        if i is None or not pat or i >= len(pat["notes"]):
            self.note_info.set_text("Shift-drag a note: velocity")
            return
        s_, l_, p_, v_ = pat["notes"][i]
        steps = max(1, round(l_ / model.STEP))
        length = {1: "1/16", 2: "1/8", 3: "3/16", 4: "1/4", 8: "1/2", 16: "1 bar"}.get(steps, "%d/16" % steps)
        self.note_info.set_text("%s%d   %s   vel %d" % (NOTE_NAMES[p_ % 12], p_ // 12 - 1, length, v_))

    # -- edits ------------------------------------------------------------------

    def add_note(self, note):
        self.app.history.do(self.app.project, {"op": "insert", "path": self.pattern_path("notes", -1), "value": note})

    def remove_note(self, index):
        self.app.history.do(self.app.project, {"op": "remove", "path": self.pattern_path("notes", index)})

    def replace_note(self, index, note):
        self.app.history.begin()
        self.remove_note(index)
        self.add_note(note)
        self.app.history.end()
        self.selected_note = len(self.pattern()["notes"]) - 1

    def clear(self):
        pat = self.pattern()
        if not pat or not pat["notes"]:
            return
        self.app.history.begin()
        for i in range(len(pat["notes"]) - 1, -1, -1):
            self.remove_note(i)
        self.app.history.end()
        self.selected_note = None
        self.build()

    def set_bars(self, bars):
        """Change the pattern's length. Longer repeats what's there into the new
        bars (one undo step), unless notes are already waiting past the end
        from when it was longer: those come back instead."""
        pat = self.pattern()
        old = pat["bars"]
        if bars == old:
            return
        h = self.app.history
        h.begin()
        self.app.edit({"op": "set", "path": self.pattern_path("bars"), "value": bars})
        old_len = old * model.BAR
        if bars > old and not any(n[0] >= old_len for n in pat["notes"]):
            inside = [list(n) for n in pat["notes"] if n[0] < old_len]
            for k in range(1, bars // old):
                for s_, l_, p_, v_ in inside:
                    self.add_note([s_ + k * old_len, l_, p_, v_])
        h.end()
        self.build()

    def wheel(self, dy, x, y):
        """The mouse wheel over the piano roll scrolls its pitches, two a notch."""
        v = self.view
        if not dy or v is None or not hasattr(v, "hit") or self.root.has_flag(lv.OBJ_FLAG.HIDDEN):
            return
        a = lv.area_t()
        v.hit.get_coords(a)
        if a.x1 <= x <= a.x2 and a.y1 <= y <= a.y2:
            self.shift(2 if dy < 0 else -2)

    def shift(self, semis):
        self.low = max(12, min(96, self.low + semis))
        self.build()

    def velocity(self, delta):
        pat = self.pattern()
        if self.selected_note is None or not pat or self.selected_note >= len(pat["notes"]):
            return
        s, l, p, v = pat["notes"][self.selected_note]
        self.replace_note(self.selected_note, [s, l, p, max(1, min(127, v + delta))])
        self.build()
        self.show_note_info()

    def delete_selected(self):
        pat = self.pattern()
        if not pat:
            return
        doomed = sorted(self.selection | ({self.selected_note} if self.selected_note is not None else set()),
                        reverse=True)
        doomed = [i for i in doomed if i < len(pat["notes"])]
        if not doomed:
            return
        self.app.history.begin()
        for i in doomed:
            self.remove_note(i)
        self.app.history.end()
        self.selected_note = None
        self.selection = set()
        self.build()

    # -- several notes: replace them as one step, and the clipboard ---------------------

    def replace_notes(self, indices, notes):
        """Swap the notes at ``indices`` for ``notes`` in one undo step; the new
        ones are appended, so they're the last len(notes) and become the selection."""
        self.app.history.begin()
        for i in sorted(indices, reverse=True):
            self.remove_note(i)
        for n in notes:
            self.add_note(n)
        self.app.history.end()
        count = len(self.pattern()["notes"])
        self.selection = set(range(count - len(notes), count))
        self.selected_note = count - 1 if len(notes) == 1 else None

    def select_all(self):
        pat = self.pattern()
        if pat and self.view is not None and hasattr(self.view, "hit"):
            self.selection = set(range(len(pat["notes"])))
            self.selected_note = None
            self.view.draw_notes()
            self.show_note_info()

    def copy(self):
        """The selected notes onto the clipboard, from the first one's step."""
        pat = self.pattern()
        chosen = self.selection or ({self.selected_note} if self.selected_note is not None else set())
        notes = [pat["notes"][i] for i in sorted(chosen) if pat and i < len(pat["notes"])]
        if not notes:
            self.app.say("Select notes to copy (Shift-drag a box, or Ctrl+A)", 2.0)
            return
        first = min(n[0] for n in notes)
        self.app.clipboard = [[n[0] - first, n[1], n[2], n[3]] for n in notes]
        self.app.say("Copied %d note%s" % (len(notes), "" if len(notes) == 1 else "s"), 2.0)

    def paste(self):
        """The clipboard into this pattern: at the playhead while playing,
        otherwise at the last step clicked."""
        clip = getattr(self.app, "clipboard", None)
        pat = self.pattern()
        if not clip or not pat or self.view is None or not hasattr(self.view, "hit"):
            return
        length = pat["bars"] * model.BAR
        tick = self.app.engines.now_tick() if self.app.engines.playing else None
        at = (int(tick % length) // model.STEP if tick is not None else self.last_step) * model.STEP
        notes = [[at + s_, l_, p_, v_] for s_, l_, p_, v_ in clip if at + s_ < length]
        if notes:
            self.replace_notes([], notes)
            self.build()
            self.app.say("Pasted %d note%s" % (len(notes), "" if len(notes) == 1 else "s"), 2.0)

    def duplicate_selection(self):
        """The selection again, straight after itself (Ctrl+D)."""
        pat = self.pattern()
        chosen = sorted(self.selection or ({self.selected_note} if self.selected_note is not None else set()))
        notes = [pat["notes"][i] for i in chosen if pat and i < len(pat["notes"])]
        if not notes:
            return
        first = min(n[0] for n in notes)
        end = max(n[0] + n[1] for n in notes)
        span = -(-(end - first) // model.STEP) * model.STEP
        length = pat["bars"] * model.BAR
        copies = [[n[0] + span, n[1], n[2], n[3]] for n in notes if n[0] + span < length]
        if copies:
            self.replace_notes([], copies)
            self.build()

    def playhead(self, tick):
        if self.view is not None:
            self.view.playhead(tick)


HIT, ACCENT = 100, 127       # a click places a hit; a second click makes it an accent
ACCENTED = 120              # velocities from here up draw as accents
QUIET = 90                  # and below this, dimmer the softer they are
RULER = 20                  # the bar and beat numbers' strip over a grid (scaled at use)


def ruler(parent, x, y, cell_w, steps):
    """Bar numbers (bright) and beat numbers (dim) over a grid of sixteenths."""
    for s in range(0, steps, 4):
        bar, beat = divmod(s // 4, 4)
        text, kind = (str(bar + 1), "text") if beat == 0 else (str(beat + 1), "muted")
        lb = theme.label(parent, text, kind)
        lb.set_pos(int(x + s * cell_w) + px(2), y)
        lb.remove_flag(lv.OBJ_FLAG.CLICKABLE)


class StepGrid:
    """One row per pad of the kit, sixteen steps a bar.

    A click on a step cycles it: off, a hit, an accent (drawn brighter), off.
    A click on a row's name plays that sound."""

    def __init__(self, editor, rect):
        self.editor = editor
        x, y, w, h = rect
        t = editor.track()
        pat = editor.pattern()
        info = catalog.instrument(t["instrument"])
        names = dict((n, name) for n, name in info["notes"])
        self.pads = [[n, names.get(n, str(n))] for n in catalog.pad_notes(t)]
        self.steps = pat["bars"] * 16
        names_w = px(150)
        self.box = theme.box(editor.root, x, y, w, h)
        rows = len(self.pads)
        top = px(8) + px(RULER)
        row_h = min(px(44), (h - top - px(8)) // max(1, rows))
        for r, (note, name) in enumerate(self.pads):
            lb = theme.label(self.box, name, "muted")
            lb.set_pos(px(16), top + r * row_h + (row_h - px(18)) // 2)
            lb.add_flag(lv.OBJ_FLAG.CLICKABLE)
            lb.set_ext_click_area(px(6))
            lb.add_event_cb((lambda n: lambda e: editor.app.audition(n, HIT, seconds=0.4))(note),
                            lv.EVENT.CLICKED, None)
        self.grid = lv.buttonmatrix(self.box)
        self.map = []
        for r in range(rows):
            self.map += [" "] * self.steps
            if r < rows - 1:
                self.map.append("\n")
        self.grid.set_map(self.map)
        self.grid.set_button_ctrl_all(lv.buttonmatrix.CTRL.CHECKABLE)
        gw = w - names_w - px(16)
        self.grid.set_size(gw, row_h * rows)
        self.grid.set_pos(names_w, top)
        self.grid.set_style_bg_opa(0, 0)
        self.grid.set_style_border_width(0, 0)
        self.grid.set_style_pad_all(0, 0)
        self.grid.set_style_pad_row(px(4), 0)
        self.grid.set_style_pad_column(px(3), 0)
        self.grid.set_style_bg_color(theme.c(theme.RAISED), lv.PART.ITEMS)
        self.grid.set_style_radius(px(4), lv.PART.ITEMS)
        self.grid.set_style_shadow_width(0, lv.PART.ITEMS)
        self.grid.set_style_border_width(0, lv.PART.ITEMS)
        self.grid.set_style_bg_color(theme.c(editor.colour), lv.PART.ITEMS | lv.STATE.CHECKED)
        self.grid.add_event_cb(lambda e: self.toggled(), lv.EVENT.VALUE_CHANGED, None)
        self.grid.add_event_cb(lambda e: self.vel_press(), lv.EVENT.PRESSED, None)
        self.grid.add_event_cb(lambda e: self.vel_drag(), lv.EVENT.PRESSING, None)
        self.grid.add_event_cb(lambda e: self.vel_release(), lv.EVENT.RELEASED, None)
        self.vdrag = None
        self.cell_w = gw / self.steps
        self.grid_x = names_w
        self.grid_h = row_h * rows
        # Where a cell is drawn, for the accent marks over it.
        self.cells = (names_w, top, (gw - (self.steps - 1) * px(3)) / self.steps,
                      (row_h * rows - (rows - 1) * px(4)) / rows, px(3), px(4))
        self.marks = []
        # Beat lines over the grid (not clickable).
        for s in range(4, self.steps, 4):
            line = theme.box(self.box, int(names_w + s * self.cell_w) - px(2), top, 1, self.grid_h,
                             theme.LINE_BAR if s % 16 == 0 else theme.LINE)
            line.remove_flag(lv.OBJ_FLAG.CLICKABLE)
        ruler(self.box, names_w, px(4), self.cell_w, self.steps)
        self.head = theme.box(self.box, names_w, top - px(4), max(2, int(self.cell_w)), self.grid_h + px(8))
        self.head.set_style_bg_color(theme.c(theme.ACCENT), 0)
        self.head.set_style_bg_opa(70, 0)
        self.head.remove_flag(lv.OBJ_FLAG.CLICKABLE)
        self.head.add_flag(lv.OBJ_FLAG.HIDDEN)
        self.refresh()

    def index_of(self, row, step):
        return row * self.steps + step

    def refresh(self):
        pat = self.editor.pattern()
        vel = {}
        for n in pat["notes"]:
            key = (n[2], n[0] // model.STEP)
            vel[key] = max(vel.get(key, 0), n[3])
        for m in self.marks:
            m.delete()
        self.marks = []
        x0, y0, cw, ch, gap_x, gap_y = self.cells
        for r, (note, _) in enumerate(self.pads):
            for s in range(self.steps):
                i = self.index_of(r, s)
                if (note, s) in vel:
                    self.grid.set_button_ctrl(i, lv.buttonmatrix.CTRL.CHECKED)
                    v = vel[(note, s)]
                    if v >= ACCENTED or v < QUIET:
                        # An accent lit brighter; a quiet hit dimmed, the softer the dimmer.
                        mx, my = int(x0 + s * (cw + gap_x)), int(y0 + r * (ch + gap_y))
                        colour, opa = (theme.TEXT, 110) if v >= ACCENTED else (theme.BG, min(170, (QUIET - v) * 2 + 40))
                        mark = theme.box(self.box, mx, my, max(2, int(cw)), max(2, int(ch)), colour, px(4))
                        mark.set_style_bg_opa(opa, 0)
                        mark.remove_flag(lv.OBJ_FLAG.CLICKABLE)
                        self.marks.append(mark)
                else:
                    self.grid.clear_button_ctrl(i, lv.buttonmatrix.CTRL.CHECKED)
        self.head.move_foreground()

    def _step_note(self, i):
        """The index in the pattern of the hit at button ``i``, or None."""
        r, s = divmod(i, self.steps)
        note, start = self.pads[r][0], s * model.STEP
        pat = self.editor.pattern()
        return next((k for k, n in enumerate(pat["notes"]) if n[2] == note and n[0] == start), None)

    def _point_y(self):
        pt = lv.point_t()
        lv.indev_active().get_point(pt)
        return pt.y

    def vel_press(self):
        self.vdrag = None
        if not self.editor.app.shift:
            return
        i = self.grid.get_selected_button()
        if i < 0 or i >= len(self.pads) * self.steps:
            return
        k = self._step_note(i)
        if k is not None:
            self.vdrag = {"k": k, "y": self._point_y(), "v0": self.editor.pattern()["notes"][k][3], "v": None}

    def vel_drag(self):
        d = self.vdrag
        if d is None:
            return
        d["v"] = max(1, min(127, d["v0"] + (d["y"] - self._point_y()) // max(1, px(2))))
        self.editor.app.say("Velocity %d" % d["v"], 1.5)

    def vel_release(self):
        d, self.vdrag = self.vdrag, None
        if d is None or d["v"] is None or d["v"] == d["v0"]:
            return
        s_, l_, p_, _ = self.editor.pattern()["notes"][d["k"]]
        self.editor.replace_note(d["k"], [s_, l_, p_, d["v"]])
        self.refresh()

    def toggled(self):
        if self.editor.app.shift:
            self.refresh()          # Shift is for velocity: a Shift-click doesn't toggle the step
            return
        i = self.grid.get_selected_button()
        if i < 0 or i >= len(self.pads) * self.steps:
            return
        r, s = divmod(i, self.steps)
        note = self.pads[r][0]
        pat = self.editor.pattern()
        start = s * model.STEP
        found = [k for k, n in enumerate(pat["notes"]) if n[2] == note and n[0] == start]
        if not found:
            self.editor.add_note([start, model.STEP, note, HIT])
        elif pat["notes"][found[0]][3] < ACCENTED:
            s_, l_, p_, _ = pat["notes"][found[0]]
            self.editor.replace_note(found[0], [s_, l_, p_, ACCENT])
        else:
            self.editor.remove_note(found[0])
        self.refresh()          # the matrix toggled the cell itself; put it right

    def playhead(self, tick):
        if tick is None or tick < 0:
            self.head.add_flag(lv.OBJ_FLAG.HIDDEN)
            return
        pat = self.editor.pattern()
        step = int(tick % (pat["bars"] * model.BAR)) // model.STEP
        self.head.remove_flag(lv.OBJ_FLAG.HIDDEN)
        self.head.set_x(int(self.grid_x + step * self.cell_w))


class PianoRoll:
    """Two octaves of pitch, sixteen steps a bar; click, drag, resize."""

    ROWS = 24

    def __init__(self, editor, rect):
        self.editor = editor
        x, y, w, h = rect
        pat = editor.pattern()
        self.steps = pat["bars"] * 16
        keys_w = px(70)
        self.box = theme.box(editor.root, x, y, w, h)
        self.row_h = (h - px(16) - px(RULER)) // self.ROWS
        self.grid_x = keys_w
        self.grid_y = px(8) + px(RULER)
        self.grid_w = w - keys_w - px(16)
        self.cell_w = self.grid_w / self.steps
        low = editor.low
        for r in range(self.ROWS):
            pitch = low + self.ROWS - 1 - r
            black = NOTE_NAMES[pitch % 12].endswith("#")
            yy = self.grid_y + r * self.row_h
            theme.box(self.box, px(8), yy, keys_w - px(12), self.row_h - 1, 0x2A2F38 if black else 0xD8DCE4, px(2))
            if pitch % 12 == 0:
                lb = theme.label(self.box, "C%d" % (pitch // 12 - 1), "muted", theme.BG)
                lb.set_pos(px(12), yy + (self.row_h - px(16)) // 2)
            band = theme.box(self.box, keys_w, yy, self.grid_w, self.row_h - 1, 0x141820 if black else 0x1A1F28)
            band.remove_flag(lv.OBJ_FLAG.CLICKABLE)
        for s in range(0, self.steps + 1, 4):
            line = theme.box(self.box, int(keys_w + s * self.cell_w), self.grid_y, 1, self.ROWS * self.row_h,
                             theme.LINE_BAR if s % 16 == 0 else theme.LINE)
            line.remove_flag(lv.OBJ_FLAG.CLICKABLE)
        # The grid's hit area sits over the bands and takes every press.
        self.hit = theme.box(self.box, keys_w, self.grid_y, self.grid_w, self.ROWS * self.row_h)
        self.hit.add_flag(lv.OBJ_FLAG.CLICKABLE)
        self.hit.add_event_cb(lambda e: self.pressed(), lv.EVENT.PRESSED, None)
        self.hit.add_event_cb(lambda e: self.pressing(), lv.EVENT.PRESSING, None)
        self.hit.add_event_cb(lambda e: self.released(), lv.EVENT.RELEASED, None)
        ruler(self.box, keys_w, px(4), self.cell_w, self.steps)
        # How many notes lie above and below the two octaves shown.
        self.above = theme.label(self.box, "", "muted", theme.ACCENT)
        self.below = theme.label(self.box, "", "muted", theme.ACCENT)
        self.above.set_pos(keys_w + self.grid_w - px(90), self.grid_y + px(2))
        self.below.set_pos(keys_w + self.grid_w - px(90), self.grid_y + self.ROWS * self.row_h - px(22))
        self.notes = []
        self.drag = None
        self.head = theme.box(self.box, keys_w, self.grid_y, max(2, px(2)), self.ROWS * self.row_h, theme.ACCENT)
        self.head.remove_flag(lv.OBJ_FLAG.CLICKABLE)
        self.head.add_flag(lv.OBJ_FLAG.HIDDEN)
        self.draw_notes()

    def draw_notes(self):
        for o in self.notes:
            o.delete()
        self.notes = []
        pat = self.editor.pattern()
        low, high = self.editor.low, self.editor.low + self.ROWS - 1
        up = sum(1 for n in pat["notes"] if n[2] > high)
        down = sum(1 for n in pat["notes"] if n[2] < low)
        self.above.set_text(("%d note%s above" % (up, "" if up == 1 else "s")) if up else "")
        self.below.set_text(("%d note%s below" % (down, "" if down == 1 else "s")) if down else "")
        for i, (start, length, pitch, vel) in enumerate(pat["notes"]):
            r = self.editor.low + self.ROWS - 1 - pitch
            if not 0 <= r < self.ROWS or start >= self.steps * model.STEP:
                self.notes.append(None)
                continue
            o = theme.box(self.box, *self.note_rect(start, length, pitch), self.editor.colour, px(3))
            o.set_style_bg_opa(110 + vel, 0)
            if i == self.editor.selected_note or i in self.editor.selection:
                o.set_style_border_width(px(2), 0)
                o.set_style_border_color(theme.c(theme.TEXT), 0)
            o.remove_flag(lv.OBJ_FLAG.CLICKABLE)
            self.notes.append(o)
        self.notes = [o for o in self.notes]

    def note_rect(self, start, length, pitch):
        r = self.editor.low + self.ROWS - 1 - pitch
        x = self.grid_x + int(start / model.STEP * self.cell_w) + 1
        w = max(px(6), int(length / model.STEP * self.cell_w) - 2)
        return x, self.grid_y + r * self.row_h + 1, w, self.row_h - 3

    def point(self):
        p = lv.point_t()
        lv.indev_active().get_point(p)
        a = lv.area_t()
        self.hit.get_coords(a)
        return p.x - a.x1, p.y - a.y1

    def cell(self, px_, py_):
        step = max(0, min(self.steps - 1, int(px_ / self.cell_w)))
        pitch = self.editor.low + self.ROWS - 1 - max(0, min(self.ROWS - 1, int(py_ / self.row_h)))
        return step, pitch

    def hit_note(self, step, pitch, px_):
        pat = self.editor.pattern()
        for i, (s, l, p, v) in enumerate(pat["notes"]):
            if p == pitch and s <= step * model.STEP < s + l:
                end_px = (s + l) / model.STEP * self.cell_w
                return i, end_px - px_ < max(px(10), self.cell_w * 0.35)
        return None, False

    def pressed(self):
        x, y = self.point()
        step, pitch = self.cell(x, y)
        i, edge = self.hit_note(step, pitch, x)
        pat = self.editor.pattern()
        self.editor.last_step = step
        sel = self.editor.selection
        if i is None and self.editor.app.shift:
            # Shift-drag on empty grid: a box that selects the notes inside it.
            self.drag = {"mode": "box", "note": [step * model.STEP, model.STEP, pitch, 100], "index": None,
                         "from": (step, pitch), "xy": (x, y)}
        elif i is not None and self.editor.app.shift:
            # Shift on a note: a click adds it to (or takes it from) the selection;
            # a drag sets velocity, on the whole selection if it's in it.
            self.drag = {"mode": "velocity", "note": list(pat["notes"][i]), "index": i, "from": (step, pitch), "y": y}
        elif i is not None and i in sel and len(sel) > 1 and not edge:
            self.drag = {"mode": "movesel", "note": list(pat["notes"][i]), "index": i, "from": (step, pitch)}
        elif i is None:
            note = [step * model.STEP, model.STEP, pitch, 100]
            self.drag = {"mode": "new", "note": note, "index": None, "from": (step, pitch)}
        else:
            self.drag = {"mode": "resize" if edge else "move", "note": list(pat["notes"][i]), "index": i,
                         "from": (step, pitch)}
        self.ghost = theme.box(self.box, *self.note_rect(*self.drag["note"][:3]), theme.TEXT, px(3))
        self.ghost.set_style_bg_opa(90, 0)
        self.ghost.remove_flag(lv.OBJ_FLAG.CLICKABLE)
        self.moved = False

    def pressing(self):
        if not self.drag:
            return
        x, y = self.point()
        step, pitch = self.cell(x, y)
        d = self.drag
        s0, l0, p0, v0 = d["note"]
        fs, fp = d["from"]
        if d["mode"] == "box":
            x0, y0 = d["xy"]
            bx, by = min(x0, x), min(y0, y)
            self.ghost.set_pos(self.grid_x + bx, self.grid_y + by)
            self.ghost.set_size(max(2, abs(x - x0)), max(2, abs(y - y0)))
            d["to"] = (x, y)
            self.moved = True
            return
        if d["mode"] == "movesel":
            d["delta"] = (step - fs, pitch - fp)
            self.moved = d["delta"] != (0, 0)
            ns = max(0, min((self.steps - 1) * model.STEP, s0 + (step - fs) * model.STEP))
            nx, ny, nw, nh = self.note_rect(ns, l0, max(0, min(127, p0 + pitch - fp)))
            self.ghost.set_pos(nx, ny)
            self.ghost.set_size(nw, nh)
            return
        if d["mode"] == "velocity":
            v = max(1, min(127, v0 + (d["y"] - y) // max(1, px(2))))
            d["now"] = [s0, l0, p0, v]
            self.ghost.set_style_bg_opa(min(255, 110 + v), 0)
            self.editor.app.say("Velocity %d" % v, 1.5)
            self.moved = v != v0
            return
        if d["mode"] in ("new", "resize"):
            end = max(s0 + model.STEP, (step + 1) * model.STEP)
            d["now"] = [s0, end - s0, p0, v0]
        else:
            ns = max(0, min((self.steps - 1) * model.STEP, s0 + (step - fs) * model.STEP))
            d["now"] = [ns, l0, max(0, min(127, p0 + (pitch - fp))), v0]
        if (step, pitch) != (fs, fp):
            self.moved = True
        nx, ny, nw, nh = self.note_rect(*d["now"][:3])
        self.ghost.set_pos(nx, ny)
        self.ghost.set_size(nw, nh)

    def released(self):
        d, self.drag = self.drag, None
        if d is None:
            return
        self.ghost.delete()
        ed = self.editor
        final = d.get("now", d["note"])
        notes = ed.pattern()["notes"]
        if d["mode"] == "box":
            if self.moved and "to" in d:
                (x0, y0), (x1, y1) = d["xy"], d["to"]
                s0_, p_hi = self.cell(min(x0, x1), min(y0, y1))
                s1_, p_lo = self.cell(max(x0, x1), max(y0, y1))
                ed.selection = {k for k, n in enumerate(notes)
                                if p_lo <= n[2] <= p_hi and n[0] < (s1_ + 1) * model.STEP and n[0] + n[1] > s0_ * model.STEP}
                ed.selected_note = None
            self.draw_notes()
            ed.show_note_info()
            return
        if d["mode"] == "velocity" and not self.moved:
            i = d["index"]
            ed.selection = ed.selection ^ {i}
            if ed.selected_note is not None and ed.selected_note != i:
                ed.selection.add(ed.selected_note)
            ed.selected_note = None
            self.draw_notes()
            ed.show_note_info()
            return
        if d["mode"] == "velocity" and len(ed.selection) > 1 and d["index"] in ed.selection:
            dv = final[3] - d["note"][3]
            idx = sorted(ed.selection)
            ed.replace_notes(idx, [[n[0], n[1], n[2], max(1, min(127, n[3] + dv))] for n in (notes[k] for k in idx)])
            self.draw_notes()
            ed.show_note_info()
            return
        if d["mode"] == "movesel":
            if self.moved:
                ds, dp = d["delta"]
                idx = sorted(ed.selection)
                limit = (self.steps - 1) * model.STEP
                ed.replace_notes(idx, [[max(0, min(limit, n[0] + ds * model.STEP)), n[1], max(0, min(127, n[2] + dp)), n[3]]
                                       for n in (notes[k] for k in idx)])
            self.draw_notes()
            ed.show_note_info()
            return
        ed.selection = set()
        if d["mode"] == "new":
            ed.add_note(final)
            ed.selected_note = len(ed.pattern()["notes"]) - 1
            ed.app.audition(final[2], final[3], 0.2)
        elif not self.moved and d["mode"] in ("move", "velocity"):
            ed.selected_note = d["index"]
        else:
            ed.replace_note(d["index"], final)
        self.draw_notes()
        ed.show_note_info()

    def playhead(self, tick):
        if tick is None or tick < 0:
            self.head.add_flag(lv.OBJ_FLAG.HIDDEN)
            return
        pat = self.editor.pattern()
        t = tick % (pat["bars"] * model.BAR)
        self.head.remove_flag(lv.OBJ_FLAG.HIDDEN)
        self.head.set_x(int(self.grid_x + t / model.STEP * self.cell_w))
