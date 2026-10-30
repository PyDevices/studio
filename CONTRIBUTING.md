# Contributing

## Run the tests

From a Python that has audiocomponents installed (so the CPython
real-instrument checks run):

```bash
python -m unittest discover -s tests
cd apps/ostinato && python -m unittest discover -s tests
```

MicroPython and CircuitPython runs get `MICROPYPATH` from the test wrapper
and need `-X heapsize=64M`: five instruments outgrow the 8 MB default.
Vamp's tests need hmilink, from `$HMILINK_PATH` or a pydevices checkout
beside this one. CI sets `VAMP_REQUIRE=1` so a missing piece fails instead of
skipping.

[How it is checked](docs/how-it-works/how-its-checked.md) says what each
check covers, and the two gates that need REAPER on Windows.

## Add a check

Every check in `tests/engine_contract.py` has a plant in `PLANTS` that must
make it fail. Add one with each new check.

The contract also runs on MicroPython and CircuitPython, so in it: no
`os.path`, no f-strings with `=`, and no `memoryview.cast` (use
`array.array("h", bytes(buf))`). CircuitPython's audio buffers count
samples, not bytes.

## Where code goes

Applications live in `apps/` (Ostinato) or `lib/` (soundmodule, Vamp). Code
two of them share goes in `lib/` (`songlink`). Ostinato's `tools/gen_boot.py`
lists what its page loads from there, so run it after adding a shared file.

## The gates

`tools/gate.py` drives Windows from WSL: REAPER, a loopback MIDI port and a
CPython venv on Windows. A Windows process outlives a timeout on the WSL
side, so the gate ends them by command line. REAPER sends CC0 = 0 to every
part when it stops, so what a part played is in `Part.played`, not in its
bank at the end. The gate's docstring lists what it needs on Windows.

## Ostinato's checks

From `apps/ostinato`, run them all in one app session, about four minutes, against a real Chrome:

```bash
.venv/bin/python tools/check_all.py --cdp http://127.0.0.1:9222
```

`tools/win_chrome.sh` starts the Chrome the tools drive. `--long` adds the
whole demo song's export, and `--only master,demo` picks checks.
