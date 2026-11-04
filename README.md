# MechKeys 🔈⌨️

Mechanical keyboard sounds for every keystroke on **GNOME / Wayland**.
A tiny keyboard daemon reads key events straight from `/dev/input` (the only
Wayland-proof window into keystrokes) and plays short **synthesized** clicks —
no sample downloads, no licensing worries, zero Python dependencies (~470 KB).

![profiles]… pick *Thock (deep & rounded)*, *Clack (bright & snappy)* or
*Creamy (soft & muted)*, adjust loudness, and enjoy random pitch variation so
typing doesn't sound like a broken MP3 on loop.

## Custom sound packs (MechVibes format) 🎵

Any MechVibes-style pack — a folder with `config.json` + `concatenated_audio.wav`
— can power the daemon with **per-key recordings** (a Keychron Q3 kit ships as
`r1-36ef0a69` in the example):

```bash
mkdir -p ~/.local/share/mechkeys/packs
cp -r /path/to/pack-folder ~/.local/share/mechkeys/packs/
```

Then choose **Custom pack** in the preferences (and type the folder name there;
default `r1-36ef0a69`). The daemon slices the concatenated WAV into per-key
samples on first use (cached under `~/.cache/mechkeys/packs/`), and switches
samples instantly when you flip profiles. Keys absent from the pack fall back
to a neighbor sample.

⚠️ **Sound packs are user-supplied and never shipped** in the repo or the
published zip — packs usually have "personal use only" licenses (this one
does), while the synthesizer keeps the published extension fully GPL-clean.

## Features

- 🎛️ **Profiles** — Thock / Clack / Creamy (all procedurally synthesized)
- 🎲 **Random pitch variation** — 6 subtly detuned variants per press
- 🪁 **Distinct spacebar** — deeper, longer sample for the wide key
- 🌧️ **Rollover damping** — held/autorepeat keys play quieter, like real typing
- 🩹 **Key release sound** — a faint return-sound with adjustable volume
- 🧩 **Panel indicator** — toggle on/off, test click, settings menu
- 🚀 **Login autostart** — optional, from the preferences window
- 🔊 Uses PipeWire (`pw-play`); falls back to `paplay` automatically

## Layout

```
Keyboard addition/
├── build.sh                  → zip the extension (schema bundled)
├── install.sh                → developer-install into ~/.local/share
├── tools/
│   └── synth_sounds.py       → regenerates every WAV (stdlib only)
└── mechkeys@skyy-07.github.io/
    ├── metadata.json
    ├── extension.js          → panel indicator + daemon lifecycle
    ├── prefs.js              → GTK4/Adwaita preferences
    ├── mechkeysd.py          → the sound daemon (evdev via stdlib)
    ├── schemas/…gschema.xml  → GSettings definitions
    └── sounds/thock|clack|creamy/{press,space}-N.wav
```

## How it works

1. `mechkeysd.py` scans `/dev/input/event*`, asks the kernel (`EVIOCGBIT`)
   which devices have key events but no mouse/touchpad axes → keyboards only.
2. It watches all keyboards with a single `selectors` loop and decodes
   `struct input_event`s in pure Python. Value `1` = press, `2` =
   autorepeat (damped), `0` = release (quieter).
3. Each keystroke spawns one `pw-play` process with the matched WAV and a
   per-stroke `--volume` — press, repeat, release and spacebar get distinct
   gain multipliers. More than 10 overlapping plays are dropped (never lag).
4. The daemon snapshots your GSettings at launch **and follows live changes**
   — volume/profile toggles apply mid-typing with no restart.
5. `extension.js` owns the daemon (starts/stops with the extension), shows the
   panel toggle, and raises a friendly notification if `/dev/input` isn't
   readable yet. GNOME Shell itself cannot see keystrokes on Wayland — that's
   why the daemon exists.

## Install (dev)

```bash
cd ~/Keyboard\ addition
./install.sh
sudo usermod -aG input "$USER"   # one-time: allow reading /dev/input
# log out & back in (input group + new extension both need a fresh session)
gnome-extensions enable mechkeys@skyy-07.github.io
```

## Package & publish

```bash
./build.sh        # → dist/mechkeys@skyy-07.github.io.zip
```

Upload the zip at [extensions.gnome.org](https://extensions.gnome.org/upload/)
(GNOME Shell reviewers like it when the zip contains the compiled schema, which
`build.sh` already does). Before submitting:

- [ ] Put your real username/URL in `metadata.json` (`uuid`, `url`)
- [ ] Add a `LICENSE` (GPL-3.0 recommended for extensions)
- [ ] Record a short screenshot/preview — sound extensions sell by ear

## Hackability notes

- **Sounds are yours to redeform** — run `python3 tools/synth_sounds.py` after
  tweaking anything in `tools/synth_sounds.py` and move files into `sounds/`.
  Profiles are just recipe objects; add your own to `PROFILES` and to the
  gschema `choices`, the combo row, plus `mechkeysd.py` picks it up and
  resolves the directory by name.
- **Profiles details:** every profile object is `{body_f, harmonic,
  harmonic_amp, decay, noise, noise_decay, lp, dur}` — `body_f` is the
  frequency range, `decay` the ring-out, `noise` the attack transient level,
  and `lp` the lowpass darkness. Spacebars get deeper `body_f`/higher `dur`
  via the `SPACE` object plus an optional spring "ping".
- Autorepeat throttling lives in `REPEAT_MIN_INTERVAL` in `mechkeysd.py`
  (default 45 ms) — lower it for denser repeat sounds.

## Roadmap ideas

- Test mode in prefs to audition all six profiles at once
- Per-key sound layout (nav cluster / print screen differences)
- PipeWire native mixing via `pw-stream` JS bindings for zero process-spawn
- Media-key weirdness: F-keys already click; blacklist them in one line if
  you share an office.

Licensed under GPL-3.0 (add a `LICENSE` file before publishing).
