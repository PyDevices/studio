"""Entry point of the master worker: the master rack, on the sum of every track.

The track engines don't talk to the AudioWorklet. Each one talks to this
worker as if it were the worklet: it asks them for audio, gets their blocks,
tells them where their audio is heard (``offset``) and when they ran short
(``underrun``). Towards the worklet it is one stream: the tracks summed,
through the master rack and volume.

It adds no queue of its own. A request from the worklet goes straight on to
the engines, and the answer goes back as soon as every engine that is
playing has delivered its block, so the worklet's queue covers both hops.
What it does add, while the master has effects, is one block (256 frames,
5 ms) in front of the rack: an effect pulls 512 frames at a time, and the
feed must hold them. The delay stays while any effect is in the list, even
bypassed, so switching one off and on doesn't skip or repeat 5 ms of audio.
With an empty list the sum goes straight out.

The sum is taken 6 dB down, so four loud tracks don't clip on the way into
the rack, and the worklet's master gain puts the 6 dB back before its
limiter.

The sound itself is ``mastercore.MasterMix``, which the desktop mixer hosts
too; this file is the worklet's protocol around it.

Ports from the page: ``audio`` to the worklet, ``track0`` .. ``trackN`` to
the engines (in place of their worklet streams), ``ui`` to the UI worker.
"""

import os

from js import Int16Array, Object, ports, self as worker
from pyodide.ffi import create_proxy, to_js

from engine import BLOCK, BLOCK_BYTES
from mastercore import MasterMix

SR = 48000
ENGINES = int(os.environ.get("ENGINES", "4"))
LATE_MS = 40.0            # a block waits this long for a slow engine, then plays without it


def post_ui(msg):
    ports.ui.postMessage(to_js(msg, dict_converter=Object.fromEntries))


