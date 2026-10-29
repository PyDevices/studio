# SPDX-FileCopyrightText: 2026 PyDevices
#
# SPDX-License-Identifier: MIT
"""The sound module's contract, on any interpreter: what a part plays for
each bank and program, the macro CCs, volume and pan on an instrument
part, the Identity Reply, the tally of what played, the file ports, and,
where the interpreter has the audio modules, a real instrument heard and
the headless runner's timing.

    python tests/engine_contract.py [--plant=NAME] [--tmp FOLDER]
    micropython tests/engine_contract.py
    circuitpython tests/engine_contract.py

Stand-in instruments and a stand-in mixer carry the routing checks, so
those need nothing but the interpreter. ``--plant=NAME`` breaks the module
in one known way first (``PLANTS``), and the check that guards against it
must fail: ``tests/test_engine.py`` runs every plant.

It ends with ``N passed, M failed`` and exits non-zero on a failure.
"""

import sys

try:
    _here = __file__
except NameError:  # pragma: no cover
    _here = sys.argv[0]
_sep = "\\" if "\\" in _here else "/"
_dir = _here.rsplit(_sep, 1)[0] if _sep in _here else "."
sys.path.insert(0, _sep.join((_dir, "..", "lib")))

from soundmodule import engine, pipes, run  # noqa: E402

PLANTS = ("bank-ignored", "lsb-ignored", "eager-bank", "macro-shift", "level-linear", "identity-wrong",
          "identity-any-device", "tally-final", "pipe-drops-peek", "frames-miscounted")

RESULTS = [0, 0]


def check(name, ok, detail=""):
    if ok:
        RESULTS[0] += 1
        print("ok   " + name)
    else:
        RESULTS[1] += 1
        print("FAIL " + name + (": " + detail if detail else ""))


# -- stand-ins -------------------------------------------------------------------


class Voice:
    def __init__(self):
        self.playing = None
        self.level = 1.0
        self.panning = 0.0

    def play(self, source, loop=False):
        self.playing = source

    def stop(self):
        self.playing = None


class Mixer:
    def __init__(self):
        self.voice = [Voice() for _ in range(17)]


class Fake:
    """An instrument that remembers what it was asked."""

    def __init__(self, name):
        self.name = name
        self.macro_labels = ("Volume", "Cutoff", "Resonance")
        self.macros = {}
        self.patches = {0: ("Default",), 1: ("Second",)}
        self.program = None
        self.notes = []
        self.output = object()
        self.ccs = []

    def set_macro(self, i, value):
        self.macros[i] = value

    def program_change(self, n):
        self.program = n

    def note_on(self, note, velocity):
        self.notes.append(note)

    def note_off(self, note):
        pass

    def all_notes_off(self):
        pass

    def reset(self):
        pass

    def pitch_bend(self, value):
        pass

    def control_change(self, cc, value):
        self.ccs.append((cc, value))

    def deinit(self):
        pass


def module(**kw):
    made = []

    def create(name, rate, channels, bank, pool=None):
        inst = Fake(name)
        made.append(inst)
        return inst

    m = engine.Module(48000, 2, create=create, mixer=Mixer(), effects=False, **kw)
    return m, made


def select(m, channel, msb, lsb, program):
    m.feed(bytes((0xB0 | channel, 0, msb, 0xB0 | channel, 32, lsb, 0xC0 | channel, program)))


# -- the checks ------------------------------------------------------------------


def check_gm_without_setup():
    m, made = module()
    m.feed(bytes((0x90, 60, 100)))
    part = m.parts[0]
    check("a part with no setup plays General MIDI", part.inst_name == engine.MELODIC and len(made) == 1,
          "made %r" % (part.inst_name,))


def check_bank_instruments():
    m, made = module()
    juno = engine.INSTRUMENTS.index("juno106")
    select(m, 0, engine.BANK_INSTRUMENTS, 0, juno)
    part = m.parts[0]
    check("bank 80, program n plays the n-th instrument", part.inst_name == "juno106", repr(part.inst_name))
    check("the instrument plays on its own mixer voice", m.mixer.voice[0].playing is part.inst.output)
    check("its name has its patch", m.name(0) == "juno106: Default", m.name(0))
    select(m, 9, engine.BANK_DRUM_MACHINES, 0, engine.DRUM_MACHINES.index("tr808"))
    check("bank 81, program n plays the n-th drum machine", m.parts[9].inst_name == "tr808",
          repr(m.parts[9].inst_name))
    select(m, 3, engine.BANK_INSTRUMENTS, 0, 127)
    m.feed(bytes((0x93, 60, 100)))
    check("a program past the last instrument plays nothing", m.parts[3].inst is None and m.parts[3].notes == 0)


