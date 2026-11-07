#!/usr/bin/env python3
"""MechKeys daemon.

Reads key events for every attached keyboard directly from /dev/input and
plays a short synthesized mechanical-keyboard click on each press. This
kernel-level window is the only reliable way to see keystrokes on Wayland,
where desktop applications (and GNOME Shell extensions) cannot observe keys
from unfocused windows.

Zero third-party dependencies: pure standard library + gobject introspection
(python3-gi, preinstalled on all GNOME systems) for reading your settings.

Exit codes:
    0  clean exit
    12 no readable keyboard devices (user is not in the 'input' group)
"""

import argparse
import fcntl
import json
import os
import random
import selectors
import shutil
import signal
import struct
import subprocess
import sys
import time
import zipfile

EV_KEY = 1
EV_REL = 2
EV_ABS = 3

# struct input_event on 64-bit Linux: 2 x timeval quads, u16 type, u16 code, s32 value
EVENT_FMT = '=QQHHi'
EVENT_SIZE = struct.calcsize(EVENT_FMT)

# standard keyboard codes live below BTN_MOUSE (0x110); higher codes are
# mice, touchpads, joysticks, etc. and must not make keyboard sounds.
KEY_MAX_PLAIN = 0x110

# which repeats to skip when damping (avoid hammering the process spawner)
REPEAT_MIN_INTERVAL = 0.045

# evdev key codes -> JavaScript KeyboardEvent.code names (MechVibes packs use
# these as file/config keys). Covers the standard 104-key layout.
KEY_CODE_NAME = {
    1: 'Escape', 2: 'Digit1', 3: 'Digit2', 4: 'Digit3', 5: 'Digit4',
    6: 'Digit5', 7: 'Digit6', 8: 'Digit7', 9: 'Digit8', 10: 'Digit9',
    11: 'Digit0', 12: 'Minus', 13: 'Equal', 14: 'Backspace', 15: 'Tab',
    16: 'KeyQ', 17: 'KeyW', 18: 'KeyE', 19: 'KeyR', 20: 'KeyT', 21: 'KeyY',
    22: 'KeyU', 23: 'KeyI', 24: 'KeyO', 25: 'KeyP', 26: 'BracketLeft',
    27: 'BracketRight', 28: 'Enter', 29: 'ControlLeft', 30: 'KeyA',
    31: 'KeyS', 32: 'KeyD', 33: 'KeyF', 34: 'KeyG', 35: 'KeyH', 36: 'KeyJ',
    37: 'KeyK', 38: 'KeyL', 39: 'Semicolon', 40: 'Quote', 41: 'Backquote',
    42: 'ShiftLeft', 43: 'Backslash', 44: 'KeyZ', 45: 'KeyX', 46: 'KeyC',
    47: 'KeyV', 48: 'KeyB', 49: 'KeyN', 50: 'KeyM', 51: 'Comma',
    52: 'Period', 53: 'Slash', 54: 'ShiftRight', 55: 'NumpadMultiply',
    56: 'AltLeft', 57: 'Space', 58: 'CapsLock', 69: 'NumLock',
    70: 'ScrollLock', 74: 'NumpadSubtract', 78: 'NumpadAdd',
    82: 'Numpad0', 83: 'NumpadDecimal', 87: 'F11', 88: 'F12', 96: 'NumpadEnter',
    97: 'ControlRight', 98: 'NumpadDivide', 99: 'PrintScreen', 100: 'AltRight',
    102: 'Home', 103: 'ArrowUp', 104: 'PageUp', 105: 'ArrowLeft',
    106: 'ArrowRight', 107: 'End', 108: 'ArrowDown', 109: 'PageDown',
    110: 'Insert', 111: 'Delete', 113: 'AudioMute', 114: 'AudioVolumeDown',
    115: 'AudioVolumeUp', 116: 'Power', 119: 'Pause',
    125: 'MetaLeft', 126: 'MetaRight',
    71: 'Numpad7', 72: 'Numpad8', 73: 'Numpad9', 75: 'Numpad4',
    76: 'Numpad5', 77: 'Numpad6', 79: 'Numpad1', 80: 'Numpad2', 81: 'Numpad3',
    86: 'Backslash', 152: 'NumpadComma',
}
# synthetic key-name for unnamed/extra keys
KEY_FALLBACK_NAMES = ('Enter', 'Space', 'KeyA', 'KeyS', 'KeyD', 'KeyF')


