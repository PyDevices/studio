#!/usr/bin/env bash
# Make sure the Windows MIDI Services loopback "Ostinato Loopback" exists.
# Created without --save-to-config (that needs an elevated shell), so it lasts
# until the MIDI service restarts; this recreates it whenever it's missing.
set -euo pipefail
NAME="Ostinato Loopback"
TOOLS="/mnt/c/Program Files/Windows MIDI Services/Tools"
cd /mnt/c
if "$TOOLS/midi1enum.exe" 2>/dev/null | grep -q "$NAME"; then
  echo "$NAME: present"
else
  "$TOOLS/midi.exe" basic-loopback create --name "$NAME" >/dev/null
  "$TOOLS/midi1enum.exe" | grep -q "$NAME" && echo "$NAME: created"
fi
