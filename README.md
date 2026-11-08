# MechKeys 🔈⌨️

Mechanical keyboard sounds for every keystroke on **GNOME / Wayland**.
A standalone keyboard daemon reads key events straight from `/dev/input` (the only
Wayland-proof window into keystrokes) and plays short **synthesized** clicks —
no sample downloads, no licensing worries, zero bloat.

The GNOME Shell extension communicates with the daemon over **D-Bus** (`io.github.skyy07.MechKeys`),
following official GNOME Extensions Review Guidelines (no bundled daemons/binaries inside the extension zip).

![profiles]… pick *Thock (deep & rounded)*, *Clack (bright & snappy)* or
*Creamy (soft & muted)*, adjust loudness, and enjoy random pitch variation so
typing doesn't sound like a broken MP3 on loop.

## Architecture

Following [GNOME Extensions review guidelines](https://gjs.guide/guides/gio/dbus.html):
- **GNOME Shell Extension (`mechkeys@skyy-07.github.io`)**: 100% pure JavaScript (GJS). Provides panel indicator, quick toggle, status monitoring, and Adwaita preferences UI. Connects to the daemon via `Gio.DBusProxy`.
- **Daemon (`mechkeysd`)**: Standalone Python package installable via `pip` (or editable from source). Exposes D-Bus service `io.github.skyy07.MechKeys` on the session bus. Reads `/dev/input/event*` keyboards and triggers PipeWire/PulseAudio playback. Can run as a systemd user service or in the background.

## Custom sound capsules & sound packs 🎵

You can load your own custom sound capsules and keyboard recordings into MechKeys:

### 1. Through Preferences GUI (Easiest)
Open the extension settings (click panel icon → **Settings…** or `gnome-extensions prefs mechkeys@skyy-07.github.io`):
- Click **Import .zip…** to extract and install any downloaded sound pack archive directly.
- Click **Import folder…** to copy a sound folder from anywhere on your machine.
- Click **Open Folder** to reveal `~/.local/share/mechkeys/packs` in Nautilus and drop sound folders directly.
- Use the **Active sound capsule** dropdown to instantly switch between your installed capsules.

### 2. Supported Formats
- **Custom Audio Folders**: Any folder of `.wav`, `.ogg`, `.flac`, or `.mp3` files (e.g. `press-1.wav`, `space.wav`, `click.wav`, or per-key recordings). Keys absent from specific mappings automatically use generic press clicks.
- **MechVibes packs**: Folders with `config.json` + `concatenated_audio.wav` (or discrete key sounds). Sliced automatically and cached under `~/.cache/mechkeys/packs/`.

### 3. Command Line (Optional)
```bash
# Import an archive or folder:
mechkeysd --import-pack /path/to/capsule.zip

# List installed capsules:
mechkeysd --list-packs
```

⚠️ **Sound packs are user-supplied and never shipped** in the repo or published zip — packs usually have "personal use only" licenses, while the built-in synthesizer keeps the published extension fully GPL-clean.

## Features

- 🎛️ **Profiles** — Thock / Clack / Creamy (all procedurally synthesized)
- 🎲 **Random pitch variation** — 6 subtly detuned variants per press
- 🪁 **Distinct spacebar** — deeper, longer sample for the wide key
- 🌧️ **Rollover damping** — held/autorepeat keys play quieter, like real typing
- 🩹 **Key release sound** — a faint return-sound with adjustable volume
- 🧩 **Panel indicator** — toggle on/off, test click, daemon connection state, settings menu
- 🚀 **Systemd user service** — run seamlessly in the background with D-Bus activation
- 🔊 Uses PipeWire (`pw-play`); falls back to `paplay` automatically

## Layout

```
Keyboard addition/
├── build.sh                  → zip the extension for extensions.gnome.org (shexli clean)
├── install.sh                → developer install (extension + D-Bus service + systemd service)
├── pyproject.toml            → pip package definition for mechkeys daemon
├── dbus/
│   └── io.github.skyy07.MechKeys.service  → D-Bus session service definition
├── systemd/
│   └── mechkeysd.service     → systemd user unit
├── mechkeys/                 → standalone Python package
│   ├── __init__.py
│   ├── __main__.py
│   └── daemon.py             → evdev reader + D-Bus service (io.github.skyy07.MechKeys)
├── tools/
│   └── synth_sounds.py       → regenerates procedural WAVs
└── mechkeys@skyy-07.github.io/ → pure GNOME Shell Extension (zero Python)
    ├── metadata.json
    ├── extension.js          → panel indicator + D-Bus proxy
    ├── prefs.js              → GTK4/Adwaita preferences + D-Bus control
    ├── schemas/…gschema.xml  → GSettings definitions
    └── sounds/thock|clack|creamy/{press,space}-N.wav
```

## Quick Start & Installation

### 1. Install Daemon via pip

From repository:
```bash
pip install .
```
Or once published to PyPI:
```bash
pip install mechkeys
```

### 2. Permissions (one-time)
Allow reading `/dev/input` devices:
```bash
sudo usermod -aG input "$USER"
```
Log out and log back in so the group membership takes effect.

### 3. Start Daemon
Enable and start the systemd user service:
```bash
systemctl --user enable --now mechkeysd
```
Or run directly in terminal:
```bash
mechkeysd
```

### 4. Install & Enable Extension
Install from [extensions.gnome.org](https://extensions.gnome.org) or locally:
```bash
./install.sh
gnome-extensions enable mechkeys@skyy-07.github.io
```

## Packaging for extensions.gnome.org (EGO)

```bash
./build.sh        # → dist/mechkeys@skyy-07.github.io.zip
```

Verify compliance with `shexli`:
```bash
shexli dist/mechkeys@skyy-07.github.io.zip
```
*(Results: 0 findings, 0 errors, 0 warnings)*

Submit `dist/mechkeys@skyy-07.github.io.zip` at [extensions.gnome.org/upload](https://extensions.gnome.org/upload/).

Licensed under GPL-3.0.