AUDIO_EXTENSIONS = ('.wav', '.ogg', '.flac', '.mp3')


class SoundPack:
    """Supports both MechVibes-style packs and loose-audio custom sound capsules.

    1. MechVibes packs: config.json + concatenated audio (or discrete files).
       Slices audio into per-key WAVs under ~/.cache/mechkeys/packs/<name>/ once.
    2. Custom audio capsules: A folder containing audio files (e.g. press.wav,
       space.wav, click-1.wav, etc.). Maps named keys or picks randomly from
       available sounds.
    """

    def __init__(self, pack_dir, cache_dir):
        self.ok = False
        self.files = {}          # key_name -> list of file paths
        self.generic_press = []  # fallback press files
        self.generic_release = []# fallback release files
        self.pack_name = os.path.basename(pack_dir.rstrip(os.sep))

        if not os.path.isdir(pack_dir):
            return

        config_path = os.path.join(pack_dir, 'config.json')
        if os.path.isfile(config_path):
            if self._load_mechvibes(pack_dir, config_path, cache_dir):
                self.ok = True
                return

        # If no config.json or MechVibes load failed, try loading as a loose sound capsule
        if self._load_audio_capsule(pack_dir):
            self.ok = True

    def _load_mechvibes(self, pack_dir, config_path, cache_dir):
        try:
            with open(config_path, encoding='utf-8') as f:
                cfg = json.load(f)
        except Exception as exc:
            print(f'pack config unreadable: {exc}', file=sys.stderr)
            return False

        pack_name = cfg.get('name') or self.pack_name
        self.pack_name = pack_name

        audio_name = cfg.get('sound') or 'concatenated_audio.wav'
        audio_path = os.path.join(pack_dir, audio_name)
        if not os.path.isfile(audio_path):
            for f in sorted(os.listdir(pack_dir)):
                if f.lower().endswith(AUDIO_EXTENSIONS):
                    audio_path = os.path.join(pack_dir, f)
                    break

        definitions = cfg.get('definitions', {}) or cfg.get('key_defines', {})
        if not definitions:
            return False

        has_timing = False
        variants = {}
        discrete_files = {}

        for key, info in definitions.items():
            if isinstance(info, dict) and 'timing' in info:
                has_timing = True
                for start_ms, end_ms in info.get('timing', []):
                    variants.setdefault(key, []).append((int(start_ms), int(end_ms)))
            elif isinstance(info, str) and info.lower().endswith(AUDIO_EXTENSIONS):
                target_file = os.path.join(pack_dir, info)
                if os.path.isfile(target_file):
                    discrete_files.setdefault(key, []).append(target_file)

        if discrete_files and not has_timing:
            self.files = discrete_files
            self._fill_generic_from_files()
            return bool(self.files)

        if not has_timing or not os.path.isfile(audio_path):
            return False

        out_dir = os.path.join(cache_dir, pack_name)
        self.files = {
            key: [os.path.join(out_dir, f'{key}-{i}.wav')
                  for i in range(1, len(takes) + 1)]
            for key, takes in variants.items()
        }

        # Check if cache is already complete
        all_exist = all(os.path.isfile(p) for flist in self.files.values() for p in flist)
        if all_exist:
            self._fill_generic_from_files()
            return True

        try:
            os.makedirs(out_dir, exist_ok=True)
            import wave
            with wave.open(audio_path, 'rb') as src:
                params = src.getparams()
                raw = src.readframes(src.getnframes())
            frame_bytes = params.sampwidth * params.nchannels
            for key, takes in variants.items():
                for i, (start_ms, end_ms) in enumerate(takes, start=1):
                    f0 = int(start_ms / 1000.0 * params.framerate)
                    nf = max(1, int((end_ms - start_ms) / 1000.0 * params.framerate))
                    dest = os.path.join(out_dir, f'{key}-{i}.wav')
                    with wave.open(dest, 'wb') as dst:
                        dst.setnchannels(params.nchannels)
                        dst.setsampwidth(params.sampwidth)
                        dst.setframerate(params.framerate)
                        dst.writeframes(raw[f0 * frame_bytes:(f0 + nf) * frame_bytes])
        except OSError as exc:
            print(f'pack slicing failed: {exc}', file=sys.stderr)
            return False

        self._fill_generic_from_files()
        return True

    def _load_audio_capsule(self, pack_dir):
        """Load loose audio files from a sound capsule folder."""
        found_files = []
        for root, _, files in os.walk(pack_dir):
            for f in sorted(files):
                if f.lower().endswith(AUDIO_EXTENSIONS) and not f.startswith('.'):
                    found_files.append(os.path.join(root, f))

        if not found_files:
            return False

        for path in found_files:
            base = os.path.splitext(os.path.basename(path))[0].lower()

            if any(k in base for k in ('release', 'up')):
                self.generic_release.append(path)
                continue

            matched = False
            if 'space' in base:
                self.files.setdefault('Space', []).append(path)
                matched = True
            elif 'enter' in base or 'return' in base:
                self.files.setdefault('Enter', []).append(path)
                matched = True
            elif 'backspace' in base:
                self.files.setdefault('Backspace', []).append(path)
                matched = True
            elif 'tab' in base:
                self.files.setdefault('Tab', []).append(path)
                matched = True
            elif 'delete' in base:
                self.files.setdefault('Delete', []).append(path)
                matched = True
            elif 'esc' in base:
                self.files.setdefault('Escape', []).append(path)
                matched = True
            else:
                for code_name in KEY_CODE_NAME.values():
                    c_low = code_name.lower()
                    if base == c_low or base.startswith(c_low + '-') or base.startswith(c_low + '_'):
                        self.files.setdefault(code_name, []).append(path)
                        matched = True
                        break

            if not matched or any(k in base for k in ('press', 'click', 'hit', 'down', 'stroke', 'tap', 'key')):
                self.generic_press.append(path)

        if not self.generic_press and found_files:
            self.generic_press = [f for f in found_files if f not in self.generic_release] or found_files

        self._fill_generic_from_files()
        return True

    def _fill_generic_from_files(self):
        if not self.generic_press:
            for k in KEY_FALLBACK_NAMES:
                if k in self.files:
                    self.generic_press.extend(self.files[k])
            if not self.generic_press:
                for flist in self.files.values():
                    self.generic_press.extend(flist)

    def pick(self, code, is_release=False):
        if is_release and self.generic_release:
            return random.choice(self.generic_release)

        name = KEY_CODE_NAME.get(code)
        if name and name in self.files and self.files[name]:
            return random.choice(self.files[name])

        if self.generic_press:
            return random.choice(self.generic_press)

        return None


