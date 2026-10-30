"""Vamp: the song and the sound on one MicroPython, the screens elsewhere.

On the ESP32-P4 DEV-KIT, with the files from ``tools/vamp_files.py`` on it::

    import vamp; vamp.run()

On desktop MicroPython (unix or Windows), with studio's ``lib/`` and hmilink on
MICROPYPATH::

    micropython -X heapsize=96M -m vamp.main --bank GeneralUser-GS.sf2 [--song FILE]
        [--port 9100] [--midi NAME] [--lookahead-ms 750] [--seconds N]

Then the window joins it (``python -m ostinato --connect BOX:9100``) and a
browser opens ``http://BOX:9100/``. On the board, the bank is read from the
SD card, the network is the Ethernet port, and a keyboard on the USB host
port plays the selected track.
"""

import gc
import sys
import time

try:
    _BOARD = sys.platform == "esp32"
except AttributeError:
    _BOARD = False

DEFAULTS = {
    "bank": "/sd/soundfonts/GeneralUser-GS.sf2" if _BOARD else "GeneralUser-GS.sf2",
    "song": "/vamp_song.json" if _BOARD else None,   # the box plays SoundFont tracks
    "port": 9100,
    "midi": None,
    "lookahead_ms": 750,
    "seconds": 0,
    "gc_s": 2.0,
    "page": None,
    "arena_mb": 16 if _BOARD else 0,
    "play": False,
    "plant": None,
    "ip": None,
    "trace_ms": 0,                  # log any pass of the loop slower than this
}

LINK_LOCAL = "169.254.80.80"


def _args(argv):
    opts = dict(DEFAULTS)
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            key = a[2:].replace("-", "_")
            if key == "play":
                opts["play"] = True
                i += 1
                continue
            value = argv[i + 1]
            if key in ("port", "lookahead_ms", "arena_mb", "trace_ms"):
                value = int(value)
            elif key in ("seconds", "gc_s"):
                value = float(value)
            opts[key] = value
            i += 2
        else:
            i += 1
    return opts


def _log(*args):
    print(*args)


def _network(ip):
    """The DEV-KIT's Ethernet port, up. An address from DHCP when the PC shares
    its connection over the cable; without that, a link-local one (the PC
    takes a 169.254 address on a cable with no DHCP, so the two still meet)."""
    import board_peripherals as bp

    lan = bp.ethernet()
    lan.active(True)
    if ip:
        _static(lan, ip)
        return lan
    t = time.ticks_ms()
    while lan.ifconfig()[0] in ("0.0.0.0", ""):
        if time.ticks_diff(time.ticks_ms(), t) > 8000:
            _static(lan, LINK_LOCAL)
            break
        time.sleep_ms(100)
    return lan


def _static(lan, ip):
    """A fixed address, with DHCP off. (ESP-IDF refuses ifconfig's tuple when
    the gateway is 0.0.0.0, so this takes ipconfig.)"""
    lan.ipconfig(dhcp4=False)
    lan.ipconfig(addr4=(ip, "255.255.0.0"))


def _mount_sd():
    import os

    try:
        os.listdir("/sd")
        return
    except OSError:
        pass
    import board_peripherals as bp

    os.mount(bp.sdcard(), "/sd")


def _output():
    """A sample player at the engine's rate, and the pump under it (or None)."""
    from audiodev import AudioFormat

    if _BOARD:
        import board_peripherals as bp

        out = bp.audio_out(AudioFormat(48000, 2, 16), latency="low")
    else:
        from audiodev import auto

        out = auto.audio_out(AudioFormat(48000, 2, 16), latency="low")
    try:
        from audiodev import pump

        owner = pump.owner() if pump.available() else None
    except ImportError:
        owner = None
    return out, owner


def _midi(name):
    """A keyboard: the board's USB host port, or a desktop port by name."""
    try:
        if _BOARD:
            import usb.core
            from midiports import usb_host

            usb.core.start(full_speed=True)
            return usb_host.all_ports()[0]
        if not name:
            return None
        if sys.platform == "win32":
            from midiports import windows as host
        else:
            from midiports import linux as host
        return host.ports(name)[0]
    except Exception as e:  # noqa: BLE001  (no keyboard is not a reason to stop)
        _log("box: no keyboard:", e)
        return None


