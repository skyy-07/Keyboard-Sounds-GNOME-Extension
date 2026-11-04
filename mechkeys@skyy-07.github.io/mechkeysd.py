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
import os
import random
import selectors
import shutil
import signal
import struct
import subprocess
import sys
import time

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


class SoundPack:
    """MechVibes-style sound pack: config.json + one concatenated WAV.

    sapi config maps key names to per-take time ranges (ms). We slice the
    wave once into per-key WAVs in a cache dir, and later play a random
    take for every keystroke of that key.
    """

    def __init__(self, pack_dir, cache_dir):
        self.ok = False
        config_path = os.path.join(pack_dir, 'config.json')
        audio_path = os.path.join(pack_dir, 'concatenated_audio.wav')
        try:
            import json
            with open(config_path, encoding='utf-8') as f:
                cfg = json.load(f)
        except (OSError, ValueError) as exc:
            print(f'pack unreadable: {exc}', file=sys.stderr)
            return

        variants = {}
        for key, info in cfg.get('definitions', {}).items():
            for i, (start_ms, end_ms) in enumerate(info.get('timing', [])):
                variants.setdefault(key, []).append(
                    (int(start_ms), int(end_ms)))
        if not variants:
            print('pack has no key definitions', file=sys.stderr)
            return

        out_dir = os.path.join(cache_dir, cfg.get('name') or
                               os.path.basename(pack_dir))
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
                    nf = max(1, int((end_ms - start_ms) / 1000.0 *
                                    params.framerate))
                    dest = os.path.join(out_dir, f'{key}-{i}.wav')
                    with wave.open(dest, 'wb') as dst:
                        dst.setnchannels(params.nchannels)
                        dst.setsampwidth(params.sampwidth)
                        dst.setframerate(params.framerate)
                        dst.writeframes(
                            raw[f0 * frame_bytes:(f0 + nf) * frame_bytes])
        except OSError as exc:
            print(f'pack slicing failed: {exc}', file=sys.stderr)
            return

        self.files = {
            key: [os.path.join(out_dir, f'{key}-{i}.wav')
                  for i in range(1, len(takes) + 1)]
            for key, takes in variants.items()
        }
        self.ok = True

    def pick(self, code):
        name = KEY_CODE_NAME.get(code)
        entry = (self.files.get(name) if name else None) or \
            (self.files.get(KEY_FALLBACK_NAMES[random.randrange(
                len(KEY_FALLBACK_NAMES))]))
        if not entry:
            return None
        return entry[random.randrange(len(entry))]


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
        """(Re)load a MechVibes-style pack if the profile asks for one."""
        self.pack = None
        if getattr(self.a, 'profile', '') != 'pack':
            return
        data_home = os.environ.get('XDG_DATA_HOME') or os.path.expanduser(
            '~/.local/share')
        pack_dir = os.path.join(data_home, 'mechkeys', 'packs',
                                getattr(self.a, 'pack_name', ''))
        cache = os.path.join(
            os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache'),
            'mechkeys', 'packs')
        self.pack = SoundPack(pack_dir, cache)
        if not self.pack.ok:
            print('pack not found/invalid; falling back to synthesized '
                  '"thock" sounds', file=sys.stderr)
            self.pack = None

    def _pick_path(self, code, press):
        if self.a.profile == 'pack':
            if self.pack is not None and self.pack.ok:
                return self.pack.pick(code)
            pack = None  # fall through to synthesized sounds
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
    ap.add_argument('--debug', action='store_true')
    args = ap.parse_args()

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
