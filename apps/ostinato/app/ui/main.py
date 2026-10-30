"""Ostinato's UI: LVGL, the project, and the engines.

In a browser this is the UI worker: the page (index.html) runs nothing but
the hand-offs, this worker draws on an OffscreenCanvas and gets the canvas's
input relayed, and the engine workers make all the sound. On the desktop
(``python -m ostinato``) it runs on the main thread in a native window, with
the engines in processes or threads. ENGINES and BASE come in the
environment.
"""

import os

from boarddev import env_get, env_set

# Ostinato lays itself out in real pixels; the desktop board config would
# otherwise draw everything at 2x. An explicit PYDEVICES_SCALE still wins.
if env_get("PYDEVICES_SCALE") is None:
    env_set("PYDEVICES_SCALE", "1")

import board_config  # pydevices' board_configs/workerdisplay (desktop: board_configs/desktop)
import appdev

app_host = appdev.App.current() or appdev.App(board_config)
import display_driver  # noqa: F401  (LVGL on the worker's canvas)
import lvgl as lv

import catalog
import fonts
import layout
from songlink import model
import page
import theme
from engines import Engines
from history import History
from files import Files

ENGINES = int(os.environ.get("ENGINES", "1"))
DEBUG = bool(os.environ.get("OSTINATO_DEBUG"))
BASE = os.environ.get("BASE", "")

W = board_config.display_drv.width
H = board_config.display_drv.height
DMK25 = {
    "transport": {15: "loop", 16: "rewind", 17: "forward", 18: "stop", 19: "play", 20: "rec"},
    "knobs": {30: 0, 31: 1, 32: 2, 33: 3, 34: 4, 35: 5, 36: 6, 37: 7, 38: 8, 39: 9, 40: 10, 41: 11},
}


