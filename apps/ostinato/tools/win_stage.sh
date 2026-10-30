#!/usr/bin/env bash
# Copy what the desktop app runs from to a Windows folder (python.exe can't
# run from WSL's filesystem). From WSL:
#   tools/win_stage.sh [/mnt/c/temp/ostinato-desktop]
# Needs vendor/lib (scripts/fetch_midi.py): midiports, with its Windows port,
# and adafruit_midi.
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
DEST=${1:-/mnt/c/temp/ostinato-desktop}
[ -f "$ROOT/vendor/lib/midiports/windows.py" ] || { echo "run scripts/fetch_midi.py first" >&2; exit 1; }
mkdir -p "$DEST"
for d in app desktop ostinato assets songs; do
  rsync -a --delete --exclude __pycache__ "$ROOT/$d/" "$DEST/$d/"
done
mkdir -p "$DEST/vendor"
rsync -a --delete --exclude __pycache__ "$ROOT/vendor/lib/" "$DEST/vendor/lib/"
# songlink, which Ostinato shares with Vamp, lives in studio's lib/.
mkdir -p "$DEST/lib"
rsync -a --delete --exclude __pycache__ "$ROOT/../../lib/songlink/" "$DEST/lib/songlink/"
echo "staged in $DEST"
