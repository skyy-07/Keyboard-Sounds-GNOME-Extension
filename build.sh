#!/usr/bin/env bash
# Compile the GSettings schema and package the extension as a zip ready for
# extensions.gnome.org or local install (gnome-extensions install <zip>).
set -euo pipefail
cd "$(dirname "$0")"

EXT_DIR="mechkeys@skyy-07.github.io"
UUID_DIR="$EXT_DIR"

glib-compile-schemas "$EXT_DIR/schemas/" --targetdir="$EXT_DIR/schemas/"

OUT="$(pwd)/dist/mechkeys@skyy-07.github.io.zip"
mkdir -p dist
rm -f "$OUT"
( cd "$EXT_DIR" && zip -r -X "$OUT" . \
    -x '*.pyc' -x '__pycache__/*' -x '.git*')

echo "built: $OUT"
