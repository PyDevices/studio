"""Write patched/synthio.py: audiodsp's synthio.py with its block clock per
thread instead of per process (the stress test's --synthio-tls experiment).

    python3 desktop/patch_synthio.py <installed synthio.py> <out dir>

synthio.py keeps one module-level _BLOCK_TICK / _BLOCK_SECONDS that every
Synthesizer advances each block and every LFO reads; LFO() also zeroes
_BLOCK_SECONDS. Synths rendering on several threads then step each other's
LFOs. This is the smallest change that shows it: a threading.local clock.
"""

import os
import re
import sys

src = open(sys.argv[1]).read()
head = "_BLOCK_TICK = 0\n_BLOCK_SECONDS = 0.0\n"
assert head in src, "synthio.py changed: no module-level block clock"
src = src.replace(head, "import threading as _threading\n\n\nclass _Clock(_threading.local):\n"
                  "    tick = 0\n    seconds = 0.0\n\n\n_T = _Clock()\n", 1)
src = re.sub(r"\n *global _BLOCK_TICK, _BLOCK_SECONDS\n", "\n", src)
src = re.sub(r"\n *global _BLOCK_SECONDS\n", "\n", src)
src = src.replace("_BLOCK_TICK", "_T.tick").replace("_BLOCK_SECONDS", "_T.seconds")
os.makedirs(sys.argv[2], exist_ok=True)
open(os.path.join(sys.argv[2], "synthio.py"), "w").write(src)
