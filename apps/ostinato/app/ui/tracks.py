"""The track list: one row per track, select to play it, mute, solo, volume.

Under the tracks, the master: its volume, and select it to see the master
effects in the inspector. The rows share the panel's height, and each has a
left and right meter at its right edge.
"""

import lvgl as lv

import catalog
import fonts
from songlink import model
import theme
from knob import Knob
from meter import StereoMeter
from theme import px

METER_W = 16          # both columns of a row's meter, before scaling


class TrackList:
    def __init__(self, parent, rect, app):
        self.app = app
        self.rect = rect
        self.root = theme.panel(parent, rect)
        self.rows = []
        self.build()

    def build(self):
        x, y, w, h = self.rect
        self.root.clean()
        self.rows = []
        head = theme.label(self.root, "TRACKS", "muted")
        head.set_pos(px(16), px(12))
        # Rows follow the fonts' real line heights: at small windows the fonts
        # stop shrinking before the scale does.
        self.lh_name = fonts.t(px(20)).line_height
        self.lh_info = fonts.t(px(14)).line_height
        self.lh_text = fonts.t(px(16)).line_height
        self.lh_big = fonts.t(px(28)).line_height
        # Name, instrument and the pattern letter at the bottom; or M and S over
        # a pan knob of at least px(40).
        least = max(px(10) + self.lh_name + px(2) + 2 * self.lh_info + px(8) + self.lh_big + px(8),
                    px(52) + px(40) + 2 * self.lh_info + px(4))
        top = px(40)
        n = len(self.app.project["tracks"])
        gap = px(8)
        # Every row (the master's too) shares what's left of the panel, the
        # master a gap further down; never shorter than its text needs.
        room = h - top - px(10) - gap * n - gap
        row_h = max(least, room // (n + 1))
        for i, t in enumerate(self.app.project["tracks"]):
            self.rows.append(self._row(i, t, px(10), top + i * (row_h + gap), w - px(20), row_h))
        self.rows.append(self._master_row(px(10), top + n * (row_h + gap) + gap, w - px(20), row_h))
        self.show_armed(getattr(self, "armed", None))
        self.show_patterns()

    def _master_row(self, x, y, w, h):
        app = self.app
        m = app.project.get("master") or {}
        row = theme.box(self.root, x, y, w, h, theme.RAISED, px(8))
        row.add_flag(lv.OBJ_FLAG.CLICKABLE)
        row.add_event_cb(lambda e: app.select_master(), lv.EVENT.CLICKED, None)
        theme.box(row, 0, 0, px(6), h, theme.TEXT, px(3))
        theme.label(row, "Master", "title").set_pos(px(18), px(10))
        names = [e["name"] + (" (off)" if e.get("bypass") else "") for e in m.get("rack", [])]
        info = theme.label(row, ", ".join(names) if names else "No effects", "muted")
        info.set_pos(px(18), px(10) + self.lh_name + px(2))
        right = w - meter_room()
        info.set_width(right - px(60))
        info.set_long_mode(lv.label.LONG_MODE.WRAP)
        vol = volume_slider(app, row, right, h, theme.TEXT, m.get("volume", 100),
                            lambda v: app.set_master("volume", v), None)
        return {"id": "master", "row": row, "volume": vol, "meter": add_meter(row, w, h)}

    def _row(self, index, t, x, y, w, h):
        """A track: its name, then the pattern it plays, then its instrument; a pan
        knob under those; M and S at the top right; then a vertical volume
        slider and the meters, as tall as the row."""
        app = self.app
        colour = theme.track_colour(index)
        row = theme.box(self.root, x, y, w, h, theme.RAISED, px(8))
        row.add_flag(lv.OBJ_FLAG.CLICKABLE)
        row.add_event_cb(lambda e: app.select_track(t["id"]), lv.EVENT.CLICKED, None)
        theme.box(row, 0, 0, px(6), h, colour, px(3))
        name = theme.label(row, t["name"], "title")
        name.set_pos(px(18), px(10))
        # A red dot after the name when this track will record.
        dot = theme.box(row, 0, 0, px(10), px(10), theme.REC, px(5))
        name.update_layout()
        dot.set_pos(px(18) + name.get_width() + px(8), px(10) + (self.lh_name - px(10)) // 2)
        dot.add_flag(lv.OBJ_FLAG.HIDDEN)
        # The pattern this track plays (and where it's switching to), bottom left, large.
        letter = theme.label(row, "", "big", colour)
        letter.set_pos(px(18), h - px(8) - self.lh_big)
        info = catalog.instrument(t["instrument"])
        inst = theme.label(row, info["title"], "muted")
        inst.set_pos(px(18), px(10) + self.lh_name + px(2))
        patch = theme.label(row, info["patches"].get(str(t.get("patch", 0)), "Default"), "muted")
        patch.set_pos(px(18), px(10) + self.lh_name + px(2) + self.lh_info)
        right = w - meter_room()                  # the meters sit right of this
        # Three columns: the words on the left, M and S over the pan knob in the
        # middle, the volume and meters on the right.
        mid0, mid1 = int(w * 0.46), right - px(36)
        centre = (mid0 + mid1) // 2
        mute = theme.button(row, "M", w=px(32), h=px(28), checkable=True)
        mute.obj.set_pos(centre - px(36), px(8))
        mute.checked = t.get("mute", False)
        mute.on(lv.EVENT.CLICKED, lambda: app.set_mix_state(t["id"], "mute", mute.checked))
        app.tips.add(mute.obj, "Mute")
        solo = theme.button(row, "S", w=px(32), h=px(28), checkable=True)
        solo.obj.set_pos(centre + px(4), px(8))
        solo.checked = t.get("solo", False)
        solo.on(lv.EVENT.CLICKED, lambda: app.set_mix_state(t["id"], "solo", solo.checked))
        app.tips.add(solo.obj, "Solo")
        vol = volume_slider(app, row, right, h, colour, t.get("volume", 100),
                            lambda v: app.set_track(t["id"], "volume", v), ["volume", t["id"]])
        # Pan: a knob like the effects', centred under M and S and filling the
        # room left there; double-click centres it.
        top = px(8) + px(28) + px(16)                     # clear of M and S
        size = max(px(40), min(mid1 - mid0, h - top - 2 * self.lh_info - px(4)))
        pan = Knob(row, centre - size // 2, top, size, "Pan", colour, lambda v: app.set_track(t["id"], "pan", v),
                   bipolar=True, default=64, app=app)
        pan.set(t.get("pan", 64))
        for lb in (inst, patch):
            lb.set_width(mid0 - px(22))                         # stop short of the middle column
            lb.set_height(self.lh_info)                         # one line: a long name ends in dots
            lb.set_long_mode(lv.label.LONG_MODE.DOTS)
        pan.on_touch = lambda kn: app.inspector.touched(["pan", t["id"]], kn)
        app.tips.add(pan.root, "Pan: drag up or down (double-click: centre)")
        return {"id": t["id"], "pan": pan, "row": row, "volume": vol, "mute": mute, "solo": solo, "meter": add_meter(row, w, h),
                "armed": dot, "letter": letter}

    def show_selected(self, track_id):
        for r in self.rows:
            r["row"].set_style_border_width(px(2) if r["id"] == track_id else 0, 0)
            r["row"].set_style_border_color(theme.c(theme.ACCENT), 0)

    def show_patterns(self):
        """Each track's pattern letter: "A", or "A > B" while a switch waits."""
        for r in self.rows:
            lb = r.get("letter")
            t = model.find_track(self.app.project, r["id"]) if lb is not None else None
            if t is None:
                continue
            text = "ABCDEFGH"[t.get("current", 0)]
            if t.get("next"):
                text += " > " + "ABCDEFGH"[t["next"]["pattern"]]
            lb.set_text(text)

    def show_armed(self, track_id):
        """Mark the track that will record (None: no track is armed)."""
        self.armed = track_id
        for r in self.rows:
            dot = r.get("armed")
            if dot is not None:
                if r["id"] == track_id:
                    dot.remove_flag(lv.OBJ_FLAG.HIDDEN)
                else:
                    dot.add_flag(lv.OBJ_FLAG.HIDDEN)

    def show_meters(self, levels):
        """``levels``: row id (track id, or "master") -> [left, right] dB, or None."""
        for r in self.rows:
            r["meter"].show(levels.get(r["id"]))

    def show_pan(self, track_id, value):
        for r in self.rows:
            if r["id"] == track_id and r.get("pan") is not None:
                r["pan"].set(value)

    def show_volume(self, track_id, value):
        for r in self.rows:
            if r["id"] == track_id:
                r["volume"].set_value(value, 0)


class SliderMark:
    """A slider standing in for a knob in MIDI-learn: it shows it's armed."""

    def __init__(self, slider):
        self.slider = slider

    def armed(self, on):
        self.slider.set_style_outline_width(px(2) if on else 0, 0)
        self.slider.set_style_outline_color(theme.c(theme.ACCENT), 0)
        self.slider.set_style_outline_pad(px(4), 0)


def volume_slider(app, row, right, h, colour, value, changed, learn):
    """A vertical volume slider in the column left of the meters, as tall as they are."""
    vol = lv.slider(row)
    vol.set_range(0, 127)
    vol.set_value(value, 0)
    vol.set_size(px(8), h - px(16))                # taller than wide: LVGL makes it vertical
    vol.set_pos(right - px(26), px(8))
    vol.set_ext_click_area(px(10))
    vol.set_style_bg_color(theme.c(theme.LINE), lv.PART.MAIN)
    vol.set_style_bg_color(theme.c(colour), lv.PART.INDICATOR)
    vol.set_style_bg_color(theme.c(theme.TEXT), lv.PART.KNOB)
    vol.add_event_cb(lambda e: changed(vol.get_value()), lv.EVENT.VALUE_CHANGED, None)
    app.tips.add(vol, "Volume")
    if learn is not None:
        vol.add_event_cb(lambda e: app.inspector.touched(learn, SliderMark(vol)), lv.EVENT.PRESSED, None)
    return vol


def meter_room():
    return px(METER_W) + px(12)


def add_meter(row, w, h):
    return StereoMeter(row, w - px(METER_W) - px(8), px(8), px(METER_W), h - px(16))


def describe(t):
    info = catalog.instrument(t["instrument"])
    pname = info["patches"].get(str(t.get("patch", 0)), "")
    # The patch only when it says something: "Default" is just noise here.
    return "%s   %s" % (info["title"], pname) if pname and pname != "Default" else info["title"]