def import_pack(source_path):
    """Import a .zip archive or directory into ~/.local/share/mechkeys/packs/."""
    source_path = os.path.expanduser(source_path)
    if not os.path.exists(source_path):
        print(f"Error: {source_path} does not exist", file=sys.stderr)
        return None

    data_home = os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share')
    dest_base = os.path.join(data_home, 'mechkeys', 'packs')
    os.makedirs(dest_base, exist_ok=True)

    base_name = os.path.splitext(os.path.basename(source_path.rstrip(os.sep)))[0]
    safe_name = "".join(c if (c.isalnum() or c in '._- ') else '_' for c in base_name).strip() or "custom_pack"
    target_dir = os.path.join(dest_base, safe_name)

    if os.path.isfile(source_path):
        if not zipfile.is_zipfile(source_path):
            if source_path.lower().endswith(AUDIO_EXTENSIONS):
                os.makedirs(target_dir, exist_ok=True)
                shutil.copy2(source_path, target_dir)
                return safe_name
            print(f"Error: {source_path} is not a valid zip archive or audio file", file=sys.stderr)
            return None

        with zipfile.ZipFile(source_path, 'r') as z:
            top_levels = {p.split('/')[0] for p in z.namelist() if p and not p.startswith('/')}
            has_root_files = any('/' not in p and p for p in z.namelist() if not p.endswith('/'))
            if len(top_levels) == 1 and not has_root_files:
                inner_dir = list(top_levels)[0]
                temp_extract = os.path.join(dest_base, f".tmp_{safe_name}")
                if os.path.exists(temp_extract):
                    shutil.rmtree(temp_extract)
                z.extractall(temp_extract)
                extracted_root = os.path.join(temp_extract, inner_dir)
                if os.path.exists(target_dir):
                    shutil.rmtree(target_dir)
                shutil.move(extracted_root, target_dir)
                shutil.rmtree(temp_extract, ignore_errors=True)
            else:
                if os.path.exists(target_dir):
                    shutil.rmtree(target_dir)
                os.makedirs(target_dir, exist_ok=True)
                z.extractall(target_dir)
        return safe_name

    elif os.path.isdir(source_path):
        if os.path.abspath(source_path) == os.path.abspath(target_dir):
            return safe_name
        if os.path.exists(target_dir):
            shutil.rmtree(target_dir)
        shutil.copytree(source_path, target_dir)
        return safe_name

    return None


