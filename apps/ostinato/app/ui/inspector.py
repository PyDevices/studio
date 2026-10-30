"""The inspector: the selected track's instrument, its knobs, and its effects;
or, with the master selected, the master effects.

Every knob an instrument has, in rows of four; the first three rows are the
DMK-25's knob banks (bank A is the top row). The panel scrolls when an
instrument and its effects need more room than it has. Learn: switch it on,
touch a control on screen, move a knob or slider on the keyboard, and
they're bound.
"""

import lvgl as lv

import catalog
import model
import theme
from knob import Knob
from theme import px

HARDWARE_KNOBS = 12      # the DMK-25's three banks of four


class Inspector:
    def __init__(self, parent, rect, app):
        self.app = app
        self.rect = rect
        self.root = theme.panel(parent, rect)
        self.track_id = None
        self.master = False       # showing the master instead of a track
        self.knobs = []
        self.fx_knobs = []
        self.fx_index = None
        self.learning = False
        self.armed = None        # (target path, knob) waiting for a CC
        self.keep_scroll = False

    # -- building -----------------------------------------------------------

    def show(self, track_id):
        self.track_id = track_id
        self.master = False
        self.fx_index = None
        self.keep_scroll = False
        self.build()

    def show_master(self):
        self.master = True
        self.fx_index = None
        self.keep_scroll = False
        self.build()

    def track(self):
        return model.find_track(self.app.project, self.track_id)

    def path(self, *rest):
        """Where the shown rack lives: the master's, or the track's."""
        return ["master", *rest] if self.master else model.track_path(self.track_id, *rest)

    def fx_target(self, fx, i):
        """A MIDI-learn target for effect ``fx``'s knob ``i`` here."""
        return ["mfx", fx, i] if self.master else ["fx", self.track_id, fx, i]

    def build(self):
        """Draw the panel again, keeping its scroll unless the subject changed."""
        x, y, w, h = self.rect
        body = getattr(self, "body", None)
        scrolled = body.get_scroll_y() if body is not None and self.keep_scroll else 0
        self.keep_scroll = True
        self.root.clean()
        self.body = theme.box(self.root, 0, 0, w, h)
        self.body.add_flag(lv.OBJ_FLAG.SCROLLABLE)
        self.body.set_scroll_dir(lv.DIR.VER)
        self._build(w, h)
        if scrolled:
            self.body.update_layout()
            self.body.scroll_to_y(scrolled, 0)

    def _build(self, w, h):
        self.knobs = []
        self.fx_knobs = []
        self.step_buttons = []
        self.move_buttons = []
        if self.master:
            self.build_master(w, h)
            return
        t = self.track()
        if t is None:
            return
        idx = self.app.project["tracks"].index(t)
        colour = theme.track_colour(idx)
        info = catalog.instrument(t["instrument"])
        pad = px(16)

        head = theme.label(self.body, "INSTRUMENT", "muted")
        head.set_pos(pad, px(12))
        self.learn = theme.button(self.body, "Learn", icon="sensors", w=px(110), h=px(32), checkable=True)
        self.learn.obj.align(lv.ALIGN.TOP_RIGHT, -pad, px(6))
        self.learn.checked = self.learning
        self.learn.on(lv.EVENT.CLICKED, self.toggle_learn)
        self.learn_clear(pad)

        # The instruments by family (headings aren't choosable), with buttons
        # to step through them while playing, to hear each in turn.
        rows = catalog.grouped(t["kind"])
        self.inst_rows = rows
        dd = lv.dropdown(self.body)
        self._style_dropdown(dd)
        dd.set_options("\n".join(("-- %s --" % h) if h else title_of(n) for h, n in rows))
        names = [n for _, n in rows]
        if t["instrument"] in names:
            dd.set_selected(names.index(t["instrument"]))
        step_w = px(42)
        dd.set_size(w - 2 * pad - 2 * (step_w + px(6)), px(42))
        dd.set_pos(pad, px(46))

        def chose(e):
            n = names[dd.get_selected()]
            if n is None:                                   # a heading: put the choice back
                dd.set_selected(names.index(t["instrument"]))
                return
            self.choose_instrument(n)

        dd.add_event_cb(chose, lv.EVENT.VALUE_CHANGED, None)
        self.step_buttons = []
        for k, (icon, delta, tip) in enumerate((("chevron_left", -1, "Previous instrument"),
                                                 ("chevron_right", 1, "Next instrument"))):
            b = theme.button(self.body, icon=icon, w=step_w, h=px(42))
            b.obj.set_pos(w - pad - (2 - k) * step_w - (1 - k) * px(6), px(46))
            b.on(lv.EVENT.CLICKED, (lambda dd_: lambda: self.step_instrument(dd_))(delta))
            self.app.tips.add(b.obj, tip)
            self.step_buttons.append(b.obj)

        patches = sorted(info["patches"].items(), key=lambda kv: int(kv[0]))
        self.patch_ids = [int(k) for k, _ in patches]
        pd = lv.dropdown(self.body)
        self._style_dropdown(pd)
        pd.set_options("\n".join(v for _, v in patches) or "Default")
        if t.get("patch", 0) in self.patch_ids:
            pd.set_selected(self.patch_ids.index(t.get("patch", 0)))
        pd.set_size(w - 2 * pad, px(42))
        pd.set_pos(pad, px(96))
        pd.add_event_cb(lambda e: self.choose_patch(self.patch_ids[pd.get_selected()] if self.patch_ids else 0),
                        lv.EVENT.VALUE_CHANGED, None)

        size = (w - 2 * pad) // 4
        top = px(150)
        values = self.macro_values(t, info)
        defaults = list(info.get("patch_values", {}).get(str(t.get("patch", 0)), []))
        bound = list(self.app.project.get("bindings", {}).values())
        for i, label in enumerate(info["macros"]):
            r, c = divmod(i, 4)
            bipolar = i < len(info["modes"]) and info["modes"][i] == "BIPOLAR"
            k = Knob(self.body, pad + c * size, top + r * (size + px(40)), size, label, colour,
                     (lambda n: lambda v: self.set_macro(n, v))(i), bipolar,
                     default=defaults[i] if i < len(defaults) else 64, app=self.app)
            k.bound(["macro", self.track_id, i] in bound)
            k.set(values[i] if i < len(values) else 64)
            k.on_touch = (lambda n: lambda kn: self.touched(["macro", self.track_id, n], kn))(i)
            self.knobs.append(k)
        rows = (len(info["macros"]) + 3) // 4
        fx_top = top + rows * (size + px(40)) + px(10)
        if not info["macros"]:
            theme.label(self.body, "This instrument has no knobs.", "muted").set_pos(pad, top)
            fx_top = top + px(40)
        self.build_effects(t.get("rack", []), fx_top, w, h, pad, colour)

    def build_master(self, w, h):
        pad = px(16)
        theme.label(self.body, "MASTER", "muted").set_pos(pad, px(12))
        self.learn = theme.button(self.body, "Learn", icon="sensors", w=px(110), h=px(32), checkable=True)
        self.learn.obj.align(lv.ALIGN.TOP_RIGHT, -pad, px(6))
        self.learn.checked = self.learning
        self.learn.on(lv.EVENT.CLICKED, self.toggle_learn)
        self.learn_clear(pad)
        note = theme.label(self.body, "Effects here play on every track together: the whole mix.", "muted")
        note.set_width(w - 2 * pad)
        note.set_long_mode(lv.label.LONG_MODE.WRAP)
        note.set_pos(pad, px(50))
        rack = (self.app.project.get("master") or {}).get("rack", [])
        self.build_effects(rack, px(110), w, h, pad, theme.ACCENT)

    def build_effects(self, rack, top, w, h, pad, colour):
        self.move_buttons = []                 # the selected effect's down, up (for tests)
        head = theme.label(self.body, "EFFECTS", "muted")
        head.set_pos(pad, top)
        names = catalog.effects()
        add = lv.dropdown(self.body)
        self._style_dropdown(add)
        add.set_options("\n".join(names))
        add.set_text("Add effect")
        add.set_size(px(170), px(34))
        add.align(lv.ALIGN.TOP_RIGHT, -pad, top - px(8))
        add.add_event_cb(lambda e: self.add_effect(names[add.get_selected()]), lv.EVENT.VALUE_CHANGED, None)
        y = top + px(34)
        for i, e in enumerate(rack):
            row = theme.box(self.body, pad, y, w - 2 * pad, px(38), theme.RAISED, px(6))
            row.add_flag(lv.OBJ_FLAG.CLICKABLE)
            row.add_event_cb((lambda n: lambda ev: self.select_effect(n))(i), lv.EVENT.CLICKED, None)
            if i == self.fx_index:
                row.set_style_border_width(px(2), 0)
                row.set_style_border_color(theme.c(colour), 0)
            name = theme.label(row, e["name"], "text", theme.MUTED if e.get("bypass") else theme.TEXT)
            name.align(lv.ALIGN.LEFT_MID, px(12), 0)
            on = theme.button(row, icon="power_settings_new", w=px(34), h=px(30), checkable=True)
            on.checked = not e.get("bypass")
            on.obj.align(lv.ALIGN.RIGHT_MID, -px(42), 0)
            on.on(lv.EVENT.CLICKED, (lambda n, b: lambda: self.set_effect(n, "bypass", not b.checked))(i, on))
            self.app.tips.add(on.obj, "On / bypassed")
            rm = theme.button(row, icon="close", w=px(34), h=px(30))
            rm.obj.align(lv.ALIGN.RIGHT_MID, -px(4), 0)
            rm.on(lv.EVENT.CLICKED, (lambda n: lambda: self.remove_effect(n))(i))
            self.app.tips.add(rm.obj, "Remove this effect")
            if i == self.fx_index and len(rack) > 1:
                # The selected effect moves up or down the chain (order matters:
                # drive into delay isn't delay into drive).
                for k, (icon, delta, tip) in enumerate((("expand_more", 1, "Later in the chain"),
                                                         ("expand_less", -1, "Earlier in the chain"))):
                    mv = theme.button(row, icon=icon, w=px(34), h=px(30))
                    mv.obj.align(lv.ALIGN.RIGHT_MID, -px(80) - k * px(38), 0)
                    mv.on(lv.EVENT.CLICKED, (lambda n, dd: lambda: self.move_effect(n, dd))(i, delta))
                    self.app.tips.add(mv.obj, tip)
                    self.move_buttons.append(mv.obj)
            y += px(44)
        if self.fx_index is not None and self.fx_index < len(rack):
            e = rack[self.fx_index]
            info = catalog.effect(e["name"])
            vals = list(info.get("patch_values", {}).get("0", []))
            fx_defaults = list(vals)
            for k_, v in e.get("macros", {}).items():
                if int(k_) < len(vals):
                    vals[int(k_)] = v
            size = (w - 2 * pad) // 4
            for i, label in enumerate(info["macros"]):
                r, c = divmod(i, 4)
                k = Knob(self.body, pad + c * size, y + px(6) + r * (size + px(40)), size, label, colour,
                         (lambda n: lambda v: self.set_effect_macro(n, v))(i),
                         default=fx_defaults[i] if i < len(fx_defaults) else 64, app=self.app)
                k.bound(self.fx_target(self.fx_index, i) in list(self.app.project.get("bindings", {}).values()))
                k.set(vals[i] if i < len(vals) else 64)
                k.on_touch = (lambda n: lambda kn: self.touched(self.fx_target(self.fx_index, n), kn))(i)
                self.fx_knobs.append(k)
            if not info["macros"]:
                theme.label(self.body, "No settings to change.", "muted").set_pos(pad, y + px(6))

    def _style_dropdown(self, dd):
        dd.set_style_bg_color(theme.c(theme.RAISED), 0)
        dd.set_style_border_width(0, 0)
        dd.set_style_radius(px(6), 0)
        dd.set_style_text_color(theme.c(theme.TEXT), 0)
        theme.style(dd, "text")
        lst = dd.get_list()
        lst.set_style_bg_color(theme.c(theme.RAISED), 0)
        lst.set_style_text_color(theme.c(theme.TEXT), 0)
        lst.set_style_border_width(0, 0)
        theme.style(lst, "text")

    def macro_values(self, t, info):
        vals = list(info.get("patch_values", {}).get(str(t.get("patch", 0)), []))
        vals += [64] * (len(info["macros"]) - len(vals))
        for k, v in t.get("macros", {}).items():
            if int(k) < len(vals):
                vals[int(k)] = v
        return vals

    # -- edits ----------------------------------------------------------------

    def choose_instrument(self, name):
        t = self.track()
        if t is None or name == t["instrument"]:
            return
        h = self.app.history
        h.begin()                         # one undo puts the old sound back, knobs and all
        self.app.edit({"op": "set", "path": model.track_path(t["id"], "macros"), "value": {}})
        self.app.edit({"op": "set", "path": model.track_path(t["id"], "patch"), "value": 0})
        self.app.edit({"op": "set", "path": model.track_path(t["id"], "instrument"), "value": name})
        h.end()
        self.app.track_changed(t["id"])

    def choose_patch(self, patch):
        t = self.track()
        h = self.app.history
        h.begin()
        self.app.edit({"op": "set", "path": model.track_path(t["id"], "macros"), "value": {}})
        self.app.edit({"op": "set", "path": model.track_path(t["id"], "patch"), "value": patch})
        h.end()
        self.app.track_changed(t["id"])

    def set_macro(self, index, value):
        self.app.edit({"op": "set", "path": model.track_path(self.track_id, "macros", str(index)),
                       "value": value}, merge=True)

    def set_effect_macro(self, index, value):
        self.app.edit({"op": "set", "path": self.path("rack", self.fx_index, "macros", str(index)),
                       "value": value}, merge=True)

    def add_effect(self, name):
        self.app.edit({"op": "insert", "path": self.path("rack", -1),
                             "value": {"name": name, "bypass": False, "macros": {}}})
        node, key = model.resolve(self.app.project, self.path("rack"))
        self.fx_index = len(node[key]) - 1
        self.rack_changed()

    def remove_effect(self, index):
        self.app.edit({"op": "remove", "path": self.path("rack", index)})
        self.fx_index = None
        self.rack_changed()

    def move_effect(self, index, delta):
        """Move effect ``index`` one place along the chain, settings and all."""
        import json

        node, key = model.resolve(self.app.project, self.path("rack"))
        rack = node[key]
        to = index + delta
        if not 0 <= to < len(rack):
            return
        effect = json.loads(json.dumps(rack[index]))
        h = self.app.history
        h.begin()
        self.app.edit({"op": "remove", "path": self.path("rack", index)})
        self.app.edit({"op": "insert", "path": self.path("rack", to), "value": effect})
        h.end()
        self.fx_index = to
        self.rack_changed()

    def set_effect(self, index, key, value):
        self.app.edit({"op": "set", "path": self.path("rack", index, key), "value": value})
        self.rack_changed()

    def rack_changed(self):
        self.build()
        if self.master:                   # the Master row lists its effects
            self.app.tracks.build()
            self.app.tracks.show_selected("master")

    def select_effect(self, index):
        self.fx_index = index
        self.build()

    # -- MIDI: default knob banks, and learn -------------------------------------

    def toggle_learn(self):
        self.learning = self.learn.checked
        if self.learning:
            self.app.say("Learn: touch a knob or slider on screen, then move a control on your keyboard", 600)
        else:
            self.app.say("", 0)
            if self.armed:
                self.armed[1].armed(False)
                self.armed = None
        self.build()

    def learn_clear(self, pad):
        """While learning: a button to forget every learned control."""
        n = len(self.app.project.get("bindings", {}))
        if not (self.learning and n):
            return
        b = theme.button(self.body, "Clear %d" % n, w=px(100), h=px(32))
        b.obj.align(lv.ALIGN.TOP_RIGHT, -pad - px(118), px(6))
        b.on(lv.EVENT.CLICKED, self.clear_bindings)
        self.app.tips.add(b.obj, "Forget every learned control")

    def clear_bindings(self):
        self.app.edit({"op": "set", "path": ["bindings"], "value": {}})
        self.app.say("Learned controls cleared; the keyboard's knob banks work as before", 3.0)
        self.build()

    def step_instrument(self, delta):
        """The previous or next instrument in the list (skipping headings)."""
        t = self.track()
        names = [n for _, n in self.inst_rows if n]
        if not names or t is None:
            return
        i = names.index(t["instrument"]) if t["instrument"] in names else 0
        self.choose_instrument(names[(i + delta) % len(names)])

    def touched(self, target, knob):
        if not self.learning:
            return
        if self.armed:
            self.armed[1].armed(False)
        self.armed = (target, knob)
        knob.armed(True)

    def control(self, chan, cc, value):
        """A CC from the keyboard. Returns what it did, for the status line
        ("Keys: Cutoff 102"), or None if it wasn't used here."""
        key = "%d:%d" % (chan, cc)
        if self.learning and self.armed:
            self.app.engines.op({"op": "set", "path": ["bindings", key], "value": self.armed[0]})
            self.armed[1].armed(False)
            self.armed = None
            self.build()
            return "Learned: this control now turns that knob"
        target = self.app.project.get("bindings", {}).get(key)
        if target is None and chan == 0 and 30 <= cc <= 41:
            # The DMK-25's knob banks turn what's on screen: the track's
            # instrument, or with the master shown, its selected effect.
            if self.master:
                if self.fx_index is None:
                    return "Master: select an effect for the knobs to turn"
                target = ["mfx", self.fx_index, cc - 30]
            else:
                target = ["macro", self.track_id, cc - 30]
        if target is None:
            return None
        return self.apply_target(target, value) or "%s %d" % (key, value)

    def apply_target(self, target, value):
        """Turn ``target`` to ``value``; returns what it turned, in words."""
        kind = target[0]
        if kind == "macro":
            _, tid, i = target
            t = model.find_track(self.app.project, tid)
            if t is None:
                return None
            labels = catalog.instrument(t["instrument"])["macros"]
            if i >= len(labels):
                return "%s has no knob %d" % (t["name"], i + 1)
            self.app.edit({"op": "set", "path": model.track_path(tid, "macros", str(i)), "value": value}, merge=True)
            if not self.master and tid == self.track_id and i < len(self.knobs):
                self.knobs[i].set(value)
            return "%s: %s %d" % (t["name"], labels[i], value)
        if kind == "fx":
            _, tid, fx, i = target
            t = model.find_track(self.app.project, tid)
            if t and fx < len(t["rack"]):
                labels = catalog.effect(t["rack"][fx]["name"])["macros"]
                if i >= len(labels):
                    return None
                self.app.edit({"op": "set", "path": model.track_path(tid, "rack", fx, "macros", str(i)),
                               "value": value}, merge=True)
                if not self.master and tid == self.track_id and fx == self.fx_index and i < len(self.fx_knobs):
                    self.fx_knobs[i].set(value)
                return "%s: %s %s %d" % (t["name"], t["rack"][fx]["name"], labels[i], value)
            return None
        if kind == "mfx":
            _, fx, i = target
            rack = (self.app.project.get("master") or {}).get("rack", [])
            if fx < len(rack):
                labels = catalog.effect(rack[fx]["name"])["macros"]
                if i >= len(labels):
                    return "Master: %s has no knob %d" % (rack[fx]["name"], i + 1)
                self.app.edit({"op": "set", "path": ["master", "rack", fx, "macros", str(i)], "value": value},
                              merge=True)
                if self.master and fx == self.fx_index and i < len(self.fx_knobs):
                    self.fx_knobs[i].set(value)
                return "Master: %s %s %d" % (rack[fx]["name"], labels[i], value)
            return None
        if kind == "pan":
            _, tid = target
            self.app.set_track(tid, "pan", value)
            self.app.tracks.show_pan(tid, value)
            t = model.find_track(self.app.project, tid)
            return "%s: Pan %d" % (t["name"] if t else "Track", value)
        if kind == "volume":
            _, tid = target
            self.app.set_track(tid, "volume", value)
            self.app.tracks.show_volume(tid, value)
            t = model.find_track(self.app.project, tid)
            return "%s: Volume %d" % (t["name"] if t else "Track", value)
        return None


def title_of(name):
    """An instrument's name in the list, marked when it plays one note at a time."""
    info = catalog.instrument(name)
    return info["title"] + ("  (mono)" if info.get("voices") == 1 else "")