class _Parser:
    """Bytes from a port into (status, d1, d2), running status and all."""

    def __init__(self):
        self.status = 0
        self.data = []

    def feed(self, buf, n, out):
        for i in range(n):
            b = buf[i]
            if b >= 0xF8:
                continue                  # clock and realtime
            if b >= 0x80:
                self.status = b if b < 0xF0 else 0
                self.data = []
                continue
            if not self.status:
                continue
            self.data.append(b)
            kind = self.status & 0xF0
            need = 1 if kind in (0xC0, 0xD0) else 2
            if len(self.data) == need:
                out.append((self.status, self.data[0], self.data[1] if need == 2 else 0))
                self.data = []


def main(argv=None):
    opts = _args(argv if argv is not None else sys.argv[1:])
    if _BOARD:
        import _thread

        _thread.stack_size(16 * 1024)
        lan = _network(opts["ip"])
        _log("box: Ethernet", lan.ifconfig()[0])
        _mount_sd()

    from vamp import engine as be
    from vamp.device import BoxDevice

    project = None
    if opts["song"]:
        import json

        with open(opts["song"]) as f:
            project = json.load(f)

    out, pump = _output()
    import audiopump

    t = time.ticks_ms()
    engine = be.BoxEngine(opts["bank"], out, audiopump.now, park=audiopump.park, unpark=audiopump.unpark,
                          lookahead=opts["lookahead_ms"] * be.SR // 1000,
                          cache_bytes=opts["arena_mb"] * 1048576 if opts["arena_mb"] else None)
    _log("box: bank open in %d ms" % time.ticks_diff(time.ticks_ms(), t))
    page = opts["page"]
    if page is None:
        import songlink

        page = songlink.page()
    dev = BoxDevice(engine, project, port=opts["port"], page=page, pump=pump, log=_log, plant=opts["plant"])
    _log("box: the song on port %d, %d tracks, %d KB heap free" % (opts["port"], len(dev.model.doc["tracks"]),
                                                                  gc.mem_free() // 1024))
    midi_in = _midi(opts["midi"])
    parser = _Parser()
    buf = bytearray(64)
    msgs = []
    serviced = hasattr(out, "service")
    _log("box: the pump %s" % ("renders the sound" if getattr(out, "pumped", False) else
                               "isn't there: Python renders (%s)" % getattr(out, "pump_refused", None)))
    if opts["play"]:
        dev.transport(True, 0.0, None)
    started = time.ticks_ms()
    last_gc = started
    trace = opts["trace_ms"]
    need = (be.SR * 6) // 10                 # a collection's worst, with room: 600 ms
    try:
        while True:
            t0 = time.ticks_ms()
            engine.sequence()
            t1 = time.ticks_ms()
            dev.poll()
            t2 = time.ticks_ms()
            if midi_in is not None:
                n = midi_in.readinto(buf)
                if n:
                    parser.feed(buf, n, msgs)
                    for m in msgs:
                        dev.midi(*m)
                    msgs.clear()
            if serviced:
                out.service()
            now = time.ticks_ms()
            if time.ticks_diff(now, last_gc) > opts["gc_s"] * 1000:
                if engine.collect(need) is not None:
                    last_gc = now
            if trace and time.ticks_diff(now, t0) > trace:
                _log("box: a slow pass, %d ms: sequence %d, panels %d, the rest %d (gc ran %d)" % (
                    time.ticks_diff(now, t0), time.ticks_diff(t1, t0), time.ticks_diff(t2, t1),
                    time.ticks_diff(now, t2), engine.stats["gc"]))
            if opts["seconds"] and time.ticks_diff(now, started) > opts["seconds"] * 1000:
                break
            time.sleep_ms(1)
    except KeyboardInterrupt:
        pass
    finally:
        fig = dev.telemetry_now()
        _log("box: figures", fig["mixer"]["box"] if fig else None)
        _log("box: %d frames in %d ms" % (engine.clock(), time.ticks_diff(time.ticks_ms(), started)))
        dev.close()
        out.stop()
    return dev


if __name__ == "__main__":
    main()