class Ostinato:
    def __init__(self):
        self.project = model.new_project()
        self.engines = Engines(ENGINES, self.project)
        self.engines.listeners.append(self.on_engine)
        self.history = History(self._op)
        self.message = ""
        self.message_until = 0.0
        self.selected = self.project["tracks"][2]["id"]   # Keys: every key sounds
        self.midi_seen = 0
        self.last_midi = ""
        self.rec_armed = False        # the record button: armed now, records on play
        self.saved_version = 0        # history.version at the last save or open
        self.shift = False            # the Shift key, as the page reports it
        self.clipboard = []           # copied notes, from their first step (across patterns and tracks)
        self.song_shown = False       # the song's scene list is in the editor's place
        self.pass_ms = []             # LVGL pass times, sampled each tick (for tests)
        self.underruns = 0
        self.screen = lv.screen_active()
        self.screen.set_style_bg_color(theme.c(theme.BG), 0)
        self.screen.remove_flag(lv.OBJ_FLAG.SCROLLABLE)

    def _op(self, op):
        self.engines.op(op)

    def edit(self, op, merge=False):
        """An undoable change to the project (see history.py)."""
        self.history.do(self.project, op, merge)

    def say(self, text, seconds=4.0):
        import time

        self.message = text
        self.message_until = time.monotonic() + seconds

    def load_project(self, project):
        """Replace the project (New, Open, a restored autosave)."""
        self.stop()
        self.engines.load(project)
        self.history.undo_steps.clear()
        self.history.redo_steps.clear()
        self.saved_version = self.history.version     # nothing to lose yet
        self.selected = self.project["tracks"][min(2, len(self.project["tracks"]) - 1)]["id"]
        self.engines.select(self.selected)
        self.tracks.build()
        self.tracks.show_selected(self.selected)
        self.inspector.show(self.selected)
        self.editor.show(self.selected)
        if self.song_view is not None:
            self.song_view.list = None        # a new song starts at the top
        self.show_song(bool(self.project.get("song_mode")))
        self.transport.refresh()

    def unsaved(self):
        return self.history.version != self.saved_version

    def build(self):
        from editor import Editor
        from inspector import Inspector
        from keyboard import Keyboard
        from status import StatusBar
        from tracks import TrackList
        from transport import TransportBar

        L = layout.pick(W, H)
        theme.setup(L["scale"])
        self.screen.clean()
        from tooltip import Tips
        self.tips = Tips(self.screen)
        self.transport = TransportBar(self.screen, L["top"], self)
        self.tracks = TrackList(self.screen, L["tracks"], self)
        self.editor = Editor(self.screen, L["editor"], self)
        self.editor_rect = L["editor"]
        self.song_view = None
        self.inspector = Inspector(self.screen, L["inspector"], self)
        self.keyboard = Keyboard(self.screen, L["keys"], self)
        self.status = StatusBar(self.screen, L["status"])
        self.tracks.show_selected(self.selected)
        self.inspector.show(self.selected)
        self.editor.show(self.selected)
        self.engines.load(self.project)
        self.engines.select(self.selected)
        self.files = Files(self)
        self.engines.on_change.append(self.files.changed)
        if hasattr(self.engines, "on_remote"):
            # The desktop UI as one panel of an hmilink device: another
            # panel's edit is already in the project; draw it.
            self.engines.on_remote.append(self.on_remote)
        from help import Help
        self.help = Help(self)
        lv.timer_create(lambda t: self.tick(), 33, None)

    # -- messages from the engines ---------------------------------------------

    def on_engine(self, msg):
        t = msg.get("t")
        if t == "midi":
            self.on_midi(msg["d"])
        elif t == "recorded":
            self.on_recorded(msg)
        elif t == "failed":
            self.say(msg.get("why", "An instrument couldn't be built"), seconds=8.0)
        elif t == "status" and msg.get("engine") == 0:
            pass

    def on_midi(self, d):
        status, d1, d2 = d
        kind, chan = status & 0xF0, status & 0x0F
        self.midi_seen += 1
        self.last_midi = describe_midi(status, d1, d2)
        if kind in (0x80, 0x90):
            self.keyboard.show(d1, kind == 0x90 and d2 > 0)
        elif kind == 0xB0:
            did = self.inspector.control(chan, d1, d2)
            if did:
                self.last_midi = did
                return
            if d1 in DMK25["transport"] and d2:
                action = DMK25["transport"][d1]
                if action == "play":
                    self.toggle_play()
                elif action == "stop":
                    self.stop()
                elif action == "rec":
                    self.toggle_record()
                elif action == "rewind":
                    self.to_top()
                elif action == "forward":
                    self.next_scene()
                elif action == "loop":
                    self.loop_playing_scene()
            elif d1 == 7 and 4 <= chan < 4 + len(self.project["tracks"]):
                # Slider bank B pans the tracks.
                t = self.project["tracks"][chan - 4]
                self.set_track(t["id"], "pan", d2)
                self.tracks.show_pan(t["id"], d2)
                self.last_midi = "%s: Pan %d" % (t["name"], d2)
            elif d1 == 7 and chan == 8:
                # Slider 9 (bank C's first) is the master volume.
                self.set_master("volume", d2)
                self.tracks.show_volume("master", d2)
                self.last_midi = "Master: Volume %d" % d2
            elif d1 == 7 and chan < len(self.project["tracks"]):
                tid = self.project["tracks"][chan]["id"]
                self.set_track(tid, "volume", d2)
                self.tracks.show_volume(tid, d2)
                self.last_midi = "%s: Volume %d" % (self.project["tracks"][chan]["name"], d2)

    def record_target(self, t, tick):
        """Where a note played at ``tick`` belongs: (pattern index, ticks into it).
        In song mode, the scene under the playhead's pattern for this track
        (None if the track is silent there); otherwise the track's own."""
        p = self.project
        song = p.get("song") or []
        if p.get("song_mode") and song:
            from song import scene_lengths

            lengths = scene_lengths(p)
            loop = p.get("loop_scene")
            if loop is not None and 0 <= loop < len(song):
                k, offset = loop, max(0.0, tick) % lengths[loop]
            else:
                rest = max(0.0, tick) % sum(lengths)
                k = 0
                while rest >= lengths[k]:
                    rest -= lengths[k]
                    k += 1
                offset = rest
            idx = song[k]["patterns"].get(str(t["id"]), -1)
            if idx < 0:
                self.say("%s is silent in scene %d: nothing recorded there" % (t["name"], k + 1))
                return None
            return idx, offset, k
        idx, base = model.pattern_at(t, tick)
        return idx, tick - base, None

    def begin_take(self):
        """A take starts (Play with record armed, or punching in): one undo step.
        With overdub off, the take replaces the armed track's pattern."""
        self.history.begin()
        self.take_cleared = set()
        if not self.project.get("overdub", True) and not self.project.get("song_mode"):
            t = model.find_track(self.project, self.selected)
            if t is not None:
                self.clear_for_take(t, model.shown_pattern(t))

    def clear_for_take(self, t, idx):
        """Empty pattern ``idx`` of track ``t`` once per take (overdub off)."""
        key = (t["id"], idx)
        if key in getattr(self, "take_cleared", set()):
            return
        self.take_cleared.add(key)
        notes = t["patterns"][idx]["notes"]
        for i in range(len(notes) - 1, -1, -1):
            self.edit({"op": "remove", "path": model.track_path(t["id"], "patterns", idx, "notes", i)})
        if t["id"] == self.editor.track_id:
            self.editor.build()

    def on_recorded(self, msg):
        t = model.find_track(self.project, msg["track"])
        if t is None:
            return
        if msg["start"] < -model.STEP / 2:
            return                                # played during the count-in
        where = self.record_target(t, msg["start"])
        if where is None:
            return
        idx, offset, scene = where
        if not self.project.get("overdub", True):
            self.clear_for_take(t, idx)           # song mode: each pattern the take reaches
        pat = t["patterns"][idx]
        length = pat["bars"] * model.BAR
        if self.project.get("quantise", True):
            start = int(round(offset / model.STEP)) * model.STEP % length
            dur = max(model.STEP, int(round((msg["end"] - msg["start"]) / model.STEP)) * model.STEP)
        else:
            start = int(round(max(0.0, offset))) % length
            dur = max(model.STEP // 4, int(round(msg["end"] - msg["start"])))
        note = [start, min(dur, length), msg["pitch"], msg["velocity"]]
        # A take is one undo step: recording opened it, stopping closes it.
        self.history.do(self.project, {"op": "insert",
                                       "path": model.track_path(t["id"], "patterns", idx, "notes", -1),
                                       "value": note})
        if t["id"] == self.editor.track_id and idx == self.editor.slot():
            self.editor.build()

    # -- actions --------------------------------------------------------------

    def toggle_play(self):
        if self.engines.playing:
            self.stop()
        else:
            tick = 0.0
            if self.rec_armed:
                self.begin_take()
                tick = -float(model.BAR)          # a bar of count-in, with the click
            self.engines.play(record=self.rec_armed, tick=tick)
        self.transport.refresh()

    def stop(self):
        self.history.end()
        self.engines.stop()
        # Pending switches land now, and every pattern starts from its top next time.
        for t in self.project["tracks"]:
            if t.get("next") or t.get("current_at"):
                cur = t["next"]["pattern"] if t.get("next") else t.get("current", 0)
                self.set_pattern_now(t, cur)
        self.tracks.show_patterns()
        self.transport.refresh()
        self.transport.show_position(None)

    def toggle_record(self):
        self.rec_armed = not self.rec_armed
        self.tracks.show_armed(self.selected if self.rec_armed else None)
        self.engines.select(self.selected, armed=self.selected if self.rec_armed else None)
        if self.engines.playing:
            # Punch in or out while playing.
            if self.rec_armed:
                self.begin_take()
            else:
                self.history.end()
            self.engines.broadcast({"t": "record", "on": self.rec_armed})
            self.engines.recording = self.rec_armed
        self.transport.refresh()

    def toggle_overdub(self):
        on = not self.project.get("overdub", True)
        self.engines.op({"op": "set", "path": ["overdub"], "value": on})
        self.transport.refresh()
        self.say("Takes add to the pattern" if on else "A take replaces the pattern", 2.0)

    def toggle_quantise(self):
        on = not self.project.get("quantise", True)
        self.engines.op({"op": "set", "path": ["quantise"], "value": on})
        self.transport.refresh()
        self.say("Recording snaps to sixteenths" if on else "Recording keeps your timing", 2.0)

    def toggle_metronome(self):
        self.engines.op({"op": "set", "path": ["metronome"], "value": not self.project.get("metronome")})
        self.transport.refresh()

    def nudge_tempo(self, delta):
        self.set_tempo(round(self.project["tempo"]) + delta)

    def tap_tempo(self):
        """A tap on the tempo: from the second tap on, the tempo follows the
        average of the last few taps. A pause of two seconds starts over."""
        import time

        now = time.monotonic()
        taps = [t for t in getattr(self, "taps", []) if now - t < 2.0 * 4]
        if taps and now - taps[-1] > 2.0:
            taps = []
        taps = (taps + [now])[-5:]
        self.taps = taps
        if len(taps) >= 2:
            beat = (taps[-1] - taps[0]) / (len(taps) - 1)
            self.set_tempo(60.0 / beat)
            self.transport.refresh()
            self.say("Tap tempo: %d BPM" % round(self.project["tempo"]), 2.0)
        else:
            self.say("Tap again in time to set the tempo", 2.0)

    def set_tempo(self, bpm):
        tempo = max(40, min(240, round(bpm)))
        self.edit({"op": "set", "path": ["tempo"], "value": float(tempo)}, merge=True)
        self.transport.refresh()

    def nudge_swing(self, delta):
        # One step is 1% on the display, 50% (straight) to 75%.
        from transport import swing_percent
        shown = max(50, min(75, swing_percent(self.project.get("swing", 0)) + delta))
        swing = 2 * (shown - 50)
        self.edit({"op": "set", "path": ["swing"], "value": swing}, merge=True)
        self.transport.refresh()

    def toggle_song(self):
        """Song mode: the editor's space shows the scenes, and play walks them.
        In song mode with a pattern open, the Song button goes back to the list."""
        if self.project.get("song_mode") and not self.song_shown:
            self.show_song(True)
            self.transport.refresh()
            return
        on = not self.project.get("song_mode")
        self.history.do(self.project, {"op": "set", "path": ["song_mode"], "value": on})
        if on and not self.project.get("song"):
            idx = self.project.get("loop_pattern", 0)
            self.history.do(self.project, {"op": "insert", "path": ["song", -1], "value": {
                "patterns": {str(t["id"]): idx for t in self.project["tracks"]}, "repeat": 1}})
        self.show_song(on)
        self.transport.refresh()

    def show_song(self, on):
        from song import SongView

        self.song_shown = on
        if on:
            self.editor.root.add_flag(lv.OBJ_FLAG.HIDDEN)
            if self.song_view is None:
                self.song_view = SongView(self.screen, self.editor_rect, self)
            else:
                self.song_view.root.remove_flag(lv.OBJ_FLAG.HIDDEN)
                self.song_view.build()
        else:
            self.editor.root.remove_flag(lv.OBJ_FLAG.HIDDEN)
            if self.song_view is not None:
                self.song_view.root.add_flag(lv.OBJ_FLAG.HIDDEN)

    def playing_scene(self):
        """The scene under the playhead, or None (stopped, or not in song mode)."""
        if not (self.engines.playing and self.project.get("song_mode") and self.project.get("song")):
            return None
        from song import scene_lengths

        loop = self.project.get("loop_scene")
        if loop is not None:
            return loop
        lengths = scene_lengths(self.project)
        rest = max(0.0, self.engines.now_tick() or 0.0) % sum(lengths)
        k = 0
        while rest >= lengths[k]:
            rest -= lengths[k]
            k += 1
        return k

    def to_top(self):
        """The keyboard's Rewind: back to the start (playing on, if it was)."""
        playing = self.engines.playing
        self.stop()
        if playing:
            self.toggle_play()

    def next_scene(self):
        """The keyboard's Forward: the next scene, in song mode."""
        k = self.playing_scene()
        if k is None:
            self.say("Forward jumps to the next scene while a song plays", 2.0)
            return
        self.play_scene((k + 1) % len(self.project["song"]))

    def loop_playing_scene(self):
        """The keyboard's Loop: loop the scene that's playing (again: stop looping)."""
        k = self.playing_scene()
        if k is None:
            self.say("Loop repeats the scene that's playing, in song mode", 2.0)
            return
        self.loop_scene(k)

    def scene_start(self, index):
        from song import scene_lengths

        return sum(scene_lengths(self.project)[:index])

    def play_scene(self, index):
        """Play the song from scene ``index`` (from the start of the loop, if looping)."""
        if self.engines.playing:
            self.stop()
        loop = self.project.get("loop_scene")
        tick = 0 if loop is not None else self.scene_start(index)
        if self.rec_armed:
            self.begin_take()
        self.engines.play(record=self.rec_armed, tick=float(tick))
        self.transport.refresh()

    def loop_scene(self, index):
        """Loop scene ``index``, or stop looping it; either way play from there."""
        loop = None if self.project.get("loop_scene") == index else index
        self.engines.op({"op": "set", "path": ["loop_scene"], "value": loop})
        if self.engines.playing or loop is not None:
            self.play_scene(index)
        if self.song_view is not None:
            self.song_view.build()

    def edit_scene_pattern(self, index, track_id):
        """Open scene ``index``'s pattern for this track in the editor; the song
        keeps playing, and the Song button (or Back to song) returns."""
        i = self.project["song"][index]["patterns"].get(str(track_id), -1)
        if i < 0:
            self.say("That track is silent in scene %d: pick a pattern for it first" % (index + 1))
            return
        self.set_pattern_now(model.find_track(self.project, track_id), i)
        self.show_song(False)
        self.select_track(track_id)
        self.transport.refresh()
        self.say("Editing pattern %s of %s; Song goes back to the scenes" % (
            "ABCDEFGH"[i], model.find_track(self.project, track_id)["name"]))

    def key(self, name):
        """Keyboard shortcuts, sent by the page."""
        if name == "space":
            self.toggle_play()
        elif name == "r":
            self.transport.rec.checked = not self.rec_armed
            self.toggle_record()
        elif name == "undo":
            self.undo()
        elif name == "redo":
            self.redo()
        elif name == "escape":
            self.help.close()
        elif name in ("save", "save-as"):
            self.files.save(quick=name == "save")
        elif name == "delete":
            self.editor.delete_selected()
        elif name == "copy":
            self.editor.copy()
        elif name == "paste":
            self.editor.paste()
        elif name == "duplicate":
            self.editor.duplicate_selection()
        elif name == "select-all":
            self.editor.select_all()
        elif name in ("shift-down", "shift-up"):
            self.shift = name == "shift-down"

    def undo(self):
        if self.history.undo():
            self.after_history()

    def redo(self):
        if self.history.redo():
            self.after_history()

    def after_history(self):
        """Undo and redo can change anything: draw it all again."""
        self.editor.selection = set()             # its notes may not be where they were
        self.engines.send_pads()
        self.tracks.build()
        self.tracks.show_selected("master" if self.inspector.master else self.selected)
        self.transport.refresh()
        self.inspector.build()
        self.editor.build()
        if self.song_view is not None and self.project.get("song_mode"):
            self.song_view.build()

    def on_remote(self, changes):
        """Another panel (the device's browser page) changed the song or started
        or stopped it: the project already holds the change, so draw it."""
        if any(c.get("doc") for c in changes):
            if model.find_track(self.project, self.selected) is None:
                self.selected = self.project["tracks"][min(2, len(self.project["tracks"]) - 1)]["id"]
                self.engines.select(self.selected)
            self.show_song(bool(self.project.get("song_mode")))
        if any("path" in c or c.get("doc") for c in changes):
            self.after_history()
        if any("playing" in c for c in changes):
            self.transport.refresh()

    def duplicate_pattern(self):
        """Copy the selected track's pattern into its next empty slot and switch
        this track there: "the next one is this with a change". (Song's
        Duplicate copies a whole scene, every track.)"""
        import json

        t = model.find_track(self.project, self.selected)
        here = model.shown_pattern(t)

        def empty(i):
            return i >= len(t["patterns"]) or not t["patterns"][i]["notes"]

        target = next((i for i in range(here + 1, 8) if empty(i)), None)
        if target is None:
            target = next((i for i in range(8) if i != here and empty(i)), None)
        if target is None:
            self.say("All eight of %s's patterns have notes: clear one to copy into it" % t["name"])
            return
        self.history.begin()
        while len(t["patterns"]) <= target:
            self.edit({"op": "insert", "path": model.track_path(t["id"], "patterns", -1), "value": model.pattern()})
        self.edit({"op": "set", "path": model.track_path(t["id"], "patterns", target),
                   "value": json.loads(json.dumps(t["patterns"][here]))})
        self.history.end()
        self.choose_pattern(target, tracks=[t])
        self.say("Copied %s's pattern %s to %s" % (t["name"], "ABCDEFGH"[here], "ABCDEFGH"[target]))

    def choose_pattern(self, index, tracks=None):
        """Play pattern ``index`` on the selected track (with Shift held, every
        track). While playing it waits for the next bar line; stopped, it's
        immediate. Choosing a pattern isn't an edit: undo leaves it alone."""
        if tracks is None:
            tracks = self.project["tracks"] if self.shift else [model.find_track(self.project, self.selected)]
        for t in tracks:
            while len(t["patterns"]) <= index:
                self.engines.op({"op": "insert", "path": model.track_path(t["id"], "patterns", -1),
                                 "value": model.pattern()})
        if self.engines.playing and not self.project.get("song_mode"):
            at = self.next_bar()
            for t in tracks:
                if model.shown_pattern(t) != index or t.get("next"):
                    self.engines.op({"op": "set", "path": model.track_path(t["id"], "next"),
                                     "value": {"pattern": index, "at": at}})
        else:
            for t in tracks:
                self.set_pattern_now(t, index)
        self.transport.refresh()
        self.tracks.show_patterns()
        self.editor.build()

    def set_pattern_now(self, t, index):
        for key, value in (("current", index), ("current_at", 0), ("next", None)):
            if t.get(key) != value:
                self.engines.op({"op": "set", "path": model.track_path(t["id"], key), "value": value})

    def next_bar(self):
        """The next bar line far enough ahead that every engine can still play it."""
        now = self.engines.now_tick() or 0.0
        at = (int(now // model.BAR) + 1) * model.BAR
        if at - now < 0.1 * self.project["tempo"] * model.PPQ / 60:     # under 100 ms away
            at += model.BAR
        return at

    def land_switches(self):
        """Pending switches whose bar line has passed become the track's current
        pattern (the engines already play them from there)."""
        now = self.engines.now_tick()
        if now is None:
            return
        landed = False
        for t in self.project["tracks"]:
            n = t.get("next")
            if n and now >= n["at"]:
                for key, value in (("current", n["pattern"]), ("current_at", n["at"]), ("next", None)):
                    self.engines.op({"op": "set", "path": model.track_path(t["id"], key), "value": value})
                landed = True
        if landed:
            self.transport.refresh()
            self.tracks.show_patterns()

    def select_master(self):
        """The master in the inspector; the keyboard and editor keep their track."""
        self.tracks.show_selected("master")
        self.inspector.show_master()

    def select_track(self, track_id):
        self.selected = track_id
        self.tracks.show_armed(track_id if self.rec_armed else None)
        self.engines.select(track_id, armed=track_id if self.rec_armed else None)
        self.tracks.show_selected(track_id)
        self.inspector.show(track_id)
        self.editor.show(track_id)
        self.transport.refresh()                 # A-H show this track's patterns

    def track_changed(self, track_id):
        """A track's instrument or patch changed: redraw its row and inspector."""
        self.engines.send_pads()
        self.tracks.build()
        self.tracks.show_selected(self.selected)
        if track_id == self.selected:
            self.inspector.show(track_id)
            self.editor.show(track_id)

    def set_mix_state(self, track_id, key, value):
        """Mute and solo: playing the mix, not editing it, so undo leaves them alone."""
        self.engines.op({"op": "set", "path": model.track_path(track_id, key), "value": value})

    def set_track(self, track_id, key, value):
        self.edit({"op": "set", "path": model.track_path(track_id, key), "value": value}, merge=True)

    def set_master(self, key, value):
        self.edit({"op": "set", "path": ["master", key], "value": value}, merge=True)

    def audition(self, pitch, velocity, seconds=None):
        """Play a note on the selected track; with ``seconds``, let it go after that."""
        tid = self.selected
        # With a length, the engine lets the note go itself, at the sample: a
        # UI timer can be collected before it fires, leaving a note stuck on.
        self.engines.audition(tid, pitch, velocity, seconds)
        self.keyboard.show(pitch, velocity > 0 and not seconds)

    def geometry(self):
        """Where things are on the canvas, in canvas pixels: for tests that
        click the real controls."""
        def rect(obj):
            a = lv.area_t()
            obj.get_coords(a)
            return [a.x1, a.y1, a.x2 - a.x1 + 1, a.y2 - a.y1 + 1]

        tb = self.transport
        g = {"file": rect(tb.file.obj), "play": rect(tb.play.obj), "stop": rect(tb.stop.obj), "rec": rect(tb.rec.obj),
             "undo": rect(tb.undo.obj), "redo": rect(tb.redo.obj),
             "metronome": rect(tb.metronome.obj), "song": rect(tb.song.obj), "swing": rect(tb.swing.get_parent()),
             "slots": [rect(b.obj) for b in tb.slots], "tracks": [rect(r["row"]) for r in self.tracks.rows]}
        import time

        g["message"] = self.message if time.monotonic() < self.message_until else ""   # the status line's text
        buttons = getattr(self, "files", None) and getattr(self.files, "confirm_buttons", None)
        if buttons:
            g["confirm"] = {k: rect(v) for k, v in buttons.items()}
        menu = getattr(self, "files", None) and self.files.menu
        if menu:
            g["menu"] = [rect(menu.get_child(i)) for i in range(menu.get_child_count())]
        v = self.editor.view
        song = getattr(self, "song_view", None)
        if song is not None and song.list is not None:
            g["scenes"] = {"list": rect(song.list), "rows": [rect(r) for r in song.rows],
                           "nums": [rect(q["num"]) for q in song.parts], "loops": [rect(q["loop"]) for q in song.parts],
                           "dups": [rect(q["dup"]) for q in song.parts],
                           "moves": [[rect(b) for b in q["moves"]] for q in song.parts],
                           "names": [rect(q["name"]) for q in song.parts],
                           "letters": [[rect(b) for b in q["letters"]] for q in song.parts], "playing": song.playing}
        g["song_shown"] = self.song_shown
        g["knobs"], g["fx_knobs"] = len(self.inspector.knobs), len(self.inspector.fx_knobs)
        g["quant"] = rect(tb.quant.obj)
        g["tempo"] = rect(tb.tempo.get_parent())
        g["position"] = tb.position.get_text()
        g["tick"] = self.engines.now_tick() if self.engines.playing else None
        g["armed"] = getattr(self.tracks, "armed", None)
        g["last_midi"] = self.last_midi
        g["tip"] = self.tips.text
        g["slot_dots"] = [not d.has_flag(lv.OBJ_FLAG.HIDDEN) for d in tb.dots]
        g["mutes"] = [rect(r["mute"].obj) for r in self.tracks.rows if r.get("mute") is not None]
        g["overdub"] = rect(tb.overdub.obj)
        g["learn"] = rect(self.inspector.learn.obj) if getattr(self.inspector, "learn", None) else None
        g["status_right"] = self.status.right.get_text()
        g["selection"] = sorted(self.editor.selection)
        g["canvas"] = [W, H]
        g["note_info"] = self.editor.note_info.get_text() if getattr(self.editor, "note_info", None) else None
        g["knob_rects"] = [rect(k.root) for k in self.inspector.knobs]
        g["fx_moves"] = [rect(b) for b in getattr(self.inspector, "move_buttons", [])]
        g["inst_steps"] = [rect(b) for b in getattr(self.inspector, "step_buttons", [])]
        g["keys_low"] = self.keyboard.low
        g["keys_shift"] = [rect(b) for b in getattr(self.keyboard, "shift_buttons", [])]
        g["status_left"] = self.status.left.get_text()
        pk = getattr(self.song_view, "picker_buttons", None) if self.song_view else None
        if pk is not None:
            g["picker"] = [rect(pk.get_child(i)) for i in range(pk.get_child_count())]
        g["hovered"] = {"metronome": bool(tb.metronome.obj.has_state(lv.STATE.HOVERED))}
        if getattr(self.editor, "copy_button", None) is not None and self.editor.root.has_flag(lv.OBJ_FLAG.HIDDEN) is False:
            g["copy"] = rect(self.editor.copy_button)
            g["bars"] = {str(k): rect(v) for k, v in self.editor.bar_buttons.items()}
        if v is not None and hasattr(v, "grid"):
            g["grid"] = {"rect": rect(v.grid), "rows": [p[0] for p in v.pads], "steps": v.steps}
        if v is not None and hasattr(v, "hit"):
            g["roll"] = {"rect": rect(v.hit), "low": self.editor.low, "rows": v.ROWS, "steps": v.steps}
        return g

    # -- every frame ------------------------------------------------------------

    def tick(self):
        self.engines.flush()
        loop = display_driver.event_loop.current_instance()
        if loop is not None:
            self.pass_ms.append(loop.last_pass_ms)
            if len(self.pass_ms) > 3000:
                del self.pass_ms[:1500]
        self.engines.poll()
        self.tracks.show_meters(self.engines.meters())
        if self.engines.playing:
            self.land_switches()
        self.transport.blink_pending()
        self.transport.pulse(self.engines.playing and self.rec_armed)
        if self.engines.playing:
            tick = self.engines.now_tick()
            self.transport.show_position(tick)
            if self.project.get("song_mode") and self.song_shown and self.song_view is not None:
                self.song_view.playhead(tick)
            elif self.project.get("song_mode"):
                self.editor.playhead(None)        # the song decides what plays, not this pattern
            else:
                # Where the selected track is in its own pattern (it may have
                # switched at a later bar line than the song's start).
                t = model.find_track(self.project, self.selected)
                idx, base = model.pattern_at(t, tick) if t else (0, 0)
                self.editor.playhead(tick - base if idx == self.editor.slot() else None)
        st = [s for s in self.engines.status if s]
        under = sum(s.get("underruns", 0) for s in st) + self.engines.master_underruns()
        import time

        now = time.monotonic()
        if under > getattr(self, "last_under", 0):
            self.dropped_until = now + 5.0          # say so for a moment, then let it go
        self.last_under = under
        right, message = "MIDI  %s" % (self.last_midi if self.midi_seen else "waiting for a keyboard"), False
        if self.message and now < self.message_until:
            right, message = self.message, True
        self.status.show(self.project.get("name", "Untitled"), self.unsaved(),
                         now < getattr(self, "dropped_until", 0.0), right, message,
                         self.engines.loads() if DEBUG else None)
        self.transport.show_history(bool(self.history.undo_steps), bool(self.history.redo_steps))


NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def describe_midi(status, d1, d2):
    """One MIDI message the way a player reads it (channels counted from 1)."""
    kind, ch = status & 0xF0, (status & 0x0F) + 1
    if kind == 0x90 and d2:
        return "ch %d  note on  %s%d  vel %d" % (ch, NOTE_NAMES[d1 % 12], d1 // 12 - 1, d2)
    if kind in (0x80, 0x90):
        return "ch %d  note off  %s%d" % (ch, NOTE_NAMES[d1 % 12], d1 // 12 - 1)
    if kind == 0xB0:
        return "ch %d  CC %d = %d" % (ch, d1, d2)
    if kind == 0xE0:
        return "ch %d  pitch bend %d" % (ch, (d1 | (d2 << 7)) - 8192)
    return "status %02X" % status


async def start():
    splash = lv.label(lv.screen_active())
    splash.set_text("Ostinato")
    splash.center()
    failed = await fonts.load(BASE)
    await catalog.load(BASE)
    if failed:
        print("fonts not loaded:", failed)
    global ostinato
    ostinato = Ostinato()
    ostinato.build()
    print("ui ready %dx%d, %d engines" % (W, H, ENGINES))


page.run(start())       # a browser worker's event loop runs it; the desktop builds it now
app_host.run()          # the desktop's loop, until the window closes; returns at once in a worker
