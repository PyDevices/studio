"""The master track's sound, whatever hosts it: the tracks' blocks summed,
through the master rack and volume.

``MasterMix.mix(blocks)`` takes one 256-frame block per engine (None for an
engine with nothing to give) and returns the master's next block. The sum is
taken 6 dB down, so four loud tracks don't clip on the way into the rack; the
output stage (the AudioWorklet, or the desktop mixer) puts the 6 dB back
before its limiter.

While the master has effects, the feed runs one block (256 frames, 5 ms)
ahead of the rack: an effect pulls 512 frames at a time, and the feed must
hold them. The delay stays while any effect is in the list, even bypassed,
so switching one off and on doesn't skip or repeat 5 ms of audio. With an
empty list the sum goes straight out.

It imports nothing from a browser: the master worker (master.py) and the
desktop mixer both host it.
"""

import time

import _audiodsp
import audiocore
import audioeffects

from songlink import model
from engine import BLOCK, BLOCK_BYTES, Puller, StereoMeter, gain_buffer

HEADROOM = 0.5            # the sum enters the rack 6 dB down
DELAY = BLOCK             # frames the feed runs ahead of the rack


def now_ms():
    return time.perf_counter() * 1000.0


class Feed(audiocore._AudioSample):
    """The rack's source: whatever has been pushed, oldest first, never done."""

    def __init__(self, sample_rate):
        self.sample_rate = sample_rate
        self.channel_count = 2
        self.bits_per_sample = 16
        self.samples_signed = True
        self._deinited = False
        self.data = bytearray(DELAY * 4)       # starts one block ahead
        self.short = 0

    def push(self, block):
        self.data += block

    def _reset_buffer(self, single_channel_output=False, audio_channel=0):
        pass

    def _get_buffer(self, single_channel_output=False, audio_channel=0):
        want = 512 * 4
        if len(self.data) < want:
            self.short += 1
            self.data += bytes(want - len(self.data))
        out, self.data = bytes(self.data[:want]), self.data[want:]
        return audiocore.GET_BUFFER_MORE_DATA, self._publish(out, 2)


def build_rack(spec, source, sample_rate):
    chain = tuple((e["name"], {}) for e in spec)
    return audioeffects.create("Rack", source, sample_rate, chain=chain)


def set_macros(rack, spec):
    if rack is None:
        return
    for i, e in enumerate(spec):
        child = rack.effects[i] if i < len(rack.effects) else None
        if child is not None:
            for k, v in e.get("macros", {}).items():
                child.set_macro(int(k), v)


class MasterMix:
    def __init__(self, sample_rate, clock=now_ms):
        self.sample_rate = sample_rate
        self.clock = clock
        self.project = None
        self.feed = Feed(sample_rate)
        self.rack = None
        self.rack_spec = None
        self.puller = None
        self.delay = 0              # frames the rack's feed adds; 0 with no rack
        self.level = 1.0
        self.frame = 0              # master frames mixed
        self.render_ms = 0.0
        self.render_blocks = 0
        self.rebuild_ms = 0.0
        self.meter = StereoMeter(sample_rate)

    # -- the sound ------------------------------------------------------------------

    def mix(self, blocks):
        t0 = self.clock()
        try:
            return self._mix(blocks)
        finally:
            self.render_ms += self.clock() - t0
            self.render_blocks += 1

    def _mix(self, blocks):
        mul, mix = _audiodsp.multiply_s16, _audiodsp.mix_s16
        acc = None
        head = gain_buffer(HEADROOM)
        for block in blocks:
            if block is None:
                continue
            block = mul(block, head, 1.0, 2)
            acc = block if acc is None else mix(acc, block)
        out = acc if acc is not None else bytes(BLOCK_BYTES)
        if self.puller is not None:
            self.feed.push(bytes(out))
            out = self.puller.block()
        if self.level < 0.9999:
            out = mul(out, gain_buffer(self.level), 1.0, 2)
        self.meter.feed(out)
        self.frame += BLOCK
        return bytes(out)

    def load(self):
        """The share of real time spent mixing since the last call."""
        load = self.render_ms / (max(1, self.render_blocks) * BLOCK * 1000.0 / self.sample_rate)
        self.render_ms, self.render_blocks = 0.0, 0
        return load

    def report(self):
        """The master's part of a status report."""
        return {"short": self.feed.short, "load": self.load(), "rebuild_ms": self.rebuild_ms,
                # the mix goes out 6 dB down and the output stage restores it
                "meter": [round(v + 6, 1) if v > -100 else v for v in self.meter.read()]}

    # -- the project: only its master matters here --------------------------------

    def apply(self, op):
        if self.project is None:
            if op["op"] != "replace":
                return
            self.project = {}
        model.apply(self.project, op)
        self.sync()

    def apply_many(self, ops):
        if self.project is None:
            return
        for op in ops:
            model.apply(self.project, op)
        self.sync()

    def sync(self):
        m = (self.project or {}).get("master") or {}
        self.level = max(0.0, min(1.5, m.get("volume", 100) / 100.0))
        spec = [e for e in m.get("rack", []) if not e.get("bypass")]
        key = repr([e["name"] for e in spec])
        delay = DELAY if m.get("rack") else 0
        if key != self.rack_spec or delay != self.delay:
            # No pause here, unlike an engine building an instrument: a rack
            # builds in a few milliseconds, well inside the output's queue,
            # and a pause makes the output throw its queue away and refill.
            t0 = self.clock()
            old = self.rack
            if delay != self.delay:
                self.feed = Feed(self.sample_rate)          # starts one block ahead
            self.rack = build_rack(spec, self.feed, self.sample_rate) if spec else None
            if self.rack is not None:
                self.puller = Puller(self.rack.output)
            elif delay:
                self.puller = Puller(self.feed)     # bypassed: keep the delay
            else:
                self.puller = None
            self.delay = delay
            self.rack_spec = key
            if old is not None:
                old.deinit()
            self.rebuild_ms = self.clock() - t0
        set_macros(self.rack, spec)

    # -- an export: the summed tracks, through a rack of its own ------------------

    def render_offline(self, pcm, frames):
        """``pcm`` is the tracks' sum, already 6 dB down as int16. Run it through
        a rack of its own, so the live one keeps its state, and return the result."""
        m = self.project.get("master") or {}
        spec = [e for e in m.get("rack", []) if not e.get("bypass")]
        feed = Feed(self.sample_rate)
        rack = build_rack(spec, feed, self.sample_rate) if spec else None
        set_macros(rack, spec)
        puller = Puller(rack.output if rack is not None else feed)
        delay = DELAY        # with no rack the feed passes straight through, still a block late
        gain = gain_buffer(self.level) if self.level < 0.9999 else None
        mul = _audiodsp.multiply_s16
        src = memoryview(pcm)
        out = bytearray()
        blocks = (frames + delay + BLOCK - 1) // BLOCK
        for k in range(blocks):
            block = bytes(src[k * BLOCK_BYTES: (k + 1) * BLOCK_BYTES])
            if len(block) < BLOCK_BYTES:
                block += bytes(BLOCK_BYTES - len(block))
            feed.push(block)
            b = puller.block()
            out += mul(b, gain, 1.0, 2) if gain is not None else b
        if rack is not None:
            rack.deinit()
        # The first DELAY frames are the feed's head start, not the song.
        return out[delay * 4: (delay + frames) * 4]
