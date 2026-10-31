# SPDX-FileCopyrightText: 2026 PyDevices
#
# SPDX-License-Identifier: MIT
"""The sound module's engine: sixteen parts, one per MIDI channel, mixed
through chorus and reverb. It has no face of its own: feed it MIDI bytes
and pull its ``output``, from a UI (midiEngine), a headless loop
(``soundmodule.run``) or a test.

What a part plays
-----------------
Bank Select (CC0, the MSB, and CC32, the LSB) then Program Change choose:

- **MSB 0-79: General MIDI**, from a SoundFont. ``soundfont`` plays the
  melodic channels and ``soundfont_drums`` channel 10, and the bank select
  reaches the SoundFont as it is, so a GS or XG variation bank plays the
  variation the bank has. Program Change is the GM program.
- **MSB 80: audioinstruments' instruments** (``INSTRUMENTS``). Program
  Change *n* plays the *n*-th of them, and the LSB picks its patch.
- **MSB 81: audioinstruments' drum machines** (``DRUM_MACHINES``), the same
  way.

A part's instrument is made the first time its channel is played or given
a program, and made again when a Program Change asks for a different one.
Nothing has to be set up first: a part that has had no Bank Select plays
General MIDI, as a GM module's do.

Controllers
-----------
Each macro of the part's instrument is a CC: macro *i* is CC ``MACRO_CC +
i`` (102 to 117, controllers MIDI leaves undefined), at the instrument's
own 0-127 scale. Volume (CC7) reaches a SoundFont part as CC7, which plays
it as the square law GM modules use (40 log10 of CC7/127 in dB, as
Expression), and pan (CC10) its Pan macro; on an instrument part both set
its level and pan in the mixer.
The sustain pedal (CC64) holds notes for every part, and Reset All
Controllers (121) also puts Expression (CC11) back to 127 and the mod wheel
and channel pressure to 0, as GM's RP-015 asks.

Registered parameters (RPN, CC101 and CC100, then Data Entry on CC6 and
CC38 or Increment and Decrement on CC96 and CC97) set a part's Pitch Bend
Sensitivity (RPN 0, two semitones at power-on, up to 24), Channel Fine
Tuning (RPN 1, a hundred cents either way) and Channel Coarse Tuning (RPN 2,
whole semitones), and RPN 127/127 (null) or any NRPN stops Data Entry
reaching them. Increment and Decrement step the bend range and the coarse
tuning a semitone and the fine tuning one 14-bit step. A SoundFont part
plays them (``soundfont``'s ``bend_range`` and ``tuning``); an
audioinstruments from before those properties gets the bend scaled into its
fixed two semitones, which a range under two plays right and a wider one
clips. audioinstruments' synths and drum machines don't bend, so nothing
reaches them. Reset All Controllers sets the RPN back to null and keeps the
values; GM System On puts the values back too. Reverb (CC91) and chorus
(CC93) are each part's send, but there is one reverb and one chorus on the
master, so the master's level is the mean of the sends of the parts in use
(the ones with an instrument), whatever order they came in. A true send per
part needs a bus in the sampler's kernel, which it doesn't have; rendering a
part's voices twice to make one would cost more than a board's block.
All Sound Off, Reset All Controllers and All Notes Off (120, 121, 123) do
what GM says. Any other controller reaches the instrument's
``control_change`` as it is.

System Exclusive
----------------
GM System On (and GM2 On, GS Reset and XG System On, which ask the same)
put every part back to its power-on state. The Identity Request (``F0 7E
<id> 06 01 F7``) is answered with an Identity Reply through ``send``, the
callable the module writes MIDI out with: manufacturer ``0x7D`` (the ID
MIDI sets aside for non-commercial use), family ``FAMILY``, model ``MODEL``
and ``VERSION``.

The voices
----------
Every SoundFont part reads the same open SoundFont and plays on the same
pool of voices (``VOICES`` of them, one sampler), as a hardware GM module's
parts do: a busy part takes voices an idle one isn't using, and the oldest
releasing voices in the module are faded out to keep a few ready, so a new
note never has to cut one off with a click. Every voice plays at ``GAIN``,
the headroom sixteen parts summed need. An instrument part has its own
voices, and its output is one voice of a mixer the pool plays into too.

``prepare()`` opens the SoundFont before anything plays. Reading a big
bank's presets takes a moment and some megabytes, and done on the first
program change it would land inside whatever sent it. Once it is open a
part costs only its patch list, and its samples are read on its notes, the
bank keeping a bounded number of them: on a board, half the memory free
once the bank is open (``CACHE_SHARE``); on a desktop, soundfont's own
bound. An audioinstruments from before that bound keeps every sample it
reads; then the module lets them all go whenever they pass the bound, or
``FALLBACK_CACHE_BYTES`` if that is less.

No UI and no sound card in this module, so a test can drive it on CPython,
MicroPython or CircuitPython with stand-in instruments (``create=``).
"""

import sys

DRUM_CHANNEL = 9  # MIDI channel 10
MELODIC = "soundfont"
DRUMS = "soundfont_drums"
PERCUSSION_BANK = 128

#: Bank Select MSBs: below ``BANK_INSTRUMENTS`` is General MIDI.
BANK_INSTRUMENTS = 80
BANK_DRUM_MACHINES = 81

