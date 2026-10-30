"""One engine's audio on its way to the mixer: a single-producer,
single-consumer ring of int16 stereo frames, plus a few shared counters.

The same class sits on a ``multiprocessing.shared_memory`` block (the
processes runner) or a plain ``bytearray`` (the threads runner). The engine
only writes frames and its own counters; the mixer only reads frames and
writes its own. Each counter is one aligned 64-bit slot, written by one side,
so a reader sees the old value or the new one.

Header slots (int64):

    W        frames the engine has written (engine)
    R        frames the mixer has read (mixer)
    PAUSE    pause generation: odd while the engine is busy building (engine)
    OFFSET   rendered frame k plays at context frame k + offset (mixer)
    ASKED    the context frame playing when the mixer last asked (mixer)
    CFRAME   a context frame ...                                  (mixer)
    CNS      ... and the perf_counter_ns it played at             (mixer)
    UNDER    the lane's underruns so far (mixer)
"""

BLOCK = 256
FRAME_BYTES = 4
W, R, PAUSE, OFFSET, ASKED, CFRAME, CNS, UNDER = range(8)
SLOTS = 16
HEADER = SLOTS * 8
NONE = -(1 << 62)


def size(blocks):
    return HEADER + blocks * BLOCK * FRAME_BYTES


class Ring:
    def __init__(self, buf, blocks, init=False):
        self.mem = memoryview(buf)
        self.h = self.mem[:HEADER].cast("q")
        self.data = self.mem[HEADER:HEADER + blocks * BLOCK * FRAME_BYTES]
        self.cap = blocks * BLOCK
        if init:
            for i in range(SLOTS):
                self.h[i] = 0
            self.h[OFFSET] = NONE

    # -- the engine's side --------------------------------------------------------

    def write(self, pcm):
        """Append whole frames; returns how many fit (all of them, if the mixer
        never asks for more than the ring holds)."""
        n = len(pcm) // FRAME_BYTES
        w = self.h[W]
        room = self.cap - (w - self.h[R])
        n = min(n, room)
        at = w % self.cap
        first = min(n, self.cap - at)
        mv = memoryview(pcm)
        self.data[at * 4:(at + first) * 4] = mv[:first * 4]
        if n > first:
            self.data[0:(n - first) * 4] = mv[first * 4:n * 4]
        self.h[W] = w + n
        return n

    def offset(self):
        o = self.h[OFFSET]
        return None if o == NONE else o

    def key_frame(self, stamp_ns, sample_rate):
        """The context frame a MIDI message stamped ``stamp_ns`` happened at."""
        cns = self.h[CNS]
        if not cns:
            return self.h[ASKED]
        return self.h[CFRAME] + int((stamp_ns - cns) * sample_rate // 1_000_000_000)

    # -- the mixer's side ---------------------------------------------------------

    def queued(self):
        return self.h[W] - self.h[R]

    def read(self, frames):
        r = self.h[R]
        at = r % self.cap
        first = min(frames, self.cap - at)
        out = bytes(self.data[at * 4:(at + first) * 4])
        if frames > first:
            out += bytes(self.data[0:(frames - first) * 4])
        self.h[R] = r + frames
        return out

    def drop(self):
        """Throw away everything queued (a stream restarting after a pause)."""
        self.h[R] = self.h[W]

    def release(self):
        self.h.release()
        self.data.release()
        self.mem.release()
