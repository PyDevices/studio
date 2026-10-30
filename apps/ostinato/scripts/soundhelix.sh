#!/usr/bin/env bash
# Regenerate the demo song (assets/demo_song.json) from SoundHelix.
#
# SoundHelix (GPL v3, soundhelix.com) composes a song from an XML style and
# a song name that seeds it. This downloads SoundHelix 0.9 once, generates
# the song from its own Piano example, and converts it to an Ostinato
# project. It needs Java (a JDK, for javac).
#
#   scripts/soundhelix.sh [SONG_NAME]      default: Ostinato
set -euo pipefail

NAME="${1:-Ostinato}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/ostinato/soundhelix"
BUILD="$ROOT/runs/soundhelix"
ZIP_URL="https://master.dl.sourceforge.net/project/soundhelix/SoundHelix-0.9-bin.zip?viasf=1"
ZIP_SHA256=8973113ebf1003c85045895c185494d3dee58e2a99cb141048928ff790e26490
SH="$CACHE/soundhelix-0.9"

if [ ! -f "$SH/SoundHelix.jar" ]; then
    mkdir -p "$CACHE"
    curl -fL --retry 3 -o "$CACHE/SoundHelix-0.9-bin.zip" "$ZIP_URL"
    echo "$ZIP_SHA256  $CACHE/SoundHelix-0.9-bin.zip" | sha256sum -c -
    (cd "$CACHE" && unzip -q -o SoundHelix-0.9-bin.zip)
fi

mkdir -p "$BUILD"
# The example includes its shared parts relative to the current directory;
# point them at the unpacked copy instead.
sed "s#\.\./includes/#$SH/includes/#g" "$SH/examples/SoundHelix-Piano.xml" > "$BUILD/song.xml"

CP="$SH/SoundHelix.jar:$(printf '%s:' "$SH"/lib/*.jar)"
javac -d "$BUILD" -cp "$CP" "$ROOT/scripts/soundhelix/Export.java"
java -cp "$CP:$BUILD" Export "$BUILD/song.xml" "$NAME" "$BUILD/song" 2>&1 | grep -v '^log4j:'

python3 "$ROOT/scripts/soundhelix_project.py" "$BUILD/song.json" "$ROOT/assets/demo_song.json"