def list_packs():
    """List all sound packs / capsules installed under ~/.local/share/mechkeys/packs/."""
    data_home = os.environ.get('XDG_DATA_HOME') or os.path.expanduser('~/.local/share')
    packs_dir = os.path.join(data_home, 'mechkeys', 'packs')
    results = []
    if os.path.isdir(packs_dir):
        for entry in sorted(os.listdir(packs_dir)):
            if entry.startswith('.'):
                continue
            full_path = os.path.join(packs_dir, entry)
            if not os.path.isdir(full_path):
                continue
            info = {"id": entry, "path": full_path, "name": entry, "type": "audio_capsule"}
            cfg_path = os.path.join(full_path, "config.json")
            if os.path.isfile(cfg_path):
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                    info["name"] = cfg.get("name") or entry
                    info["type"] = "mechvibes"
                except Exception:
                    pass
            results.append(info)
    return results


def _ioc(direction, size, t, nr):
    return (direction << 30) | (size << 16) | (t << 8) | nr


def _ior(t, nr, size):
    return _ioc(2, size, t, nr)


def eviocgbit(ev_type, size):
    return _ior(0x45, 0x20 + ev_type, size)


def get_bitmap(fd, ev_type, size):
    buf = bytearray(size)
    try:
        fcntl.ioctl(fd, eviocgbit(ev_type, size), buf)
    except OSError:
        return None
    return buf


def bit_set(buf, bit):
    return buf is not None and bool((buf[bit >> 3] >> (bit & 7)) & 1)


def is_keyboard(fd):
    """Best-effort keyboard detection: has key events, but no REL/ABS axes."""
    ev = get_bitmap(fd, 0, 32)
    if ev is None:
        return True  # can't introspect; assume keyboard
    if not bit_set(ev, EV_KEY):
        return False
    if bit_set(ev, EV_REL) or bit_set(ev, EV_ABS):
        return False
    keys = get_bitmap(fd, 1, 128)
    if keys is None:
        return True
    # real typing keyboards: check a few letter keys (Q=16, E=18, S=31, C=46)
    return any(bit_set(keys, k) for k in (16, 18, 30, 31, 46, 57))


def get_name(fd):
    buf = bytearray(128)
    try:
        fcntl.ioctl(fd, _ior(0x45, 0x06, len(buf)), buf)
    except OSError:
        return '?'
    return buf.split(b'\0', 1)[0].decode('utf-8', 'replace')


