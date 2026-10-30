#!/usr/bin/env bash
set -euo pipefail

PYSCRIPT_VERSION="${PYSCRIPT_VERSION:-2026.7.3}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/vendor/pyscript"
ARCHIVE="$(mktemp)"
TMP="$(mktemp -d)"
trap 'rm -f "$ARCHIVE"; rm -rf "$TMP"' EXIT

curl -fsSL \
  "https://pyscript.net/releases/${PYSCRIPT_VERSION}/offline_${PYSCRIPT_VERSION}.zip" \
  -o "$ARCHIVE"
unzip -q "$ARCHIVE" -d "$TMP"
rm -rf "$DEST"
mkdir -p "$(dirname "$DEST")"
mv "$TMP/offline/pyscript" "$DEST"
printf 'Vendored PyScript %s in %s\n' "$PYSCRIPT_VERSION" "$DEST"
