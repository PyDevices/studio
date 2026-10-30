"""The two ways to run the engines and the mixer: processes or threads.

Both give the UI the same object:

    runner.start()                    engines and mixer running
    runner.send(i, msg)               a UI message to engine i
    runner.broadcast(msg)             ... to every engine
    runner.midi(status, d1, d2, ns)   a MIDI message to every engine (the processes
                                      runner can read MIDI itself instead: make(midi_names=))
    runner.mixer(msg)                 a message to the mixer (master ops, stats, tap)
    runner.poll()                     messages for the UI: engines' dicts, and the mixer's
    runner.stop()

Processes: one ``multiprocessing`` (spawn) process per engine and one for the
mixer. The UI and the mixer each have a pipe to every engine; audio goes
through one shared-memory ring per engine, so no block is pickled. Threads:
the same code on threads in the UI's process, with queues, and the same rings
in plain memory.
"""

import multiprocessing as mp
import pickle
import queue
import threading
import time
from multiprocessing import connection, shared_memory

import ring as ringmod

RING_BLOCKS = 64


# -- processes ----------------------------------------------------------------------


class _PipeEngineIO:
    def __init__(self, from_ui, from_mixer, to_ui, doorbell):
        self.ins = [from_ui, from_mixer]
        self.to_ui = to_ui
        self.doorbell = doorbell

    def get(self, timeout):
        out = []
        for c in connection.wait(self.ins, timeout):
            while c.poll():
                out.append(c.recv())
        return out

    def poll(self):
        out = []
        for c in self.ins:
            while c.poll():
                out.append(c.recv())
        return out

    def post(self, msg):
        self.to_ui.send(msg)

    def done(self):
        self.doorbell.send_bytes(b".")


def _engine_process(index, shm_name, blocks, from_ui, from_mixer, to_ui, doorbell, plant, prio):
    import engine_loop

    _priority(prio)
    shm = shared_memory.SharedMemory(name=shm_name)
    r = ringmod.Ring(shm.buf, blocks)
    try:
        engine_loop.run(index, _PipeEngineIO(from_ui, from_mixer, to_ui, doorbell), r, plant)
    finally:
        r.release()
        shm.close()


class _PipeMixerIO:
    def __init__(self, ctl, needs, doorbells):
        self.ctl = ctl
        self.needs = needs
        self.doorbells = doorbells
        self.lock = threading.Lock()     # the MIDI thread writes to the same pipes

    def need(self, i, blocks, asked):
        with self.lock:
            self.needs[i].send(("n", blocks, asked))

    def midi(self, status, d1, d2, ns):
        """A MIDI message to every engine (from this process's MIDI thread)."""
        m = ("m", status, d1, d2, ns)
        with self.lock:
            for c in self.needs:
                c.send(m)

    def wait(self, timeout):
        """Sleep until an engine delivers or ``timeout`` s pass."""
        ready = connection.wait(self.doorbells, timeout) if timeout > 0 else [d for d in self.doorbells if d.poll()]
        for d in ready:
            while d.poll():
                d.recv_bytes()

    def control(self):
        out = []
        while self.ctl.poll():
            out.append(self.ctl.recv())
        return out

    def reply(self, msg):
        self.ctl.send(msg)


def _mixer_process(names, blocks, ctl, needs, doorbells, opts, prio, midi_names):
    import mixer

    _priority(prio)
    shms = [shared_memory.SharedMemory(name=n) for n in names]
    rings = [ringmod.Ring(s.buf, blocks) for s in shms]
    io = _PipeMixerIO(ctl, needs, doorbells)
    midi = None
    if midi_names is not None:
        # MIDI is read here, not in the UI's process: there it would wait
        # whenever the UI holds the GIL (building a view can take 100 ms),
        # and a key would sound late. This process does little but mix.
        from midi import MidiIn

        midi = MidiIn(io, midi_names)
        midi.start()
    try:
        mixer.run(rings, io, opts)
    finally:
        if midi is not None:
            midi.stop()
        for r in rings:
            r.release()
        for s in shms:
            s.close()