def check_lsb_patch():
    m, made = module()
    select(m, 1, engine.BANK_INSTRUMENTS, 1, engine.INSTRUMENTS.index("minimoog"))
    inst = m.parts[1].inst
    check("the bank LSB picks the instrument's patch", inst is not None and inst.program == 1,
          repr(inst and inst.program))
    check("and the name says so", m.name(1) == "minimoog: Second", m.name(1))


def check_bank_waits_for_program():
    m, made = module()
    select(m, 0, engine.BANK_INSTRUMENTS, 0, 0)
    first = m.parts[0].inst
    m.feed(bytes((0xB0, 0, 0)))
    check("a bank select alone keeps the instrument playing", m.parts[0].inst is first and len(made) == 1,
          "now %r" % (m.parts[0].inst_name,))
    m.feed(bytes((0xC0, 5)))
    check("the next program change brings the new bank's", m.parts[0].inst_name == engine.MELODIC,
          repr(m.parts[0].inst_name))


def check_macro_ccs():
    m, made = module()
    select(m, 0, engine.BANK_INSTRUMENTS, 0, 0)
    inst = m.parts[0].inst
    m.feed(bytes((0xB0, engine.MACRO_CC + 1, 77)))
    check("CC %d + i sets macro i" % engine.MACRO_CC, inst.macros.get(1) == 77, repr(inst.macros))
    m.feed(bytes((0xB0, engine.MACRO_CC + 3, 50)))
    check("a macro the instrument hasn't got is left alone", 3 not in inst.macros, repr(inst.macros))


def check_volume_pan():
    m, made = module()
    select(m, 2, engine.BANK_INSTRUMENTS, 0, 0)
    m.feed(bytes((0xB2, 7, 64, 0xB2, 10, 0)))
    voice = m.mixer.voice[2]
    want = engine.PART_LEVEL * (64 / 127.0) ** 2
    check("CC7 sets an instrument part's level, squared", abs(voice.level - want) < 1e-6,
          "%r, not %r" % (voice.level, want))
    check("CC10 pans it", abs(voice.panning + 1.0) < 1e-6, repr(voice.panning))


def check_identity():
    replies = []
    m, made = module(send=replies.append)
    m.feed(bytes((0xF0, 0x7E, 0x7F, 0x06, 0x01, 0xF7)))
    want = bytes((0xF0, 0x7E, 0x7F, 0x06, 0x02, 0x7D, 0x01, 0x00, 0x01, 0x00, 0x00, 0x01, 0x00, 0x00, 0xF7))
    got = bytes(replies[0]) if replies else b""
    check("an Identity Request is answered with the Identity Reply", got == want, got.hex() if got else "nothing")
    m.feed(bytes((0xF0, 0x7E, 0x10, 0x06, 0x01, 0xF7)))
    check("one for another device is not", len(replies) == 1 and m.identities == 1,
          "%d replies" % len(replies))


def check_tally():
    m, made = module()
    select(m, 0, engine.BANK_INSTRUMENTS, 0, 14)
    m.feed(bytes((0x90, 60, 100, 0x80, 60, 0, 0x90, 62, 100)))
    m.feed(bytes((0xB0, 0, 0)))  # a sequencer's reset on stop
    lines = []
    run.report(m, 9, [0, 0], log=lines.append)
    text = "\n".join(lines)
    check("the report says what played, not what the bank was left at",
          "2 notes, bank 80:0 program 14, juno106" in text, text)


def check_pipes(tmp):
    folder = tmp + "/soundmodule-pipes"
    try:
        import os

        os.mkdir(folder)
    except OSError:
        pass
    for name in ("in.mid", "out.mid"):
        open(folder + "/" + name, "wb").close()
    midi_in, midi_out = pipes.ports(folder)
    check("an empty pipe reads nothing", midi_in.readinto(bytearray(8)) is None)
    with open(folder + "/in.mid", "ab") as f:
        f.write(bytes((0x90, 60, 100)))
    peeked = midi_in.any()
    buf = bytearray(8)
    n = midi_in.readinto(buf)
    check("bytes appended later are read once, after a peek", peeked == 3 and n == 3 and bytes(buf[:3]) == b"\x90\x3c\x64"
          and midi_in.readinto(buf) is None, "peek %r, read %r" % (peeked, n))
    midi_out.write(b"\xf0\x7e\xf7")
    with open(folder + "/out.mid", "rb") as f:
        check("what the module writes is appended", f.read() == b"\xf0\x7e\xf7")
    midi_in.close()
    midi_out.close()