#: audioinstruments' melodic instruments, by program number on bank
#: ``BANK_INSTRUMENTS``. New ones go on the end, so a song's program numbers
#: keep their sound. ``None`` is a program an earlier instrument set had and
#: this one doesn't: it plays nothing, and keeps the numbers after it where
#: they were.
INSTRUMENTS = (
    "polya6", "patch2600", "tonewheel3", "clavichord", "electric_grand70",
    "poly80", None, None, None, None, None, "compact_organ", None, None,
    "poly106", "poly8", None, None, None, None, "mono_synth", "duo20", None,
    "west_coast_synth", None, "polyxa", "duosynth", "reed_piano", "poly6",
    None, "poly5", None, "electric_piano", "mono101", "string_ensemble",
    "bass_pedals", "acid303", None, None, "combo_organ", "vocoder330",
    "hybrid_mono", "reed_piano200a", "mono_one", "state_variable_mono",
)

#: audioinstruments' drum machines, by program number on bank
#: ``BANK_DRUM_MACHINES``, with ``None`` as above.
DRUM_MACHINES = (
    None, "rhythm78", None, None, None, None, "electronic_drums5", None,
    "rhythm606", None, "rhythm808", "rhythm909",
)

CC_BANK_MSB, CC_BANK_LSB = 0, 32
CC_VOLUME, CC_PAN, CC_SUSTAIN = 7, 10, 64
CC_REVERB, CC_CHORUS = 91, 93
CC_ALL_SOUND_OFF, CC_RESET_CONTROLLERS, CC_ALL_NOTES_OFF = 120, 121, 123
CC_MOD_WHEEL, CC_EXPRESSION = 1, 11
CC_DATA_MSB, CC_DATA_LSB, CC_DATA_INC, CC_DATA_DEC = 6, 38, 96, 97
CC_NRPN_LSB, CC_NRPN_MSB, CC_RPN_LSB, CC_RPN_MSB = 98, 99, 100, 101
#: The registered parameters a part keeps, by (MSB, LSB): MIDI's numbers.
RPN_BEND_RANGE, RPN_FINE_TUNING, RPN_COARSE_TUNING = (0, 0), (0, 1), (0, 2)
RPN_NULL = (127, 127)
#: Their power-on values, as 14-bit Data Entry (MSB << 7 | LSB): two
#: semitones of bend, and both tunings centred.
RPN_DEFAULTS = {RPN_BEND_RANGE: 2 << 7, RPN_FINE_TUNING: 8192, RPN_COARSE_TUNING: 64 << 7}
MAX_BEND_RANGE = 24  # semitones, as GM2 and most modules cap it
#: The bend an audioinstruments without ``bend_range`` plays at full deflection.
NATIVE_BEND_RANGE = 2.0
#: Macro *i* of a part's instrument is CC ``MACRO_CC + i``.
MACRO_CC = 102
MACRO_CCS = 16

#: The Identity Reply's fields. 0x7D is MIDI's ID for non-commercial use.
MANUFACTURER = 0x7D
FAMILY = 0x0001
MODEL = 0x0001
VERSION = (0, 1, 0, 0)
#: The SysEx device ID this module answers to, as well as 0x7F (everyone).
DEVICE_ID = 0x7F

DEFAULT_VOLUME = 100
DEFAULT_PAN = 64
# The soundfont instruments' macros that CC7 and CC10 drive.
VOLUME_MACRO, PAN_MACRO = 0, 4
DEFAULT_REVERB = 40  # GM Level 1's power-on reverb send; chorus is 0
DEFAULT_CHORUS = 0
DEFAULT_MASTER = 80  # the output's level, 0-100
VOICES = 64  # the module's polyphony, shared by the sixteen parts
#: A board: MicroPython on a microcontroller, where one core renders the
#: audio. On an ESP32-P4 a dense orchestral file at 64 voices with chorus
#: and reverb takes about three quarters of each 5.3 ms block, which a
#: denser file or a slower chip would overrun; a board gets 48 voices and no
#: chorus (its delay line costs 0.4 ms a block even at a level of 0), about
#: 3 ms a block. ``voice_count`` and ``chorus=True`` take them back.
BOARD = sys.implementation.name in ("micropython", "circuitpython") and sys.platform not in (
    "linux", "darwin", "win32", "windows", "webassembly")
BOARD_VOICES = 48
RESERVE = 8  # voices kept ready by fading the oldest releases, so a steal is silent rather than a click
GAIN = 0.2  # every voice's level: headroom for sixteen parts summed, as GM players run these banks
#: An instrument part's mixer level at full volume: about where one of
#: them sits beside a SoundFont part at the same CC7.
PART_LEVEL = 0.35
BEND_CENTER = 8192
CACHE_SHARE = 2  # on a board, the bank keeps samples up to the free memory over this, once it is open
# The most sample bytes a bank may keep when audioinstruments' soundfont has
# no bound of its own: soundfont's own defaults, a board's and a desktop's.
FALLBACK_CACHE_BYTES = (64 if sys.implementation.name == "cpython" else 8) * 1048576
MIXER_BUFFER = 2048  # bytes: 512 stereo frames, about 11 ms at 48 kHz, so a live note waits little
PARTS_MIXER_BUFFER = 4096  # the stand-ins' mixer, as it always was