def _priority(prio):
    """Raise this process's scheduling class (Windows), as an audio app would."""
    if not prio:
        return
    try:
        import ctypes

        k = ctypes.windll.kernel32
        k.SetPriorityClass(k.GetCurrentProcess(), 0x80 if prio == "high" else 0x8000)  # HIGH / ABOVE_NORMAL
    except Exception as e:  # noqa: BLE001
        print("priority:", e)


class ProcessRunner:
    def __init__(self, engines, mixer_opts, plant=None, prio=None, midi_names=None):
        self.n = engines
        self.opts = mixer_opts
        self.plant = plant
        self.prio = prio
        self.midi_names = midi_names     # read MIDI in the mixer's process (None: the caller relays it)

    def start(self):
        ctx = mp.get_context("spawn")
        self.lock = threading.Lock()
        size = ringmod.size(RING_BLOCKS)
        self.shms = [shared_memory.SharedMemory(create=True, size=size) for _ in range(self.n)]
        for s in self.shms:
            ringmod.Ring(s.buf, RING_BLOCKS, init=True).release()
        self.to_engine, self.from_engine, mixer_needs, bells_rx, self.procs = [], [], [], [], []
        for i in range(self.n):
            ui_tx_rx = ctx.Pipe(duplex=False)        # (recv end, send end)
            mx = ctx.Pipe(duplex=False)
            up = ctx.Pipe(duplex=False)
            bell = ctx.Pipe(duplex=False)
            self.to_engine.append(ui_tx_rx[1])
            mixer_needs.append(mx[1])
            self.from_engine.append(up[0])
            bells_rx.append(bell[0])
            p = ctx.Process(target=_engine_process, name="engine%d" % i, daemon=True,
                            args=(i, self.shms[i].name, RING_BLOCKS, ui_tx_rx[0], mx[0], up[1], bell[1],
                                  self.plant, self.prio))
            p.start()
            self.procs.append(p)
        self.ctl, ctl_far = ctx.Pipe(duplex=True)
        m = ctx.Process(target=_mixer_process, name="mixer", daemon=True,
                        args=([s.name for s in self.shms], RING_BLOCKS, ctl_far, mixer_needs, bells_rx,
                              self.opts, self.prio, self.midi_names))
        m.start()
        self.procs.append(m)

    # The UI thread and the MIDI thread both send: a pipe isn't safe for two writers.
    def send(self, i, msg):
        with self.lock:
            self.to_engine[i].send(msg)

    def broadcast(self, msg):
        with self.lock:
            for c in self.to_engine:
                c.send(msg)

    def midi(self, status, d1, d2, ns):
        m = ("m", status, d1, d2, ns)
        with self.lock:
            for c in self.to_engine:
                c.send(m)

    def mixer(self, msg):
        with self.lock:
            self.ctl.send(msg)

    def poll(self):
        out = []
        for c in self.from_engine + [self.ctl]:
            while c.poll():
                out.append(c.recv())
        return out

    def stop(self):
        try:
            self.mixer({"t": "quit"})
            for c in self.to_engine:
                c.send(("q",))
        except OSError:
            pass
        for p in self.procs:
            p.join(5)
            if p.is_alive():
                p.terminate()
        for s in self.shms:
            s.close()
            s.unlink()


# -- threads ------------------------------------------------------------------------------


class _QueueEngineIO:
    def __init__(self, inbox, outbox, bell):
        self.inbox = inbox
        self.outbox = outbox
        self.bell = bell

    def get(self, timeout):
        try:
            out = [self.inbox.get(timeout=timeout)]
        except queue.Empty:
            return []
        return out + self.poll()

    def poll(self):
        out = []
        while True:
            try:
                out.append(self.inbox.get_nowait())
            except queue.Empty:
                return out

    def post(self, msg):
        self.outbox.put(msg)

    def done(self):
        self.bell.set()


