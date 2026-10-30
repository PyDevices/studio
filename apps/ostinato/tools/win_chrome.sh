#!/usr/bin/env bash
# Start a separate Windows Chrome that tools/midi_play.py --cdp can drive.
# Its own profile (C:\temp\ostinato-chrome), so the everyday browser is untouched.
# Real audio device, real Web MIDI (the loopback ports show up here). It plays
# out of the speakers, so you can hear the work in progress.
#   tools/win_chrome.sh            start it (returns once the debug port answers)
#   tools/win_chrome.sh stop       close it
set -euo pipefail
PORT=${PORT:-9222}
CHROME="/mnt/c/Program Files/Google/Chrome/Application/chrome.exe"
if [ "${1:-}" = stop ]; then
  powershell.exe -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | Where-Object { \$_.CommandLine -like '*ostinato-chrome*' } | ForEach-Object { Stop-Process -Id \$_.ProcessId -Force -ErrorAction SilentlyContinue }"
  exit 0
fi
(cd /mnt/c && "$CHROME" --remote-debugging-port=$PORT --user-data-dir='C:\temp\ostinato-chrome' \
  --autoplay-policy=no-user-gesture-required --no-first-run --no-default-browser-check \
  --window-size=1920,1080 about:blank >/dev/null 2>&1 &)
for _ in $(seq 60); do
  curl -s "http://127.0.0.1:$PORT/json/version" >/dev/null && { echo "chrome on :$PORT"; exit 0; }
  sleep 0.5
done
echo "chrome did not open its debug port" >&2; exit 1