class Args:
    """Snapshot of settings, refreshed live from GSettings (or fallbacks)."""

    FALLBACK = {
        'volume': 0.6, 'profile': 'thock', 'pack-name': 'r1-36ef0a69',
        'pitch-variation': True, 'space-sound': True,
        'repeat-damping': True, 'repeat-volume': 0.3,
        'release-sound': True, 'release-volume': 0.45,
    }

    def __init__(self, args):
        self._args = args
        self._s = None
        try:
            from gi.repository import Gio
            if args.schemadir:
                src = Gio.SettingsSchemaSource.new_from_directory(
                    args.schemadir, Gio.SettingsSchemaSource.get_default(), False)
                schema = src.lookup(args.schema, True)
                if schema is not None:
                    self._s = Gio.Settings.new_full(schema, None, None)
        except Exception as exc:  # cannot read settings; run with defaults
            print(f'settings unavailable ({exc}); using built-in defaults',
                  file=sys.stderr)
        for k in self.FALLBACK:
            self._setattr(k)

    def _setattr(self, key):
        arg = key.replace('-', '_')
        if self._s is not None:
            t = self._s.get_value(key).get_type_string().lstrip('.')
            getter = {
                'd': 'get_double',
                'b': 'get_boolean',
                's': 'get_string',
                'i': 'get_int',
                'u': 'get_uint',
            }.get(t)
            if getter and hasattr(self._s, getter):
                val = getattr(self._s, getter)(key)
            else:
                val = self.FALLBACK[key]
        else:
            val = self.FALLBACK[key]
        setattr(self, arg, val)

    def connect(self, on_change=None):
        if self._s is None:
            return
        def refresh(*_a):
            for k in self.FALLBACK:
                self._setattr(k)
            if on_change:
                on_change()
        self._s.connect('changed', refresh)