def check_real(tmp):
    """A real instrument heard, and the runner's frames counted right."""
    try:
        import audiocore
        import audioinstruments  # noqa: F401
    except ImportError as e:
        print("skip a real instrument: %s" % (e,))
        return
    import array

    m = engine.Module(48000, 2, effects=False, instruments=True)
    select(m, 0, engine.BANK_INSTRUMENTS, 0, engine.INSTRUMENTS.index("juno106"))
    m.feed(bytes((0x90, 57, 100)))
    peak = 0
    for _ in range(12):
        for v in array.array("h", bytes(audiocore.get_buffer(m.output)[1])):
            if v > peak or -v > peak:
                peak = v if v > 0 else -v
    check("a real juno106 is heard", peak > 500 and m.name(0) == "juno106: Default", "peak %d" % peak)
    m.feed(bytes((0x80, 57, 0)))

    class Quiet:
        def readinto(self, buf, nbytes=None):
            return None

    path = tmp + "/soundmodule-contract.wav"
    wav = run.Wav(path)
    run.serve(m, Quiet(), wav, seconds=0.5, tail=0.0)
    wav.close()
    seconds = wav.bytes / (2.0 * run.CHANNELS * run.RATE)
    check("the runner renders by the clock: half a second is half a second", 0.45 <= seconds <= 0.75,
          "%.2f s" % seconds)
    m.close()


# -- the plants ------------------------------------------------------------------


def plant(name):
    M = engine.Module
    if name == "bank-ignored":
        M.wanted = lambda self, part: engine.DRUMS if part.drums else engine.MELODIC
    elif name == "lsb-ignored":
        old = M._select

        def _select(self, part):
            lsb = part.bank_lsb
            part.bank_lsb = 0
            old(self, part)
            part.bank_lsb = lsb

        M._select = _select
    elif name == "eager-bank":
        old = M.control

        def control(self, part, cc, value):
            old(self, part, cc, value)
            if cc in (0, 32) and part.inst is not None:
                self.instrument(part)

        M.control = control
    elif name == "macro-shift":
        old = M.control

        def control(self, part, cc, value):
            if engine.MACRO_CC <= cc < engine.MACRO_CC + engine.MACRO_CCS:
                cc += 1  # macro i on CC 103 + i
            old(self, part, cc, value)

        M.control = control
    elif name == "level-linear":
        def _level(self, part):
            if self._own_voice(part):
                self.mixer.voice[part.channel].level = engine.PART_LEVEL * part.volume / 127.0
                self.mixer.voice[part.channel].panning = (part.pan - 64) / 63.0

        M._level = _level
    elif name == "identity-wrong":
        engine.identity_reply = lambda device_id=0x7F: bytes((0xF0, 0x7E, 0x7F, 0x06, 0x02, 0x7D, 0xF7))
    elif name == "identity-any-device":
        engine.DEVICE_ID = 0x10
    elif name == "tally-final":
        def report(module, total, worst, log=print):
            for part in module.parts:
                if part.notes:
                    log("  channel %2d: %4d notes, bank %d:%d program %d, %s"
                        % (part.channel + 1, part.notes, part.bank_msb, part.bank_lsb, part.program,
                           module.name(part.channel)))

        run.report = report
    elif name == "pipe-drops-peek":
        old = pipes.FileIn.readinto

        def readinto(self, buf, nbytes=None):
            self._pending = 0
            return old(self, buf, nbytes)

        pipes.FileIn.readinto = readinto
    elif name == "frames-miscounted":
        run._frames = lambda data: len(data) // 8
    else:
        raise SystemExit("no plant " + name)
    print("PLANTED " + name)


def main(argv):
    tmp = "."
    for i, a in enumerate(argv):
        if a.startswith("--plant="):
            plant(a[len("--plant="):])
        elif a == "--tmp":
            tmp = argv[i + 1]
    print("soundmodule contract on %s %s" % (sys.implementation.name, sys.platform))
    check_gm_without_setup()
    check_bank_instruments()
    check_lsb_patch()
    check_bank_waits_for_program()
    check_macro_ccs()
    check_volume_pan()
    check_identity()
    check_tally()
    check_pipes(tmp)
    check_real(tmp)
    print("%d passed, %d failed" % (RESULTS[0], RESULTS[1]))
    if RESULTS[1]:
        sys.exit(1)


if __name__ == "__main__":
    main(sys.argv[1:])
