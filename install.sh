#!/usr/bin/env bash
# Local developer install of MechKeys.
set -euo pipefail
cd "$(dirname "$0")"

EXT_DIR="mechkeys@skyy-07.github.io"
EXT_ID="mechkeys@skyy-07.github.io"
DEST="$HOME/.local/share/gnome-shell/extensions/$EXT_ID"

mkdir -p "$HOME/.local/share/gnome-shell/extensions"
rm -rf "$DEST"
cp -r "$EXT_DIR" "$DEST"
find "$DEST" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

glib-compile-schemas "$DEST/schemas/"

echo
echo "Installed to: $DEST"

if id -nG "$USER" | tr ' ' '\n' | grep -qx input; then
    echo "input group: OK (keyboard events readable)"
else
    echo
    echo "  ACTION REQUIRED — make /dev/input readable for this user:"
    echo "      sudo usermod -aG input $USER"
    echo "  then log out and back in."
fi

echo
echo "Wayland tip: log out and back in once, then enable with:"
echo "    gnome-extensions enable $EXT_ID"
