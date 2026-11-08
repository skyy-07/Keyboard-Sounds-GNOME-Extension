#!/usr/bin/env bash
# Local developer install of MechKeys (GNOME Extension + Python D-Bus daemon).
set -euo pipefail
cd "$(dirname "$0")"

EXT_DIR="mechkeys@skyy-07.github.io"
EXT_ID="mechkeys@skyy-07.github.io"
DEST="$HOME/.local/share/gnome-shell/extensions/$EXT_ID"

echo "=== 1. Installing GNOME Shell Extension ==="
mkdir -p "$HOME/.local/share/gnome-shell/extensions"
rm -rf "$DEST"
cp -r "$EXT_DIR" "$DEST"
find "$DEST" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

glib-compile-schemas "$DEST/schemas/"
echo "Installed extension to: $DEST"

echo
echo "=== 2. Setting up D-Bus & Systemd User Services ==="
mkdir -p "$HOME/.config/systemd/user" "$HOME/.local/share/dbus-1/services" "$HOME/.local/bin"

cp systemd/mechkeysd.service "$HOME/.config/systemd/user/"
cp dbus/io.github.skyy07.MechKeys.service "$HOME/.local/share/dbus-1/services/"

# Link local venv binary to ~/.local/bin if not already in system PATH
if ! command -v mechkeysd >/dev/null 2>&1; then
    if [ -f "venv/bin/mechkeysd" ]; then
        ln -sf "$(pwd)/venv/bin/mechkeysd" "$HOME/.local/bin/mechkeysd"
        echo "Linked $(pwd)/venv/bin/mechkeysd -> $HOME/.local/bin/mechkeysd"
    fi
fi

if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload || true
fi

echo
echo "=== 3. Permissions Check ==="
if id -nG "$USER" | tr ' ' '\n' | grep -qx input; then
    echo "input group: OK (keyboard events readable)"
else
    echo
    echo "  ACTION REQUIRED — make /dev/input readable for this user:"
    echo "      sudo usermod -aG input $USER"
    echo "  then log out and back in."
fi

echo
echo "=== 4. Service Management ==="
echo "To enable and start the daemon with systemd:"
echo "    systemctl --user enable --now mechkeysd"
echo "Or run manually:"
echo "    mechkeysd"
echo
echo "To enable the extension in GNOME Shell:"
echo "    gnome-extensions enable $EXT_ID"
