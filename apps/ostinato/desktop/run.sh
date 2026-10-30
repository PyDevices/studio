#!/usr/bin/env bash
# One spike run from WSL: stage, start the app under python.exe, play keys
# into the loopback, then check the tap.
#   desktop/run.sh NAME processes|threads|threads-ft SECONDS [app args...]
# threads-ft is the free-threaded build with the GIL off (-X gil=0).
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
NAME=$1; KIND=$2; SECS=$3; shift 3
STAGE=/mnt/c/temp/ostinato-desktop
WSTAGE='C:\temp\ostinato-desktop'
"$ROOT/desktop/stage.sh" >/dev/null
case $KIND in
  processes) PY='C:\temp\py315\gate315\Scripts\python.exe'; FLAGS=-I; ENG=processes ;;
  threads) PY='C:\temp\py315\gate315\Scripts\python.exe'; FLAGS=-I; ENG=threads ;;
  threads-ft) PY='C:\temp\py315\gate315t\Scripts\python.exe'; FLAGS='-I -X gil=0'; ENG=threads ;;
  processes-ft) PY='C:\temp\py315\gate315t\Scripts\python.exe'; FLAGS='-I -X gil=0'; ENG=processes ;;
esac
OUT="$STAGE/runs/$NAME"; WOUT="$WSTAGE\\runs\\$NAME"
rm -rf "$OUT"; mkdir -p "$OUT"
LIMIT=$(python3 -c "print(int($SECS + 150))")
cd /mnt/c/temp
timeout "$LIMIT" cmd.exe /c "$PY $FLAGS $WSTAGE\\desktop\\app.py --engines $ENG --seconds $SECS --out $WOUT $*" > "$OUT/app.log" 2>&1 &
APP=$!
timeout "$LIMIT" cmd.exe /c "C:\\temp\\py315\\gate315\\Scripts\\python.exe -I $WSTAGE\\desktop\\send.py --out $WOUT --seconds $(python3 -c "print($SECS - 4)")" > "$OUT/send.log" 2>&1 &
SEND=$!
wait $SEND; wait $APP
echo "app exit $?"
mkdir -p "$ROOT/runs/$NAME"
cp "$OUT"/*.log "$OUT"/*.json "$ROOT/runs/$NAME/" 2>/dev/null
"${PYTHON:-$ROOT/.venv/bin/python}" "$ROOT/tools/desktop_tap_check.py" "$OUT" | tee "$ROOT/runs/$NAME/check.txt"
cp "$OUT/check.json" "$ROOT/runs/$NAME/" 2>/dev/null
