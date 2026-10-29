# AGENTS.md

- Tests: `python -m unittest discover -s tests` from a Python with audiocomponents installed (its `.venv`), so the CPython real-instrument checks run. MicroPython and CircuitPython get `MICROPYPATH=.frozen:<audiocomponents>/lib` from the wrapper, and need `-X heapsize=64M`: five instruments outgrow the 8 MB default.
- Every check in `tests/engine_contract.py` has a plant in `PLANTS` that must make it fail. Add one with each new check.
- The contract runs on MicroPython and CircuitPython too: no `os.path`, no f-strings with `=`, and `memoryview.cast` isn't there (use `array.array("h", bytes(buf))`). CircuitPython's audio buffers count samples, not bytes.
- `tools/gate.py` drives Windows from WSL: REAPER, a loopback MIDI port and a CPython venv on Windows. A Windows process outlives a timeout on the WSL side; the gate ends them by command line.
- REAPER sends CC0 = 0 to every part when it stops. What a part played is in `Part.played`, not in its bank at the end.