class Lane:
    """One engine, as the worklet would see it."""

    def __init__(self, index, port):
        self.index = index
        self.port = port
        self.queue = bytearray()
        self.requested = 0        # frames asked for and not yet delivered
        self.played = 0           # frames of this engine mixed so far
        self.started = False
        self.paused = False
        self.offset = None
        port.onmessage = create_proxy(self.on_message)

    def on_message(self, event):
        d = event.data
        pause = getattr(d, "pause", None)
        if pause is not None:
            self.paused = bool(pause)
            if not self.paused:
                self.queue = bytearray()
                self.requested = 0
                self.started = False
            return
        pcm = getattr(d, "pcm", None)
        if pcm is None:
            return
        data = pcm.to_bytes()
        self.queue += data
        self.requested = max(0, self.requested - len(data) // 4)
        master.fill()

    def top_up(self, frames, asked):
        """Ask for what the master still owes the worklet, less what this engine
        already holds or owes, as the worklet asks its streams. An engine that
        holds its requests (building an instrument) then can't pile up a
        backlog that would make its track play late from then on."""
        if self.paused:
            return
        short = frames - len(self.queue) // 4 - self.requested
        if short > 0:
            blocks = (short + BLOCK - 1) // BLOCK
            self.requested += blocks * BLOCK
            self.port.postMessage(to_js({"need": blocks, "asked": asked}, dict_converter=Object.fromEntries))

    def ready(self):
        return self.paused or len(self.queue) >= BLOCK_BYTES or not self.started

    def take(self, master_frame, out_offset):
        """This engine's next block (silence if it has none), and its offset news."""
        if self.paused:
            return None
        if len(self.queue) >= BLOCK_BYTES:
            block = bytes(self.queue[:BLOCK_BYTES])
            del self.queue[:BLOCK_BYTES]
            self.played += BLOCK
            self.started = True
        else:
            block = None
            if self.started:
                self.port.postMessage(to_js({"underrun": 1}, dict_converter=Object.fromEntries))
        if self.started and out_offset is not None:
            # This engine's frame k went into master frame k + (master_frame + BLOCK - played),
            # which is heard master.delay frames later than the master's own offset says.
            offset = out_offset + master.mix.delay + master_frame + BLOCK - self.played
            if offset != self.offset:
                self.offset = offset
                self.port.postMessage(to_js({"offset": offset}, dict_converter=Object.fromEntries))
        return block


class Master:
    def __init__(self):
        self.lanes = [Lane(i, getattr(ports, "track%d" % i)) for i in range(ENGINES)]
        self.mix = MasterMix(SR, performance_now)
        self.pending = 0            # blocks the worklet asked for, not yet sent
        self.pending_since = None
        self.asked = None
        self.offset = None          # master frame k is heard at context frame k + offset
        self.underruns = 0
        self.late = 0

    # -- the worklet's side ------------------------------------------------------

    def on_audio(self, event):
        d = event.data
        need = getattr(d, "need", None)
        if need is not None:
            self.asked = getattr(d, "asked", None)
            n = int(need)
            if not self.pending:
                self.pending_since = performance_now()
            self.pending += n
            for lane in self.lanes:
                lane.top_up(self.pending * BLOCK, self.asked)
            self.fill()
            return
        offset = getattr(d, "offset", None)
        if offset is not None:
            self.offset = int(offset)
        underrun = getattr(d, "underrun", None)
        if underrun is not None:
            self.underruns += int(underrun)

    def fill(self):
        """Send every asked-for block whose engine blocks have all arrived."""
        sent = bytearray()
        while self.pending:
            if not all(lane.ready() for lane in self.lanes):
                if performance_now() - self.pending_since < LATE_MS:
                    break
                self.late += 1            # an engine is stuck: play on without it
            sent += self.mix_block()
            self.pending -= 1
            self.pending_since = performance_now()
        if sent:
            arr = Int16Array.new(len(sent) // 2)
            arr.assign(memoryview(sent).cast("h"))
            msg = to_js({"pcm": arr, "asked": self.asked}, dict_converter=Object.fromEntries)
            ports.audio.postMessage(msg, to_js([arr.buffer]))

    def mix_block(self):
        frame = self.mix.frame
        return self.mix.mix([lane.take(frame, self.offset) for lane in self.lanes])

    # -- the project: only its master matters here --------------------------------

    def apply(self, op):
        self.mix.apply(op)

    def apply_many(self, ops):
        self.mix.apply_many(ops)

    # -- an export: the page sends the summed tracks; send them back through the rack --

    def render_offline(self, pcm, frames):
        """``pcm`` is the tracks' sum, already 6 dB down as int16 (the page does
        that in one pass)."""
        # This blocks the worker for the whole song: play silence meanwhile
        # instead of counting dropouts (nothing plays during an export).
        pause(True)
        try:
            return self.mix.render_offline(pcm, frames)
        finally:
            pause(False)


def pause(on):
    ports.audio.postMessage(to_js({"pause": bool(on)}, dict_converter=Object.fromEntries))
    if not on:
        master.pending = 0      # the worklet starts its stream afresh and asks again


def performance_now():
    from js import performance

    return float(performance.now())


master = Master()
ports.audio.onmessage = create_proxy(master.on_audio)


def on_ui(event):
    msg = event.data.to_py()
    t = msg.get("t")
    if t == "op":
        master.apply(msg["op"])
    elif t == "ops":
        master.apply_many(msg["ops"])
    elif t == "status":
        # load: the share of real time this worker spends making its blocks
        msg = {"t": "master", "underruns": master.underruns, "late": master.late, "offset": master.offset}
        msg.update(master.mix.report())
        post_ui(msg)


ports.ui.onmessage = create_proxy(on_ui)


def on_page(msg):
    data = {"kind": msg.kind, "frames": getattr(msg, "frames", 0)}
    if data.get("kind") == "master-render":
        out = master.render_offline(msg.pcm.to_bytes(), int(data["frames"]))
        arr = Int16Array.new(len(out) // 2)
        arr.assign(memoryview(out).cast("h"))
        worker.postMessage(to_js({"type": "app", "kind": "master-rendered", "pcm": arr},
                                 dict_converter=Object.fromEntries), to_js([arr.buffer]))


worker.onPageMessage = create_proxy(on_page)
print("master ready")
