"""The song: scenes in order, each a pattern per track and a repeat count.

Click a track's letter in a scene to cycle through its patterns (and "-",
silent); hold it to open that pattern in the editor while the song plays on.
A scene's number plays the song from there; its loop button repeats just that
scene. The scene playing now is highlighted, and the list scrolls to keep it
in view.
"""

import lvgl as lv

from songlink import model
import theme
from theme import px

LETTERS = "ABCDEFGH"


class SongView:
    def __init__(self, parent, rect, app):
        self.app = app
        self.rect = rect
        self.root = theme.panel(parent, rect)
        self.rows = []
        self.playing = None
        self.list = None
        self.build()

    def build(self):
        x, y, w, h = self.rect
        scrolled = self.list.get_scroll_y() if self.list is not None else 0
        self.root.clean()
        self.rows = []
        self.parts = []           # each row's buttons, for tests
        self.playing = None
        p = self.app.project
        head_h = px(52)
        theme.label(self.root, "Song", "title").set_pos(px(16), (head_h - px(26)) // 2)
        add = theme.button(self.root, "Add scene", icon="add", w=px(150), h=px(36))
        add.obj.set_pos(w - px(166), (head_h - px(36)) // 2)
        add.on(lv.EVENT.CLICKED, self.add_scene)
        tracks = p["tracks"]
        col_w = px(110)
        left = px(70)
        for c, t in enumerate(tracks):
            lb = theme.label(self.root, t["name"], "muted", theme.track_colour(c))
            lb.set_pos(left + c * col_w + px(22), head_h)
        theme.label(self.root, "Repeat", "muted").set_pos(left + len(tracks) * col_w + px(22), head_h)
        row_h = px(50)
        top = head_h + px(28)
        if not p.get("song"):
            theme.label(self.root, "Add a scene to start the song.", "muted").set_pos(px(16), top + px(10))
        rows = theme.box(self.root, 0, top, w, h - top - px(8))
        rows.add_flag(lv.OBJ_FLAG.SCROLLABLE)
        rows.set_scroll_dir(lv.DIR.VER)
        for r, scene in enumerate(p.get("song", [])):
            yy = r * (row_h + px(6))
            row = theme.box(rows, px(10), yy, w - px(20), row_h, theme.RAISED, px(6))
            num = theme.button(row, str(r + 1), icon="play_arrow", w=left - px(16), h=row_h - px(12))
            num.obj.set_pos(px(4), px(6))
            num.on(lv.EVENT.CLICKED, (lambda rr: lambda: self.app.play_scene(rr))(r))
            parts = {"num": num.obj, "letters": []}
            self.app.tips.add(num.obj, "Play from scene %d" % (r + 1))
            for c, t in enumerate(tracks):
                i = scene["patterns"].get(str(t["id"]), -1)
                b = theme.button(row, LETTERS[i] if i >= 0 else "-", w=col_w - px(16), h=row_h - px(12))
                b.obj.set_pos(left - px(10) + c * col_w + px(14), px(6))
                b.on(lv.EVENT.SHORT_CLICKED, (lambda rr, tt, bb: lambda: self.pick(rr, tt, bb))(r, t, b.obj))
                b.on(lv.EVENT.LONG_PRESSED, (lambda rr, tt: lambda: self.app.edit_scene_pattern(rr, tt["id"]))(r, t))
                parts["letters"].append(b.obj)
                self.app.tips.add(b.obj, "Click: choose its pattern.  Hold: edit it")
            rx = left - px(10) + len(tracks) * col_w + px(14)
            minus = theme.button(row, icon="remove", w=px(36), h=row_h - px(12))
            minus.obj.set_pos(rx, px(6))
            minus.on(lv.EVENT.CLICKED, (lambda rr: lambda: self.repeat(rr, -1))(r))
            theme.label(row, "x%d" % scene.get("repeat", 1), "title").set_pos(rx + px(46), (row_h - px(26)) // 2)
            plus = theme.button(row, icon="add", w=px(36), h=row_h - px(12))
            plus.obj.set_pos(rx + px(90), px(6))
            plus.on(lv.EVENT.CLICKED, (lambda rr: lambda: self.repeat(rr, 1))(r))
            rm = theme.button(row, icon="close", w=px(36), h=row_h - px(12))
            rm.obj.align(lv.ALIGN.RIGHT_MID, -px(8), 0)
            loop = theme.button(row, icon="repeat", w=px(44), h=row_h - px(12), checkable=True)
            loop.obj.align(lv.ALIGN.RIGHT_MID, -px(56), 0)
            dup = theme.button(row, icon="content_copy", w=px(44), h=row_h - px(12))
            dup.obj.align(lv.ALIGN.RIGHT_MID, -px(108), 0)
            dup.on(lv.EVENT.CLICKED, (lambda rr: lambda: self.duplicate(rr))(r))
            self.app.tips.add(dup.obj, "Duplicate this scene (every track's pattern)")
            dup_obj = dup.obj
            # Move the scene up or down the song.
            moves = []
            for k, (icon, d, tip) in enumerate((("expand_more", 1, "Move this scene down"),
                                                 ("expand_less", -1, "Move this scene up"))):
                mv = theme.button(row, icon=icon, w=px(40), h=row_h - px(12))
                mv.obj.align(lv.ALIGN.RIGHT_MID, -px(160) - k * px(46), 0)
                mv.on(lv.EVENT.CLICKED, (lambda rr, dd: lambda: self.move(rr, dd))(r, d))
                self.app.tips.add(mv.obj, tip)
                moves.append(mv.obj)
            # Its name (Intro, Verse...), after the repeats; click to name it.
            name = theme.label(row, scene.get("name") or "name", "title" if scene.get("name") else "muted")
            name.set_pos(rx + px(150), (row_h - (px(26) if scene.get("name") else px(18))) // 2)
            name.set_width(px(220))
            name.set_long_mode(lv.label.LONG_MODE.DOTS)
            name.add_flag(lv.OBJ_FLAG.CLICKABLE)
            name.set_ext_click_area(px(8))
            name.add_event_cb((lambda rr, lb: lambda e: self.rename(rr, lb))(r, name), lv.EVENT.CLICKED, None)
            self.app.tips.add(name, "Click to name this scene")
            loop.checked = p.get("loop_scene") == r
            loop.on(lv.EVENT.CLICKED, (lambda rr: lambda: self.app.loop_scene(rr))(r))
            parts["loop"] = loop.obj
            parts["dup"] = dup_obj
            parts["moves"] = moves
            parts["name"] = name
            self.app.tips.add(loop.obj, "Loop this scene")
            self.app.tips.add(rm.obj, "Remove this scene")
            self.app.tips.add(minus.obj, "Fewer repeats")
            self.app.tips.add(plus.obj, "More repeats")
            self.parts.append(parts)
            rm.on(lv.EVENT.CLICKED, (lambda rr: lambda: self.remove(rr))(r))
            self.rows.append(row)
        self.list = rows
        rows.update_layout()
        rows.scroll_to_y(scrolled, 0)

    def add_scene(self):
        p = self.app.project
        # A new scene is what's playing now: each track's own pattern.
        scene = {"patterns": {str(t["id"]): model.shown_pattern(t) for t in p["tracks"]}, "repeat": 1}
        self.app.history.do(p, {"op": "insert", "path": ["song", -1], "value": scene})
        self.build()

    def move(self, r, delta):
        """Scene ``r`` one place up or down the song (one undo step)."""
        import json

        p = self.app.project
        to = r + delta
        if not 0 <= to < len(p["song"]):
            return
        scene = json.loads(json.dumps(p["song"][r]))
        h = self.app.history
        h.begin()
        self.app.edit({"op": "remove", "path": ["song", r]})
        self.app.edit({"op": "insert", "path": ["song", to], "value": scene})
        h.end()
        if p.get("loop_scene") == r:
            self.app.engines.op({"op": "set", "path": ["loop_scene"], "value": to})
        self.build()

    def rename(self, r, label):
        """A one-line field over the name: Enter keeps it, Escape doesn't,
        clicking away keeps what's typed."""
        self.close_rename()
        a = lv.area_t()
        label.get_coords(a)
        scr = self.app.screen
        ta = lv.textarea(scr)
        ta.set_one_line(True)
        ta.set_max_length(24)
        ta.set_text(self.app.project["song"][r].get("name") or "")
        ta.set_placeholder_text("Intro, Verse...")
        ta.set_size(px(240), px(40))
        ta.set_pos(a.x1 - px(6), (a.y1 + a.y2) // 2 - px(20))
        ta.set_style_bg_color(theme.c(theme.PANEL), 0)
        ta.set_style_text_color(theme.c(theme.TEXT), 0)
        ta.set_style_border_color(theme.c(theme.ACCENT), 0)
        theme.style(ta, "text")
        self.renaming = (ta, r)
        ta.add_event_cb(lambda e: self.finish_rename(True), lv.EVENT.READY, None)
        ta.add_event_cb(lambda e: self.finish_rename(True), lv.EVENT.DEFOCUSED, None)

        def key(e):
            if e.get_key() == lv.KEY.ESC:
                self.finish_rename(False)

        ta.add_event_cb(key, lv.EVENT.KEY, None)
        lv.group_focus_obj(ta)
        self.app.files.typing(True)

    def finish_rename(self, keep):
        if getattr(self, "renaming", None) is None:
            return
        ta, r = self.renaming
        text = ta.get_text().strip()
        self.close_rename()
        p = self.app.project
        if keep and r < len(p["song"]) and text != (p["song"][r].get("name") or ""):
            self.app.edit({"op": "set", "path": ["song", r, "name"], "value": text})
        self.build()

    def close_rename(self):
        if getattr(self, "renaming", None) is not None:
            ta, _ = self.renaming
            self.renaming = None
            ta.delete()
            self.app.files.typing(False)

    def duplicate(self, r):
        """A copy of scene ``r`` right after it."""
        import json

        p = self.app.project
        self.app.history.do(p, {"op": "insert", "path": ["song", r + 1], "value": json.loads(json.dumps(p["song"][r]))})
        self.build()

    def pick(self, r, t, anchor):
        """A row of A to H and "-" by the letter; a click on one sets it."""
        self.close_picker()
        scr = self.app.screen
        shade = theme.box(scr, 0, 0, scr.get_width(), scr.get_height())
        shade.add_flag(lv.OBJ_FLAG.CLICKABLE)
        shade.add_event_cb(lambda e: self.close_picker(), lv.EVENT.CLICKED, None)
        a = lv.area_t()
        anchor.get_coords(a)
        bw, n = px(40), 9
        box = theme.box(shade, 0, 0, n * (bw + px(4)) + px(8), bw + px(12), theme.PANEL, px(8))
        box.set_style_border_width(1, 0)
        box.set_style_border_color(theme.c(theme.LINE), 0)
        box.add_flag(lv.OBJ_FLAG.CLICKABLE)                     # clicks inside don't close it
        box.update_layout()
        x = max(px(4), min(scr.get_width() - box.get_width() - px(4), a.x1 - px(4)))
        box.set_pos(x, a.y2 + px(4))
        now = self.app.project["song"][r]["patterns"].get(str(t["id"]), -1)
        for k, label in enumerate(list(LETTERS) + ["-"]):
            idx = k if k < 8 else -1
            b = theme.button(box, label, w=bw, h=bw, checkable=True)
            b.obj.set_pos(px(4) + k * (bw + px(4)), px(6))
            b.checked = idx == now
            if 0 <= idx and not (idx < len(t["patterns"]) and t["patterns"][idx]["notes"]):
                b.obj.set_style_opa(110, 0)                     # an empty pattern: dim
            b.on(lv.EVENT.CLICKED, (lambda v: lambda: self.choose(r, t, v))(idx))
        self.picker = shade
        self.picker_buttons = box

    def close_picker(self):
        if getattr(self, "picker", None) is not None:
            self.picker.delete()
            self.picker = None
            self.picker_buttons = None

    def choose(self, r, t, idx):
        self.close_picker()
        p = self.app.project
        while idx >= len(t["patterns"]):
            self.app.engines.op({"op": "insert", "path": model.track_path(t["id"], "patterns", -1), "value": model.pattern()})
        self.app.history.do(p, {"op": "set", "path": ["song", r, "patterns", str(t["id"])], "value": idx})
        self.build()

    def cycle(self, r, t):
        p = self.app.project
        i = p["song"][r]["patterns"].get(str(t["id"]), -1)
        n = len(t["patterns"])
        nxt = -1 if i + 1 >= n else i + 1
        self.app.history.do(p, {"op": "set", "path": ["song", r, "patterns", str(t["id"])], "value": nxt})
        self.build()

    def repeat(self, r, delta):
        p = self.app.project
        v = max(1, min(16, p["song"][r].get("repeat", 1) + delta))
        self.app.history.do(p, {"op": "set", "path": ["song", r, "repeat"], "value": v})
        self.build()

    def remove(self, r):
        self.app.history.do(self.app.project, {"op": "remove", "path": ["song", r]})
        self.build()

    def playhead(self, tick):
        """Highlight the scene playing at ``tick``."""
        p = self.app.project
        song = p.get("song") or []
        if tick is None or not song:
            for row in self.rows:
                row.set_style_border_width(0, 0)
            return
        lengths = scene_lengths(p)
        loop = p.get("loop_scene")
        looping = loop is not None and 0 <= loop < len(song)
        t = tick % (lengths[loop] if looping else sum(lengths))
        at = 0
        for k, (row, length) in enumerate(zip(self.rows, lengths)):
            on = (k == loop) if looping else at <= t < at + length
            row.set_style_border_width(px(2) if on else 0, 0)
            row.set_style_border_color(theme.c(theme.ACCENT), 0)
            if on and k != self.playing:
                self.playing = k
                row.scroll_to_view(1)
            at += length


def scene_lengths(p):
    """Each scene's length in ticks: its longest pattern, times its repeats."""
    out = []
    for scene in p.get("song") or []:
        bars = 1
        for t in p["tracks"]:
            i = scene["patterns"].get(str(t["id"]), -1)
            if 0 <= i < len(t["patterns"]):
                bars = max(bars, t["patterns"][i]["bars"])
        out.append(bars * model.BAR * max(1, scene.get("repeat", 1)))
    return out
