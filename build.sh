#!/usr/bin/env bash
# Package the extension as a zip ready for extensions.gnome.org (EGO).
# Note: GNOME Shell 45+ compiles schemas server-side/client-side, so
# gschemas.compiled must NOT be included in the submission zip (EGO-P-006).
set -euo pipefail
cd "$(dirname "$0")"

EXT_DIR="mechkeys@skyy-07.github.io"
OUT="$(pwd)/dist/mechkeys@skyy-07.github.io.zip"
mkdir -p dist
rm -f "$OUT"

# Ensure proper permissions (non-exec for scripts/data)
chmod 644 "$EXT_DIR"/mechkeysd.py 2>/dev/null || true

( cd "$EXT_DIR" && zip -r -X "$OUT" . \
    -x '*.pyc' -x '__pycache__/*' -x '.git*' -x 'schemas/gschemas.compiled' )

echo "built: $OUT"
