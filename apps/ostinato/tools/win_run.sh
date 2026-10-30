#!/usr/bin/env bash
# Start the desktop app on Windows from WSL: stage the checkout on a Windows
# path, then run python.exe -m ostinato there.
#   tools/win_run.sh [processes|threads|threads-ft] [ostinato args...]
# threads-ft is the free-threaded build with the GIL off (-X gil=0).
# processes and threads use the README's venv; threads-ft wants a
# free-threaded 3.15t gate venv. Override
# with PY315=... / PY315T=... (Windows paths). STAGE moves the staging folder.
# It runs in the foreground until the window closes; TIMEOUT (seconds) caps it.
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
KIND=${1:-processes}; [ $# -gt 0 ] && shift
WSTAGE=${STAGE:-'C:\temp\ostinato-desktop'}
STAGE_WSL=$(wslpath -u "$WSTAGE")
PY315=${PY315:-'C:\Users\bradb\ostinato-venv\Scripts\python.exe'}
PY315T=${PY315T:-'C:\temp\py315\gate315t\Scripts\python.exe'}
case $KIND in
  processes) PY=$PY315; FLAGS=""; ENG=processes ;;
  threads) PY=$PY315; FLAGS=""; ENG=threads ;;
  threads-ft) PY=$PY315T; FLAGS="-X gil=0"; ENG=threads ;;
  processes-ft) PY=$PY315T; FLAGS="-X gil=0"; ENG=processes ;;
  *) echo "runner: processes, threads or threads-ft" >&2; exit 2 ;;
esac
"$ROOT/tools/win_stage.sh" "$STAGE_WSL" >/dev/null || exit 1
cd "$STAGE_WSL" || exit 1
ARGS=""
for a in "$@"; do ARGS="$ARGS \"$a\""; done      # each argument quoted for cmd.exe
# A batch file carries the command line: WSL would escape the quotes on the way
# to cmd.exe. It starts in the staging folder, so -m finds the package there.
printf '@echo off\r\nset PYTHONUNBUFFERED=1\r\ncd /d %s\r\n%s %s -m ostinato --engines %s%s\r\n' "$WSTAGE" "$PY" "$FLAGS" "$ENG" "$ARGS" > "$STAGE_WSL/run.cmd"
exec timeout "${TIMEOUT:-86400}" cmd.exe /c "$WSTAGE\\run.cmd"
