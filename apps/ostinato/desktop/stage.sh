#!/usr/bin/env bash
# Stage the desktop app and this measuring harness on a Windows path for
# python.exe (run from WSL): tools/win_stage.sh, which copies desktop/ too.
#   desktop/stage.sh [/mnt/c/temp/ostinato-desktop]
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/.." && pwd)
"$ROOT/tools/win_stage.sh" "${1:-/mnt/c/temp/ostinato-desktop}"