class _QueueMixerIO:
    def __init__(self, ctl, replies, inboxes, bell):
        self.ctl = ctl
        self.replies = replies
        self.inboxes = inboxes
        self.bell = bell

    def need(self, i, blocks, asked):
        self.inboxes[i].put(("n", blocks, asked))

    def wait(self, timeout):
        if timeout > 0:
            self.bell.wait(timeout)
        self.bell.clear()

    def control(self):
        out = []
        while True:
            try:
                out.append(self.ctl.get_nowait())
            except queue.Empty:
                return out

    def reply(self, msg):
        self.replies.put(msg)


class ThreadRunner:
    def __init__(self, engines, mixer_opts, plant=None, prio=None, midi_names=None):
        self.n = engines
        self.opts = mixer_opts
        self.plant = plant

    def start(self):
        import engine_loop
        import mixer

        self.rings = [ringmod.Ring(bytearray(ringmod.size(RING_BLOCKS)), RING_BLOCKS, init=True)
                      for _ in range(self.n)]
        self.inboxes = [queue.SimpleQueue() for _ in range(self.n)]
        self.outbox = queue.SimpleQueue()
        self.ctl = queue.SimpleQueue()
        bell = threading.Event()
        self.threads = []
        for i in range(self.n):
            io = _QueueEngineIO(self.inboxes[i], self.outbox, bell)
            t = threading.Thread(target=engine_loop.run, args=(i, io, self.rings[i], self.plant),
                                 name="engine%d" % i, daemon=True)
            t.start()
            self.threads.append(t)
        mio = _QueueMixerIO(self.ctl, self.outbox, self.inboxes, bell)
        t = threading.Thread(target=mixer.run, args=(self.rings, mio, self.opts), name="mixer", daemon=True)
        t.start()
        self.threads.append(t)

    # Each engine gets its own copy of a message, as a pipe (or a browser
    # worker's postMessage) gives it: an op's value is a list or dict the
    # engine's project keeps, and one shared object would be changed by
    # every engine that applies the op (a bar repeated once per engine).
    def send(self, i, msg):
        self.inboxes[i].put(_copy(pickle.dumps(msg, pickle.HIGHEST_PROTOCOL)))

    def broadcast(self, msg):
        data = pickle.dumps(msg, pickle.HIGHEST_PROTOCOL)
        for q in self.inboxes:
            q.put(_copy(data))

    def midi(self, status, d1, d2, ns):
        m = ("m", status, d1, d2, ns)
        for q in self.inboxes:
            q.put(m)

    def mixer(self, msg):
        self.ctl.put(_copy(pickle.dumps(msg, pickle.HIGHEST_PROTOCOL)))

    def poll(self):
        out = []
        while True:
            try:
                out.append(self.outbox.get_nowait())
            except queue.Empty:
                return out

    def stop(self):
        self.mixer({"t": "quit"})
        for q in self.inboxes:
            q.put(("q",))
        for t in self.threads:
            t.join(5)


def _copy(data):
    return pickle.loads(data)


def make(kind, engines, mixer_opts, plant=None, prio=None, midi_names=None):
    """``midi_names``: the processes runner reads those MIDI inputs itself, in
    the mixer's process (empty: every input). The threads runner, or None,
    leaves MIDI to the caller (``runner.midi``)."""
    if kind == "processes":
        return ProcessRunner(engines, mixer_opts, plant, prio, midi_names)
    return ThreadRunner(engines, mixer_opts, plant, prio)


def wait_for(runner, pred, timeout, sink=None):
    """Poll the runner until ``pred(msg)`` for some message; others go to ``sink``."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        for m in runner.poll():
            if pred(m):
                return m
            if sink is not None:
                sink(m)
        time.sleep(0.005)
    return None