#: The 128 General MIDI programs, by number.
GM_NAMES = (
    "Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano", "Honky-tonk Piano",
    "Electric Piano 1", "Electric Piano 2", "Harpsichord", "Clavinet",
    "Celesta", "Glockenspiel", "Music Box", "Vibraphone", "Marimba", "Xylophone", "Tubular Bells", "Dulcimer",
    "Drawbar Organ", "Percussive Organ", "Rock Organ", "Church Organ",
    "Reed Organ", "Accordion", "Harmonica", "Tango Accordion",
    "Acoustic Guitar (nylon)", "Acoustic Guitar (steel)", "Electric Guitar (jazz)", "Electric Guitar (clean)",
    "Electric Guitar (muted)", "Overdriven Guitar", "Distortion Guitar", "Guitar Harmonics",
    "Acoustic Bass", "Electric Bass (finger)", "Electric Bass (pick)", "Fretless Bass",
    "Slap Bass 1", "Slap Bass 2", "Synth Bass 1", "Synth Bass 2",
    "Violin", "Viola", "Cello", "Contrabass", "Tremolo Strings", "Pizzicato Strings", "Orchestral Harp", "Timpani",
    "String Ensemble 1", "String Ensemble 2", "Synth Strings 1", "Synth Strings 2",
    "Choir Aahs", "Voice Oohs", "Synth Voice", "Orchestra Hit",
    "Trumpet", "Trombone", "Tuba", "Muted Trumpet", "French Horn", "Brass Section", "Synth Brass 1", "Synth Brass 2",
    "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax", "Oboe", "English Horn", "Bassoon", "Clarinet",
    "Piccolo", "Flute", "Recorder", "Pan Flute", "Blown Bottle", "Shakuhachi", "Whistle", "Ocarina",
    "Lead 1 (square)", "Lead 2 (sawtooth)", "Lead 3 (calliope)", "Lead 4 (chiff)",
    "Lead 5 (charang)", "Lead 6 (voice)", "Lead 7 (fifths)", "Lead 8 (bass + lead)",
    "Pad 1 (new age)", "Pad 2 (warm)", "Pad 3 (polysynth)", "Pad 4 (choir)",
    "Pad 5 (bowed)", "Pad 6 (metallic)", "Pad 7 (halo)", "Pad 8 (sweep)",
    "FX 1 (rain)", "FX 2 (soundtrack)", "FX 3 (crystal)", "FX 4 (atmosphere)",
    "FX 5 (brightness)", "FX 6 (goblins)", "FX 7 (echoes)", "FX 8 (sci-fi)",
    "Sitar", "Banjo", "Shamisen", "Koto", "Kalimba", "Bag pipe", "Fiddle", "Shanai",
    "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock", "Taiko Drum", "Melodic Tom", "Synth Drum", "Reverse Cymbal",
    "Guitar Fret Noise", "Breath Noise", "Seashore", "Bird Tweet", "Telephone Ring", "Helicopter", "Applause", "Gunshot",
)

#: GM's standard drum kits, by program number, for a bank that has no kit
#: at the number asked for (the nearest lower one plays, as on a GS module).
GM_KITS = ((0, "Standard"), (8, "Room"), (16, "Power"), (24, "Electronic"), (25, "TR-808"),
           (32, "Jazz"), (40, "Brush"), (48, "Orchestra"), (56, "SFX"))


def board_cache_bytes():
    """The bound for the open bank's samples on MicroPython and
    CircuitPython: the memory free now over ``CACHE_SHARE``. None on
    CPython, which keeps soundfont's."""
    if sys.implementation.name == "cpython":
        return None
    try:
        import gc

        gc.collect()
        return gc.mem_free() // CACHE_SHARE
    except (ImportError, AttributeError):
        return None


def _shared_voices():
    """``soundfont.Voices``, or None from an audioinstruments that predates
    it: then each part has its own voices, summed by a mixer."""
    try:
        from audioinstruments._rack.soundfont import Voices
    except ImportError:
        return None
    return Voices


def _default_create(name, rate, channels, bank, voice_pool=None):
    import audioinstruments
    if name not in (MELODIC, DRUMS):
        return audioinstruments.create(name, rate, channel_count=channels)
    if voice_pool is None:
        return audioinstruments.create(name, rate, channel_count=channels, bank=bank)
    try:
        return audioinstruments.create(name, rate, channel_count=channels, bank=bank, voice_pool=voice_pool)
    except TypeError:  # an audioinstruments from before the pool was voice_pool=
        return audioinstruments.create(name, rate, channel_count=channels, bank=bank, voices=voice_pool)


def identity_reply(device_id=DEVICE_ID):
    """The Identity Reply this module sends, F0 to F7."""
    return bytes((0xF0, 0x7E, device_id, 0x06, 0x02, MANUFACTURER,
                  FAMILY & 0x7F, FAMILY >> 7, MODEL & 0x7F, MODEL >> 7) + tuple(VERSION) + (0xF7,))


