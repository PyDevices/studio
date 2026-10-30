"""An engine's life on the desktop, the same in a process and in a thread.

The runner hands it ``io`` (how messages arrive and leave) and a ``Ring``
(where its audio goes). Messages in:

    dict                         from the UI: ops, transport, select... (EngineHost.handle),
                                 and {"t": "render"} for a WAV export
    ("m", status, d1, d2, ns)    a MIDI message, stamped with perf_counter_ns
    ("n", blocks, asked)         the mixer wants ``blocks`` more blocks
    ("q",)                       stop

Live MIDI plays at the start of the next block it renders, as in a browser
worker: before each block it takes whatever MIDI has arrived since.
"""

import time

from enginehost import EngineHost
from ring import ASKED, CFRAME, CNS, PAUSE

SR = 48000


def run(index, io, ring, plant=None):
    pending = []                  # messages that arrived mid-render, for after it
    held = []                     # requests before the first tracks are built
    ready = [False]

    def pause(on):
        g = ring.h[PAUSE]
        if (g & 1) != bool(on):
            ring.h[PAUSE] = g + 1

    def go_live():
        ready[0] = True
        host.engine.streaming = True

    def post(msg):
        msg.setdefault("engine", index)
        io.post(msg)

    host = EngineHost(SR, index, post, pause, on_assign=go_live)
    engine = host.engine

    ons = [0]

    def live(m):
        _, status, d1, d2, ns = m
        engine.asked = ring.h[ASKED]
        if (status & 0xF0) == 0x90 and d2:
            ons[0] += 1
            if plant == "delay":                 # hold every note-on back six blocks (32 ms)
                delayed.append((engine.frame + 6 * 256, m))
                return
            if plant == "lose" and ons[0] % 10 == 0:     # drop every tenth note-on
                return
            if plant == "detune":                # every note a semitone sharp
                d1 += 1
        elif plant == "detune" and (status & 0xF0) == 0x80:
            d1 += 1
        host.live(status, d1, d2, ring.key_frame(ns, SR))

    delayed = []

    def midi_drain():
        for m in io.poll():
            if isinstance(m, tuple) and m[0] == "m":
                live(m)
            else:
                pending.append(m)
        while delayed and delayed[0][0] <= engine.frame:
            _, status, d1, d2, ns = delayed.pop(0)[1]
            host.live(status, d1, d2, ring.key_frame(ns, SR))

    def status_extra():
        # "clock": the mixer's last (context frame, perf_counter_ns) pair, so
        # the UI can tell the frame playing now however late this arrives.
        return {"underruns": ring.h[7], "time": time.perf_counter() * 1000.0,
                "clock": [ring.h[CFRAME], ring.h[CNS]]}

    def render(blocks, asked):
        engine.asked = asked
        pcm = host.render(blocks, midi_drain, ring.offset())
        ring.write(pcm)
        io.done()

    def export(m):
        """A WAV export: the whole song from the top, as fast as it goes, in
        chunks to the UI (the page sums them). The stream pauses meanwhile."""
        def send(seq, pcm):
            post({"t": "pcm", "seq": seq, "pcm": pcm})

        n = engine.render_offline(int(m["frames"]), int(m.get("chunk", 96000)), send)
        post({"t": "rendered", "chunks": n})

    def handle(m):
        if isinstance(m, dict):
            if m.get("t") == "render":
                export(m)
                return True
            host.handle(m, status_extra)
            if ready[0] and held:
                for b, a in held:
                    render(b, a)
                held.clear()
            return True
        kind = m[0]
        if kind == "m":
            live(m)
        elif kind == "n":
            if not ready[0]:
                held.append((m[1], m[2]))
            else:
                render(m[1], m[2])
        elif kind == "q":
            return False
        return True

    post({"t": "hello", "sample_rate": SR})
    while True:
        msgs = pending + io.get(0.1)
        pending.clear()
        for m in msgs:
            if not handle(m):
                return