class Player:
    def __init__(self, sounds_dir, args):
        self.sounds_dir = sounds_dir
        self.a = args
        self.last_repeat = 0.0
        self.players = []
        self.devnull = subprocess.DEVNULL
        self.pack = None
        binary = shutil.which('pw-play') or shutil.which('paplay')
        if binary is None:
            raise SystemExit('no audio player found (need pw-play or paplay)')
        self.binary = binary
        self.reload_pack()

    def reload_pack(self):
        """(Re)load a custom sound capsule / MechVibes pack."""
        self.pack = None
        if getattr(self.a, 'profile', '') != 'pack':
            return
        data_home = os.environ.get('XDG_DATA_HOME') or os.path.expanduser(
            '~/.local/share')
        pack_name = getattr(self.a, 'pack_name', '').strip()
        packs_base = os.path.join(data_home, 'mechkeys', 'packs')

        pack_dir = ''
        if pack_name and os.path.isabs(pack_name) and os.path.isdir(pack_name):
            pack_dir = pack_name
        elif pack_name:
            pack_dir = os.path.join(packs_base, pack_name)

        # Fallback to any installed pack if specified pack doesn't exist
        if (not pack_dir or not os.path.isdir(pack_dir)) and os.path.isdir(packs_base):
            for e in sorted(os.listdir(packs_base)):
                p = os.path.join(packs_base, e)
                if not e.startswith('.') and os.path.isdir(p):
                    pack_dir = p
                    break

        cache = os.path.join(
            os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache'),
            'mechkeys', 'packs')
        if pack_dir and os.path.isdir(pack_dir):
            self.pack = SoundPack(pack_dir, cache)
            if self.pack.ok:
                return

        print('Sound capsule not found/invalid; falling back to synthesized "thock" sounds',
              file=sys.stderr)
        self.pack = None

    def _pick_path(self, code, press):
        if self.a.profile == 'pack':
            if self.pack is not None and self.pack.ok:
                sample = self.pack.pick(code, is_release=not press)
                if sample is not None:
                    return sample
                if not press and self.a.release_sound:
                    return self.pack.pick(code, is_release=False)
                if press:
                    return self.pack.pick(code, is_release=False)
            # If pack failed or missing, fall through to synthesized sounds
        if not press and not self.a.release_sound:
            return None
        kind_file = ('space' if (press and self.a.space_sound and
                                 code == 0x39) else 'press')
        if not press:  # releases reuse the press sample, quieter
            kind_file = 'space' if kind_file == 'space' else 'press'
        variant = random.randint(1, 6) if self.a.pitch_variation else 1
        path = os.path.join(self.sounds_dir, self.a.profile
                            if self.a.profile != 'pack' else 'thock',
                            f'{kind_file}-{variant}.wav')
        if not os.path.isfile(path):
            path = os.path.join(self.sounds_dir, 'thock', 'press-1.wav')
        return path if os.path.isfile(path) else None

    def play(self, code, value):
        press = value == 1
        repeat = value == 2
        release = value == 0
        vol = self.a.volume

        if repeat:
            if not self.a.repeat_damping:
                return
            now = time.monotonic()
            if now - self.last_repeat < REPEAT_MIN_INTERVAL:
                return
            self.last_repeat = now
            vol *= self.a.repeat_volume
        elif release:
            if not self.a.release_sound:
                return
            vol *= self.a.release_volume

        path = self._pick_path(code, press)
        if path is None:
            return

        argv = [self.binary, '--volume', f'{vol:.4f}']
        if os.path.basename(self.binary) == 'pw-play':
            argv.extend(['--latency', '10ms'])
        argv.append(path)
        # recycle finished players
        self.players = [p for p in self.players if p.poll() is None]
        if len(self.players) >= 10:  # too much already in flight
            return
        try:
            self.players.append(subprocess.Popen(
                argv, stdout=self.devnull, stderr=self.devnull))
        except OSError as exc:
            print(f'playback failed: {exc}', file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description='MechKeys keyboard sound daemon')
    ap.add_argument('--sounds-dir', default=None)
    ap.add_argument('--schemadir', default=None)
    ap.add_argument('--schema', default='org.gnome.shell.extensions.mechkeys')
    ap.add_argument('--import-pack', metavar='PATH', help='Import a .zip or folder into sound capsules and exit')
    ap.add_argument('--list-packs', action='store_true', help='List installed sound capsules in JSON format and exit')
    ap.add_argument('--debug', action='store_true')
    args = ap.parse_args()

    if args.list_packs:
        print(json.dumps(list_packs()))
        sys.exit(0)

    if args.import_pack:
        imported_name = import_pack(args.import_pack)
        if imported_name:
            print(imported_name)
            sys.exit(0)
        else:
            sys.exit(1)

    default_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sounds')
    sounds_dir = args.sounds_dir or default_dir

    a = Args(args)
    player = Player(sounds_dir, a)
    a.connect(player.reload_pack)

    # Enumerate & open keyboards.
    sel = selectors.DefaultSelector()
    leftovers = {}
    opened = 0
    for dev in sorted(os.listdir('/dev/input')):
        if not dev.startswith('event'):
            continue
        path = os.path.join('/dev/input', dev)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as exc:
            if args.debug:
                print(f'{path}: cannot open ({exc})', file=sys.stderr)
            continue
        if not is_keyboard(fd):
            os.close(fd)
            continue
        sel.register(fd, selectors.EVENT_READ, path)
        leftovers[fd] = b''
        opened += 1
        if args.debug:
            print(f'{path}: listening ({get_name(fd)})', file=sys.stderr)

    if opened == 0:
        print('MechKeys: no readable keyboard devices.', file=sys.stderr)
        print('Fix: run "sudo usermod -aG input $USER", then log out and back in.',
              file=sys.stderr)
        sys.exit(12)

    running = True

    def stop(signum, _frame):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGHUP, stop)

    if args.debug:
        print(f'daemon running: {opened} keyboard(s)', file=sys.stderr)

    try:
        while running:
            for key, _mask in sel.select(timeout=0.25):
                fd = key.fd
                try:
                    data = os.read(fd, 1024)
                except (BlockingIOError, InterruptedError):
                    continue
                except OSError:
                    sel.unregister(fd)
                    os.close(fd)
                    leftovers.pop(fd, None)
                    continue
                if not data:  # device detached
                    sel.unregister(fd)
                    os.close(fd)
                    leftovers.pop(fd, None)
                    continue
                buf = leftovers.pop(fd, b'') + data
                n = (len(buf) // EVENT_SIZE) * EVENT_SIZE
                leftovers[fd] = buf[n:]
                for off in range(0, n, EVENT_SIZE):
                    _tvs, _tvu, etype, code, value = struct.unpack_from(
                        EVENT_FMT, buf, off)
                    if etype != EV_KEY or code >= KEY_MAX_PLAIN:
                        continue
                    player.play(code, value)
    finally:
        sel.close()


if __name__ == '__main__':
    main()