class _Now:
    """A context that changes nothing: writes go straight to the voices."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


_NOW = _Now()


def deferred(module):
    """``with engine.deferred(module):`` queues the voices' writes and sends
    them together at the end, the audio pump parked once for all of them
    (``soundfont.Voices.deferred``). Each write alone waits for the block
    the pump is pulling, which on a busy board is most of a block. Changes
    nothing for a module without shared voices, or no module."""
    pool = getattr(module, "voice_pool", None)
    if pool is not None and hasattr(pool, "deferred"):
        return pool.deferred()
    return _NOW


class Part:
    """One MIDI channel's state. ``inst`` is None until the channel is used,
    and ``inst_name`` is what it is (``MELODIC``, ``DRUMS`` or one of
    ``INSTRUMENTS`` and ``DRUM_MACHINES``)."""

    def __init__(self, channel):
        self.channel = channel
        self.inst = None
        self.inst_name = None
        self.notes = 0  # note-ons so far, for an activity light
        self.last = None  # (note, velocity) of the last note-on
        # What the part has played: (bank MSB, bank LSB, program) to
        # [note-ons, the name of what sounded]. A sequencer may move the
        # bank back when it stops, so the selection at the end can differ.
        self.played = {}
        self.reset()

    def reset(self):
        self.program = 0
        self.bank_msb = 0
        self.bank_lsb = 0
        self.volume = DEFAULT_VOLUME
        self.pan = DEFAULT_PAN
        self.sustain = False
        self.held = set()
        self.sustained = set()
        self.bend = BEND_CENTER
        self.rpn = RPN_NULL  # the parameter Data Entry sets; None for an NRPN
        self.params = dict(RPN_DEFAULTS)
        self.reverb = DEFAULT_REVERB  # CC91, the part's send
        self.chorus = DEFAULT_CHORUS  # CC93

    @property
    def bend_range(self):
        """Pitch Bend Sensitivity in semitones, cents included."""
        v = self.params[RPN_BEND_RANGE]
        return min(MAX_BEND_RANGE, (v >> 7) + (v & 0x7F) / 100.0)

    @property
    def tuning(self):
        """Fine and coarse tuning together, in semitones."""
        return ((self.params[RPN_COARSE_TUNING] >> 7) - 64) + (self.params[RPN_FINE_TUNING] - 8192) / 8192.0

    @property
    def drums(self):
        return self.channel == DRUM_CHANNEL

    @property
    def gm(self):
        """True when the part's bank is General MIDI's (the SoundFont's)."""
        return self.bank_msb < BANK_INSTRUMENTS


class Module:
    """Sixteen parts, mixed, with chorus and reverb on the master.

    With the default instruments every SoundFont part plays on one shared
    ``soundfont.Voices`` of ``voices`` voices. ``instruments`` (True on a
    desktop, False on a board until its cost is measured there) lets banks
    80 and 81 play audioinstruments' instruments: each one's output is a
    voice of ``mixer``, an ``audiomixer.Mixer`` the pool plays into as its
    last voice. Without them the pool is the source, as it always was.
    A ``create(name, rate, channels, bank)`` of your own (stand-ins in a
    test) gives each part its own output instead, summed by ``mixer``, made
    here when None. ``effects`` False leaves the source as the output
    (tests, or a board with no cycles to spare). ``voices`` defaults to
    ``VOICES``, or ``BOARD_VOICES`` on a board, and ``voice_count``
    changes it while the module plays; ``chorus`` False (a board's default)
    leaves the chorus out, and CC93 then sets a level nothing hears.
    ``send`` is called with the bytes of each reply the module sends (the
    Identity Reply); None drops them.
    """

    def __init__(self, rate=48000, channels=2, bank=None, create=None, mixer=None, effects=True, kits=None,
                 voices=None, chorus=None, instruments=None, send=None):
        self.rate = rate
        self.channels = channels
        self.bank = bank
        self.send = send
        self._create = create or _default_create
        self.parts = [Part(c) for c in range(16)]
        if voices is None:
            voices = BOARD_VOICES if BOARD else VOICES
        if chorus is None:
            chorus = not BOARD
        if instruments is None:
            instruments = not BOARD
        self.instruments = instruments or create is not None
        self.voice_pool = self.mixer = None
        shared = _shared_voices() if create is None and mixer is None else None
        if shared is not None:
            # Leave room for ``voice_count`` to rise to VOICES later; an
            # audioinstruments without ``max_voice_count`` sizes to the ceiling.
            try:
                self.voice_pool = shared(rate, channels, voice_count=voices, reserve=RESERVE,
                                         gain=GAIN, max_voice_count=max(voices, VOICES))
            except TypeError:
                self.voice_pool = shared(rate, channels, voice_count=voices, reserve=RESERVE, gain=GAIN)
            source = self.voice_pool.output
            if instruments:
                self.mixer = self._make_mixer(17)
                self.mixer.voice[16].play(source, loop=True)
                source = self.mixer
        else:
            self.mixer = mixer if mixer is not None else self._make_mixer(16)
            source = self.mixer
        self.chorus = self.reverb = None
        self.output = source
        if effects:
            import audioeffects
            if chorus:
                self.chorus = audioeffects.create("Chorus", source, rate, mix=0.0)
                source = self.chorus.output
            self.reverb = audioeffects.create("Reverb", source, rate, character="hall", mix=0.0)
            self.output = self.reverb.output
        self.reverb_level = self.chorus_level = 0
        self.set_reverb(DEFAULT_REVERB)
        self.set_chorus(DEFAULT_CHORUS)
        self.master_level = DEFAULT_MASTER
        self.on_master = None  # the output's volume setter (0-100): a UI's MASTER slider
        self._kits = kits  # the bank's drum programs, when known; else asked of the bank
        self._status = 0
        self._need = 0
        self._data = []
        self._sysex = None
        self._skip = 0
        self.error = None  # why the last instrument couldn't be made (no bank)
        self.loaded = None  # the open bank, once prepare() has opened it
        self.cache_bytes = None  # the bound prepare() gave its samples; None for soundfont's own
        self._unbounded = None  # that bank, when its soundfont keeps every sample
        self._kept = 0  # how many samples it held when last looked at
        self.identities = 0  # Identity Requests answered

    @property
    def voice_count(self):
        """How many engine voices the parts share, settable while it plays
        (``soundfont.Voices.voice_count``); None without a shared pool."""
        pool = self.voice_pool
        return None if pool is None else pool.voice_count

    @voice_count.setter
    def voice_count(self, count):
        if self.voice_pool is None:
            raise ValueError("no shared voices to count")
        self.voice_pool.voice_count = count

    def _make_mixer(self, count):
        import audiomixer
        size = MIXER_BUFFER if count > 16 else PARTS_MIXER_BUFFER
        return audiomixer.Mixer(voice_count=count, buffer_size=size, channel_count=self.channels,
                                bits_per_sample=16, samples_signed=True, sample_rate=self.rate)

    # -- the bank -------------------------------------------------------------------

    def prepare(self):
        """Open the bank now, so no part has to: the open bank, or None
        with the reason in ``error``. Nothing to do with instruments of your
        own (``create=``)."""
        if self._create is not _default_create:
            return None
        if self.loaded is None:
            try:
                import audioinstruments._rack.soundfont  # dotted: a lazy package's from-import fails on MicroPython

                self.loaded = audioinstruments._rack.soundfont.open_bank(self.bank)
            except Exception as e:
                self.error = e
                return None
            self.cache_bytes = board_cache_bytes()
            if not hasattr(self.loaded, "cache_bytes"):
                self._unbounded = self.loaded
            elif self.cache_bytes is not None:
                self.loaded.cache_bytes = self.cache_bytes
            self.kit_programs()
        return self.loaded

    def _trim(self):
        """Let every sample go once an unbounded bank holds more than
        ``FALLBACK_CACHE_BYTES``; voices playing one keep theirs."""
        samples = self._unbounded.samples
        if len(samples) == self._kept:
            return
        held = 0
        for pcm in samples.values():
            held += len(pcm) * 2
        limit = FALLBACK_CACHE_BYTES
        if self.cache_bytes is not None and self.cache_bytes < limit:
            limit = self.cache_bytes
        if held > limit:
            samples.clear()
        self._kept = len(samples)

    # -- the parts' instruments -----------------------------------------------------

    def wanted(self, part):
        """The instrument the part's bank and program ask for: one of
        ``INSTRUMENTS`` or ``DRUM_MACHINES`` on banks 80 and 81 (None for a
        program past the end), else the SoundFont's."""
        if self.instruments and not part.gm:
            if part.bank_msb == BANK_INSTRUMENTS:
                table = INSTRUMENTS
            elif part.bank_msb == BANK_DRUM_MACHINES:
                table = DRUM_MACHINES
            else:
                return None
            return table[part.program] if part.program < len(table) else None
        return DRUMS if part.drums else MELODIC

    def instrument(self, part):
        """The part's instrument, made on its first use or when its bank and
        program ask for another; None without one (no SoundFont, or a
        program no instrument has)."""
        name = self.wanted(part)
        inst = part.inst
        if inst is not None and part.inst_name == name:
            return inst
        if inst is not None:
            self._drop(part)
        if name is None:
            return None
        try:
            if self.voice_pool is not None and name in (MELODIC, DRUMS):
                inst = self._create(name, self.rate, self.channels, self.bank, self.voice_pool)
            else:
                inst = self._create(name, self.rate, self.channels, self.bank)
        except Exception as e:
            self.error = e
            return None
        part.inst = inst
        part.inst_name = name
        self._sends()
        if self.mixer is not None and not (self.voice_pool is not None and name in (MELODIC, DRUMS)):
            voice = self.mixer.voice[part.channel]
            voice.play(inst.output, loop=True)
        self._apply(part)
        return inst

    def _drop(self, part):
        """Let the part's instrument go: its notes released, its mixer voice
        stopped, its voices given back."""
        inst = part.inst
        part.inst = None
        part.inst_name = None
        self._sends()
        if inst is None:
            return
        try:
            inst.all_notes_off()
        except Exception:
            pass
        if self.mixer is not None and part.channel < 16:
            try:
                self.mixer.voice[part.channel].stop()
            except Exception:
                pass
        inst.deinit()

    def _own_voice(self, part):
        """True when the part's instrument plays through its own mixer voice
        rather than the shared pool."""
        return part.inst is not None and self.mixer is not None and not (
            self.voice_pool is not None and part.inst_name in (MELODIC, DRUMS))

    def _apply(self, part):
        """Send a part's remembered state to its instrument."""
        inst = part.inst
        if inst is None:
            return
        if part.inst_name == MELODIC:
            inst.control_change(CC_BANK_MSB, part.bank_msb)
            inst.control_change(CC_BANK_LSB, part.bank_lsb)
        self._select(part)
        self._tune(part)
        inst.pitch_bend(self._wheel(part))  # centred too: a reset may not centre it

    def _tune(self, part):
        """Give a SoundFont part its bend range and tuning."""
        inst = part.inst
        if inst is not None and part.inst_name in (MELODIC, DRUMS) and hasattr(inst, "bend_range"):
            inst.bend_range = part.bend_range
            inst.tuning = part.tuning

    def _wheel(self, part):
        """The bend to send the part's instrument: as it came, unless the
        instrument's range is fixed, when it is scaled into that range."""
        inst = part.inst
        if part.inst_name not in (MELODIC, DRUMS) or hasattr(inst, "bend_range"):
            return part.bend
        v = BEND_CENTER + int((part.bend - BEND_CENTER) * part.bend_range / NATIVE_BEND_RANGE)
        return 0 if v < 0 else 16383 if v > 16383 else v

    def _select(self, part):
        """Pick the part's program on its instrument, then its volume and
        pan: a program change resets the patch's macros."""
        inst = part.inst
        if part.inst_name in (MELODIC, DRUMS):
            index = self.kit_index(part.program) if part.inst_name == DRUMS else part.program
            if index in inst.patches:
                inst.program_change(index)
            inst.control_change(CC_VOLUME, part.volume)
            inst.set_macro(PAN_MACRO, part.pan)
        else:
            if part.bank_lsb in inst.patches:
                inst.program_change(part.bank_lsb)
            self._level(part)

    def _level(self, part):
        """An instrument part's volume and pan, on its mixer voice."""
        if not self._own_voice(part):
            return
        voice = self.mixer.voice[part.channel]
        v = part.volume / 127.0
        voice.level = PART_LEVEL * v * v
        try:
            voice.panning = max(-1.0, min(1.0, (part.pan - 64) / 63.0))
        except AttributeError:  # an audiomixer without panning: centred
            pass

    def kit_index(self, program):
        """The drum instrument's patch for GM drum program *program*: the
        kit at that number, else the nearest lower one (the standard kit at
        worst), as GS modules fall back."""
        kits = self.kit_programs()
        best = 0
        for i, p in enumerate(kits):
            if p <= program:
                best = i
        return best

    def kit_programs(self):
        """The GM drum program numbers the bank has kits for, in order."""
        if self._kits is None:
            try:
                import audioinstruments._rack.soundfont  # dotted: a lazy package's from-import fails on MicroPython

                soundfont = audioinstruments._rack.soundfont
                self._kits = [p for p, _ in soundfont.open_bank(self.bank).programs(PERCUSSION_BANK)]
            except Exception:
                self._kits = [p for p, _ in GM_KITS]
        return self._kits

    def name(self, channel):
        """What channel *channel* plays: the GM program's name, the kit's,
        or the instrument's with its patch."""
        part = self.parts[channel]
        name = self.wanted(part)
        if name is None:
            return "(none)"
        if name not in (MELODIC, DRUMS):
            inst = part.inst
            if inst is not None and part.inst_name == name and part.bank_lsb in inst.patches:
                return "%s: %s" % (name, inst.patches[part.bank_lsb][0])
            return name
        if part.drums:
            inst = part.inst
            if inst is not None and part.inst_name == DRUMS:
                return inst.patches[self.kit_index(part.program)][0]
            kits = self.kit_programs()
            best = GM_KITS[0][1]
            for p, n in GM_KITS:
                if p <= part.program and p in kits:
                    best = n
            return best + " Kit"
        return GM_NAMES[part.program]

    # -- MIDI in --------------------------------------------------------------------

    def feed(self, data, nbytes=None):
        """Parse raw MIDI 1.0 bytes (the first ``nbytes`` when given):
        running status, realtime bytes inside a message, and SysEx."""
        n = len(data) if nbytes is None else nbytes
        for i in range(n):
            b = data[i]
            if b >= 0xF8:  # realtime: clock, start, stop, sensing
                continue
            if self._sysex is not None:
                if b == 0xF7:
                    self.sysex(bytes(self._sysex))
                    self._sysex = None
                elif b < 0x80:
                    self._sysex.append(b)
                else:  # a status byte ends an unterminated SysEx
                    self._sysex = None
                    self._status_byte(b)
                continue
            if b >= 0x80:
                self._status_byte(b)
            elif self._skip:
                self._skip -= 1
            elif self._status:
                self._data.append(b)
                if len(self._data) == self._need:
                    d = self._data
                    self._data = []
                    self.message(self._status, d[0], d[1] if self._need == 2 else 0)

    def _status_byte(self, b):
        if b == 0xF0:
            self._sysex = bytearray()
            self._status = 0
        elif b >= 0xF0:  # system common cancels running status
            self._status = 0
            self._skip = {0xF1: 1, 0xF2: 2, 0xF3: 1}.get(b, 0)
        else:
            self._status = b
            self._data = []
            self._need = 1 if (b & 0xF0) in (0xC0, 0xD0) else 2

    def message(self, status, d1, d2=0):
        """One channel message: ``status`` with its channel, and its data."""
        kind = status & 0xF0
        part = self.parts[status & 0x0F]
        if kind == 0x90 and d2:
            self.note_on(part, d1, d2)
        elif kind in (0x80, 0x90):
            self.note_off(part, d1)
        elif kind == 0xB0:
            self.control(part, d1, d2)
        elif kind == 0xC0:
            self.program(part, d1)
        elif kind == 0xD0:
            if part.inst is not None and hasattr(part.inst, "channel_pressure"):
                part.inst.channel_pressure(d1)
        elif kind == 0xE0:
            self.bend(part, d1 | (d2 << 7))

    def sysex(self, data):
        """``data`` is the bytes between F0 and F7. GM System On (and GM2
        On, GS Reset and XG System On, which ask the same) reset every part;
        the Identity Request is answered."""
        if len(data) >= 4 and data[0] == 0x7E and data[2] == 0x06 and data[3] == 0x01:
            if data[1] in (0x7F, DEVICE_ID):
                self.identities += 1
                if self.send is not None:
                    self.send(identity_reply())
        elif len(data) >= 4 and data[0] == 0x7E and data[2] == 0x09 and data[3] in (0x01, 0x03):
            self.gm_reset()
        elif len(data) >= 9 and data[0] == 0x41 and data[2:4] == b"\x42\x12" and data[4:7] == b"\x40\x00\x7f":
            self.gm_reset()
        elif len(data) >= 7 and data[0] == 0x43 and data[2] == 0x4C and data[3:6] == b"\x00\x00\x7e":
            self.gm_reset()

    # -- the events ----------------------------------------------------------------

    def note_on(self, part, note, velocity):
        inst = self.instrument(part)
        if inst is None:
            return
        inst.note_on(note, velocity)
        if self._unbounded is not None:
            self._trim()
        part.held.add(note)
        part.sustained.discard(note)
        part.notes += 1
        part.last = (note, velocity)
        key = (part.bank_msb, part.bank_lsb, part.program)
        tally = part.played.get(key)
        if tally is None:
            part.played[key] = [1, self.name(part.channel)]
        else:
            tally[0] += 1

    def note_off(self, part, note):
        part.held.discard(note)
        if part.sustain:
            part.sustained.add(note)
        elif part.inst is not None:
            part.inst.note_off(note)

    def control(self, part, cc, value):
        inst = part.inst
        if cc in (CC_BANK_MSB, CC_BANK_LSB):
            if cc == CC_BANK_MSB:
                part.bank_msb = value
            else:
                part.bank_lsb = value
            # A SoundFont part hears its bank at once, as it always did;
            # a change of instrument waits for the Program Change.
            if inst is not None and part.inst_name == MELODIC and part.gm:
                inst.control_change(cc, value)
        elif cc == CC_VOLUME:
            part.volume = value
            if inst is not None:
                if part.inst_name in (MELODIC, DRUMS):
                    inst.control_change(CC_VOLUME, value)
                else:
                    self._level(part)
        elif cc == CC_PAN:
            part.pan = value
            if inst is not None:
                if part.inst_name in (MELODIC, DRUMS):
                    inst.set_macro(PAN_MACRO, value)
                else:
                    self._level(part)
        elif cc == CC_SUSTAIN:
            on = value >= 64
            part.sustain = on
            if not on:
                self._release_sustained(part)
        elif cc == CC_REVERB:
            part.reverb = value
            self._sends()
        elif cc == CC_CHORUS:
            part.chorus = value
            self._sends()
        elif cc in (CC_RPN_MSB, CC_RPN_LSB):
            msb, lsb = part.rpn if part.rpn is not None else RPN_NULL
            part.rpn = (value, lsb) if cc == CC_RPN_MSB else (msb, value)
        elif cc in (CC_NRPN_MSB, CC_NRPN_LSB):
            part.rpn = None  # no NRPNs here: Data Entry goes nowhere
        elif cc in (CC_DATA_MSB, CC_DATA_LSB, CC_DATA_INC, CC_DATA_DEC):
            self.data_entry(part, cc, value)
        elif cc == CC_ALL_SOUND_OFF:
            self.all_sound_off(part)
        elif cc == CC_RESET_CONTROLLERS:
            self.reset_controllers(part)
        elif cc == CC_ALL_NOTES_OFF:
            self.all_notes_off(part)
        elif MACRO_CC <= cc < MACRO_CC + MACRO_CCS:
            if inst is not None and cc - MACRO_CC < len(inst.macro_labels):
                inst.set_macro(cc - MACRO_CC, value)
        elif inst is not None:
            inst.control_change(cc, value)

    def program(self, part, number):
        part.program = number
        if part.inst is None or part.inst_name != self.wanted(part):
            self.instrument(part)  # made now, with the program applied
        else:
            self._select(part)

    def bend(self, part, value):
        part.bend = value
        if part.inst is not None:
            part.inst.pitch_bend(self._wheel(part))

    def data_entry(self, part, cc, value):
        """Data Entry (CC6, CC38), Increment (CC96) or Decrement (CC97) for
        the part's selected RPN: an MSB alone zeroes the LSB."""
        rpn = part.rpn
        if rpn not in part.params:
            return
        v = part.params[rpn]
        if cc == CC_DATA_MSB:
            v = value << 7
        elif cc == CC_DATA_LSB:
            v = (v & 0x3F80) | value
        else:
            step = 1 if rpn == RPN_FINE_TUNING else 128
            v += step if cc == CC_DATA_INC else -step
            v = 0 if v < 0 else 16383 if v > 16383 else v
        if rpn == RPN_BEND_RANGE and v > MAX_BEND_RANGE << 7:
            v = MAX_BEND_RANGE << 7
        part.params[rpn] = v
        if part.inst is not None:
            self._tune(part)
            if rpn == RPN_BEND_RANGE and part.bend != BEND_CENTER:
                part.inst.pitch_bend(self._wheel(part))

    def _release_sustained(self, part):
        notes = part.sustained
        part.sustained = set()
        if part.inst is not None:
            for n in notes:
                part.inst.note_off(n)

    def all_notes_off(self, part):
        """CC123: release every note the part holds."""
        part.held = set()
        part.sustained = set()
        if part.inst is not None:
            part.inst.all_notes_off()

    def all_sound_off(self, part):
        """CC120: stop the part's sound at once, keeping its program."""
        part.held = set()
        part.sustained = set()
        inst = part.inst
        if inst is not None:
            inst.reset()  # stops every voice, and forgets the program and bank
            self._apply(part)

    def reset_controllers(self, part):
        """CC121, as RP-015 has it: pedal up, bend centred, Expression 127,
        the mod wheel and channel pressure 0, and the RPN null; program,
        volume, pan and the RPN values stay."""
        part.sustain = False
        self._release_sustained(part)
        part.bend = BEND_CENTER
        part.rpn = RPN_NULL
        inst = part.inst
        if inst is not None:
            inst.pitch_bend(BEND_CENTER)
            if part.inst_name in (MELODIC, DRUMS):
                inst.control_change(CC_EXPRESSION, 127)
                inst.control_change(CC_MOD_WHEEL, 0)
                if hasattr(inst, "channel_pressure"):
                    inst.channel_pressure(0)

    def panic(self):
        """Every part silent now; programs and levels stay."""
        for part in self.parts:
            self.all_sound_off(part)
            self.reset_controllers(part)

    def gm_reset(self):
        """GM System On: every part back to bank 0, program 0, volume 100,
        pan centre, pedal up, bend centred, two semitones of bend and no
        tuning; reverb 40 and chorus 0. A part
        playing one of audioinstruments' instruments lets it go, and plays
        the SoundFont again from its next note."""
        for part in self.parts:
            part.reset()
            if part.inst is not None:
                if part.inst_name != self.wanted(part):
                    self._drop(part)
                else:
                    part.inst.reset()
                    self._apply(part)
        self._sends()

    # -- a UI's channel sliders -------------------------------------------------------

    def set_volume(self, part, value):
        """A UI's volume slider for one channel: the path CC7 takes, so
        ``part.volume`` is what the UI shows."""
        self.control(part, CC_VOLUME, value)

    def set_pan(self, part, value):
        """A UI's pan slider for one channel: the path CC10 takes."""
        self.control(part, CC_PAN, value)

    # -- the master -----------------------------------------------------------------

    def set_master(self, level):
        """The MASTER level, 0-100: the output's volume, not any part's.
        ``on_master`` (the AudioOut's ``set_volume``) gets it, so a part's
        CC7 and the master never fight over one control."""
        level = max(0, min(100, int(level)))
        self.master_level = level
        if self.on_master is not None:
            self.on_master(level)

    def _sends(self):
        """The master reverb and chorus: the mean of the parts' sends,
        over the parts in use, or every part before any is."""
        parts = [p for p in self.parts if p.inst is not None] or self.parts
        self.set_reverb((sum(p.reverb for p in parts) + len(parts) // 2) // len(parts))
        self.set_chorus((sum(p.chorus for p in parts) + len(parts) // 2) // len(parts))

    def set_reverb(self, level):
        self.reverb_level = level
        _set_mix(self.reverb, level)

    def set_chorus(self, level):
        self.chorus_level = level
        _set_mix(self.chorus, level)

    def fill(self):
        """Let the parts' instruments move their notes and knobs on: call it
        before each pull of ``output``, or every 10 to 20 ms where something
        else pulls it. An audioinstruments instrument on MicroPython takes
        notes and knobs into the sound in ``fill()``; on CPython, and for a
        SoundFont part, it does nothing."""
        for part in self.parts:
            inst = part.inst
            if inst is not None and part.inst_name not in (MELODIC, DRUMS):
                fill = getattr(inst, "fill", None)
                if fill is not None:
                    fill()

    def close(self):
        for part in self.parts:
            self._drop(part)
        if self.mixer is not None and self.voice_pool is not None and self.instruments:
            try:
                self.mixer.voice[16].stop()
            except Exception:
                pass
        for fx in (self.reverb, self.chorus):
            if fx is not None:
                fx.deinit()
        self.reverb = self.chorus = None
        if self.voice_pool is not None:
            self.voice_pool.deinit()
            self.voice_pool = None


def _mix_index(fx):
    for i, label in enumerate(fx.MACRO_LABELS):
        if label.lower().startswith("mix"):
            return i
    raise ValueError("%s has no Mix macro" % fx.NAME)


def _set_mix(fx, level):
    if fx is not None:
        fx.set_macro(_mix_index(fx), level)
