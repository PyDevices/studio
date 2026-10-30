"""Files: save and open projects, autosave, and export WAV and MIDI.

The file pickers belong to the page (index.html in a browser,
desktop/ui/page.py on the desktop), so the UI asks the page through
``page.send`` and the page answers. A WAV is rendered by the engines
themselves, each its own tracks, faster than real time; the page sums them
and writes the file.
"""

import json

import lvgl as lv

import model
import page
import theme
from theme import px

SR = 48000
TAIL_SECONDS = 2.0
to_page = page.send


class Files:
    def __init__(self, app):
        self.app = app
        self.menu = None
        self.progress = None
        self.dirty_at = None
        page.listen(self.on_page)
        lv.timer_create(lambda t: self.autosave(), 2000, None)

    # -- the menu -----------------------------------------------------------------

    def toggle_menu(self, anchor):
        if self.menu is not None:
            self.menu.delete()
            self.menu = None
            return
        items = [("New", "add", self.new), ("Demo song", "album", self.demo), ("Open...", "folder_open", self.open),
                 ("Save", "save", lambda: self.save(quick=True)), ("Save as...", "save", self.save), ("Export WAV...", "file_download", self.export_wav),
                 ("Export MIDI...", "music_note", self.export_midi)]
        w, h = px(230), px(44)
        self.menu = theme.box(self.app.screen, 0, 0, w + px(12), len(items) * (h + px(4)) + px(8), theme.RAISED, px(8))
        self.menu.set_style_border_width(1, 0)
        self.menu.set_style_border_color(theme.c(theme.LINE), 0)
        a = lv.area_t()
        anchor.get_coords(a)
        self.menu.set_pos(a.x1, a.y2 + px(4))
        for i, (label, icon, fn) in enumerate(items):
            b = theme.button(self.menu, label, icon=icon, w=w, h=h)
            b.obj.set_pos(px(6), px(6) + i * (h + px(4)))
            b.on(lv.EVENT.CLICKED, (lambda f: lambda: self._pick(f))(fn))

    def _pick(self, fn):
        if self.menu is not None:
            self.menu.delete()
            self.menu = None
        fn()

    # -- actions --------------------------------------------------------------------

    def new(self):
        self.confirm(lambda: (self.app.load_project(model.new_project()), self.app.say("New project")))

    def demo(self, ask=True):
        """The demo song: a SoundHelix composition, preloaded as patterns and scenes."""
        go = lambda: page.run(self._demo())
        self.confirm(go) if ask else go()

    async def _demo(self):
        try:
            project = json.loads(await page.fetch_text("assets/demo_song.json"))
        except Exception:
            self.app.say("The demo song didn't load")
            return
        self.app.load_project(project)
        if self.app.help.box is not None:          # the first-run guide stays on top
            self.app.help.box.move_foreground()
        self.app.say("Demo song, composed by SoundHelix: press play")

    def open(self):
        self.confirm(lambda: to_page({"kind": "open"}))

    def typing(self, on):
        """A text field has the keys: the page holds its shortcuts (Space, R,
        Delete...) until it closes."""
        to_page({"kind": "typing", "on": bool(on)})

    # -- before throwing work away -------------------------------------------------

    def confirm(self, then):
        """Run ``then`` now if nothing would be lost; otherwise ask first."""
        if not self.app.unsaved():
            then()
            return
        self.close_confirm()
        scr = self.app.screen
        sw, sh = scr.get_width(), scr.get_height()
        shade = theme.box(scr, 0, 0, sw, sh, 0x000000)
        shade.set_style_bg_opa(140, 0)
        shade.add_flag(lv.OBJ_FLAG.CLICKABLE)
        w, h = px(520), px(200)
        card = theme.box(shade, (sw - w) // 2, (sh - h) // 2, w, h, theme.PANEL, px(14))
        card.set_style_border_width(1, 0)
        card.set_style_border_color(theme.c(theme.LINE), 0)
        theme.label(card, "Discard your changes?", "title").set_pos(px(28), px(24))
        note = theme.label(card, "This project has changes you haven't saved.", "muted")
        note.set_pos(px(28), px(66))
        keep = theme.button(card, "Cancel", w=px(130), h=px(44))
        keep.obj.align(lv.ALIGN.BOTTOM_RIGHT, -px(334), -px(22))
        keep.on(lv.EVENT.CLICKED, self.close_confirm)
        first = theme.button(card, "Save first", w=px(150), h=px(44))
        first.obj.align(lv.ALIGN.BOTTOM_RIGHT, -px(176), -px(22))
        first.on(lv.EVENT.CLICKED, lambda: (self.close_confirm(), self.save(quick=True, then=then)))
        drop = theme.button(card, "Discard", w=px(140), h=px(44))
        drop.obj.align(lv.ALIGN.BOTTOM_RIGHT, -px(24), -px(22))
        drop.label.set_style_text_color(theme.c(theme.REC), 0)
        drop.on(lv.EVENT.CLICKED, lambda: (self.close_confirm(), then()))
        self.confirm_box = shade
        self.confirm_buttons = {"cancel": keep.obj, "save": first.obj, "discard": drop.obj}

    def close_confirm(self):
        if getattr(self, "confirm_box", None) is not None:
            self.confirm_box.delete()
            self.confirm_box = None
            self.confirm_buttons = None

    def save(self, quick=False, then=None):
        """Save: ``quick`` writes back to the last file saved or opened (the
        first time, it asks where). ``then`` runs once it's saved."""
        p = self.app.project
        self.after_save = then
        to_page({"kind": "save", "quick": quick, "name": p.get("name", "Untitled"), "json": json.dumps(p)})

    def export_midi(self):
        data = smf(self.app.project)
        to_page({"kind": "export-midi", "name": self.app.project.get("name", "Untitled"), "bytes": list(data)})

    def export_wav(self):
        frames = int((song_ticks(self.app.project) / ticks_per_second(self.app.project) + TAIL_SECONDS) * SR)
        self.app.stop()
        to_page({"kind": "export-wav", "name": self.app.project.get("name", "Untitled"), "frames": frames})
        self.show_progress("Exporting %d:%02d of audio" % divmod(round(frames / SR), 60))

    # -- the export's progress card: it stays up until the file is written -----------

    def show_progress(self, title):
        self.hide_progress()
        scr = self.app.screen
        sw, sh = scr.get_width(), scr.get_height()
        shade = theme.box(scr, 0, 0, sw, sh, 0x000000)
        shade.set_style_bg_opa(120, 0)
        shade.add_flag(lv.OBJ_FLAG.CLICKABLE)          # nothing underneath while it renders
        w, h = px(520), px(170)
        card = theme.box(shade, (sw - w) // 2, (sh - h) // 2, w, h, theme.PANEL, px(14))
        card.set_style_border_width(1, 0)
        card.set_style_border_color(theme.c(theme.LINE), 0)
        theme.label(card, title, "title").set_pos(px(28), px(24))
        self.bar = lv.bar(card)
        self.bar.set_size(w - px(56), px(14))
        self.bar.set_pos(px(28), px(76))
        self.bar.set_range(0, 100)
        self.bar.set_style_bg_color(theme.c(theme.RAISED), 0)
        self.bar.set_style_bg_color(theme.c(theme.ACCENT), lv.PART.INDICATOR)
        self.bar_text = theme.label(card, "Starting...", "muted")
        self.bar_text.set_pos(px(28), px(110))
        self.progress = shade

    def set_progress(self, part):
        if self.progress is not None:
            self.bar.set_value(round(100 * part), 0)
            self.bar_text.set_text("%d%%" % round(100 * part))

    def hide_progress(self):
        if self.progress is not None:
            self.progress.delete()
            self.progress = None

    def changed(self):
        import time

        self.dirty_at = time.monotonic()

    def autosave(self):
        import time

        if self.dirty_at is not None and time.monotonic() - self.dirty_at > 1.5:
            self.dirty_at = None
            to_page({"kind": "autosave", "json": json.dumps(self.app.project)})

    # -- answers from the page ------------------------------------------------------

    def on_page(self, data):
        kind = data.get("kind")
        if kind in ("loaded", "restore"):
            try:
                project = json.loads(data["json"])
            except Exception:
                self.app.say("That file isn't an Ostinato project")
                return
            if project.get("version") != model.VERSION:
                self.app.say("That project is from a different version")
                return
            self.app.load_project(project)
            if kind == "restore":
                # Autosaved, not necessarily saved to a file: treat it as unsaved.
                self.app.saved_version = -1
            self.app.say("Restored your last session" if kind == "restore" else "Opened %s" % project.get("name"))
        elif kind == "saved":
            self.app.saved_version = self.app.history.version
            self.app.say("Saved %s" % data.get("file", ""))
            then, self.after_save = getattr(self, "after_save", None), None
            if then is not None:
                then()
        elif kind == "render-progress":
            self.set_progress(data["part"])
            if data.get("master") and self.progress is not None:
                self.bar_text.set_text("Master effects...")
        elif kind == "exported":
            self.hide_progress()
            self.app.say("Exported %s" % data.get("file", ""))
        elif kind == "failed":
            self.hide_progress()
            self.after_save = None                  # "Save first" didn't save: don't go on
            self.app.say(data.get("why", "That didn't work"))
        elif kind == "project":
            to_page({"kind": "project", "json": json.dumps(self.app.project)})   # for tests
        elif kind == "wheel":
            self.app.editor.wheel(data.get("dy", 0), data.get("x", 0), data.get("y", 0))
        elif kind == "cc":                          # for tests: a control change, as if from the keyboard
            self.app.midi_seen += 1
            self.app.on_midi([0xB0 | int(data.get("chan", 0)), int(data["cc"]), int(data["value"])])
        elif kind == "inspect":                     # for tests: what the inspector shows
            if data.get("master"):
                self.app.select_master()
            if data.get("fx") is not None:
                self.app.inspector.select_effect(int(data["fx"]))
        elif kind == "settled":                     # for tests: the last load is built
            to_page({"kind": "settled", "ok": self.app.engines.settled()})
        elif kind == "reset":                       # for tests: as if just opened, without a reload
            self.app.help.close()
            self.close_confirm()
            if self.menu is not None:
                self.menu.delete()
                self.menu = None
            if self.app.rec_armed:
                self.app.transport.rec.checked = False
                self.app.toggle_record()
            self.app.load_project(json.loads(data["json"]))
        elif kind == "masterstats":                 # for tests
            st = [s or {} for s in self.app.engines.status]
            meters = {str(k): v for s in st for k, v in (s.get("meters") or {}).items()}
            to_page({"kind": "masterstats", "stats": self.app.engines.master_status,
                     "engines": [s.get("underruns") for s in st], "meters": meters})
        elif kind == "uistats":
            ms = list(self.app.pass_ms)
            self.app.pass_ms.clear()
            st = self.app.engines.status
            to_page({"kind": "uistats", "pass_ms": ms,
                     "ui_heap_mb": page.heap_mb(),
                     "engine_heap_mb": [s.get("heap_mb") if s else None for s in st],
                     "engine_load": [s.get("load") if s else None for s in st],
                     "engine_status": [{k: v for k, v in (s or {}).items() if k != "meters"} for s in st],
                     "assignment": {str(k): v for k, v in self.app.engines.assignment.items()}})
        elif kind == "instrument":
            # Tests: the same change the instrument picker makes.
            t = self.app.project["tracks"][int(data["index"])]
            self.app.inspector.track_id = t["id"]
            self.app.inspector.choose_instrument(data["name"])
        elif kind == "key":
            self.app.key(data.get("key"))
        elif kind == "op":                          # for tests: one edit, as the UI would make it
            self.app.engines.op(data["op"])
            self.app.tracks.build()
        elif kind == "first-run":
            self.demo(ask=False)
            self.app.help.open(first_run=True)
        elif kind == "save-now":
            self.dirty_at = None
            to_page({"kind": "autosave", "json": json.dumps(self.app.project), "then": data.get("then")})
        elif kind == "geom":
            to_page(dict(self.app.geometry(), kind="geom"))
        elif kind == "select":
            # Tests (and the page) can pick a track by its position.
            tracks = self.app.project["tracks"]
            i = int(data.get("index", 0))
            if 0 <= i < len(tracks):
                self.app.select_track(tracks[i]["id"])


# -- song length ---------------------------------------------------------------------

def ticks_per_second(project):
    return project["tempo"] * model.PPQ / 60.0


def song_ticks(project):
    """One pass of the song, or four of the current pattern in pattern mode."""
    song = project.get("song") or []
    if project.get("song_mode") and song:
        total = 0
        for scene in song:
            bars = 1
            for t in project["tracks"]:
                i = scene["patterns"].get(str(t["id"]), -1)
                if 0 <= i < len(t["patterns"]):
                    bars = max(bars, t["patterns"][i]["bars"])
            total += bars * model.BAR * max(1, scene.get("repeat", 1))
        return total
    bars = max([t["patterns"][t.get("current", 0)]["bars"] for t in project["tracks"]
                if t.get("current", 0) < len(t["patterns"])] + [1])
    return bars * model.BAR * 4


# -- Standard MIDI File ---------------------------------------------------------------

def _vlq(n):
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.insert(0, 0x80 | (n & 0x7F))
        n >>= 7
    return bytes(out)


def _track(events):
    """events: [(tick, bytes)] -> an MTrk chunk."""
    events.sort(key=lambda e: (e[0], e[1][0] & 0xF0 != 0x80))
    body, last = b"", 0
    for tick, data in events:
        body += _vlq(int(tick) - last) + data
        last = int(tick)
    body += b"\x00\xff\x2f\x00"
    return b"MTrk" + len(body).to_bytes(4, "big") + body


def note_events(project, track):
    """Every note the track plays over one pass of the song, as (start, end, pitch, vel)."""
    out = []
    song = project.get("song") or []
    if project.get("song_mode") and song:
        at = 0
        for scene in song:
            bars = 1
            for t in project["tracks"]:
                i = scene["patterns"].get(str(t["id"]), -1)
                if 0 <= i < len(t["patterns"]):
                    bars = max(bars, t["patterns"][i]["bars"])
            length = bars * model.BAR * max(1, scene.get("repeat", 1))
            i = scene["patterns"].get(str(track["id"]), -1)
            if 0 <= i < len(track["patterns"]):
                pat = track["patterns"][i]
                plen = pat["bars"] * model.BAR
                for k in range(0, length, plen):
                    for s, l, p, v in pat["notes"]:
                        if k + s < length:
                            out.append((at + k + s, at + k + s + l, p, v))
            at += length
    else:
        idx = track.get("current", 0)
        total = song_ticks(project)
        if idx < len(track["patterns"]):
            pat = track["patterns"][idx]
            plen = pat["bars"] * model.BAR
            for k in range(0, total, plen):
                for s, l, p, v in pat["notes"]:
                    out.append((k + s, k + s + l, p, v))
    return out


def smf(project):
    """The project as a type-1 Standard MIDI File: a tempo track, then one
    track per app track (drums on channel 10)."""
    us = int(60_000_000 / project["tempo"])
    tempo = _track([(0, b"\xff\x51\x03" + us.to_bytes(3, "big")),
                    (0, b"\xff\x03" + _vlq(len(project.get("name", "Ostinato"))) + project.get("name", "Ostinato").encode())])
    chunks = [tempo]
    melodic = 0
    for t in project["tracks"]:
        if t["kind"] == "drums":
            ch = 9
        else:
            ch = melodic if melodic < 9 else melodic + 1
            melodic += 1
        name = t["name"].encode()
        ev = [(0, b"\xff\x03" + _vlq(len(name)) + name)]
        swing = project.get("swing", 0)
        for start, end, pitch, vel in note_events(project, t):
            shift = round(model.swung(start, swing)) - start    # as it plays
            ev.append((start + shift, bytes([0x90 | ch, pitch, max(1, vel)])))
            ev.append((end + shift, bytes([0x80 | ch, pitch, 0])))
        chunks.append(_track(ev))
    head = b"MThd" + (6).to_bytes(4, "big") + (1).to_bytes(2, "big") + len(chunks).to_bytes(2, "big") + \
        model.PPQ.to_bytes(2, "big")
    return head + b"".join(chunks)
