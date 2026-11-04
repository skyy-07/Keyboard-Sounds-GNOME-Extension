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
        'volume': 0.6, 'profile': 'thock', 'pitch-variation': True,
        'space-sound': True, 'repeat-damping': True, 'repeat-volume': 0.3,
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
            t = self._s.get_value(key).get_type_string()
            val = getattr(self._s, {'.d': 'get_double', '.b': 'get_boolean',
                                    '.s': 'get_string'}[t])(key)
        else:
            val = self.FALLBACK[key]
        setattr(self, arg, val)

    def connect(self):
        if self._s is None:
            return
        def refresh(*_a):
            for k in self.FALLBACK:
                self._setattr(k)
        self._s.connect('changed', refresh)


class Player:
    def __init__(self, sounds_dir, args):
        self.sounds_dir = sounds_dir
        self.a = args
        self.last_repeat = 0.0
        self.players = []
        self.devnull = subprocess.DEVNULL
        binary = shutil.which('pw-play') or shutil.which('paplay')
        if binary is None:
            raise SystemExit('no audio player found (need pw-play or paplay)')
        self.binary = binary

    def _variant(self, press):
        if self.a.pitch_variation:
            return random.randint(1, 6)
        return 1

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

        kind = 'space' if self.a.space_sound and code == 0x39 else 'press'
        if not press:  # releases reuse the press sample, quieter
            kind_file = 'space' if kind == 'space' else 'press'
        else:
            kind_file = kind
        path = os.path.join(self.sounds_dir, self.a.profile,
                            f'{kind_file}-{self._variant(press)}.wav')
        if not os.path.isfile(path):
            path = os.path.join(self.sounds_dir, 'thock', 'press-1.wav')
            if not os.path.isfile(path):
                return

        argv = [self.binary, '--volume', f'{vol:.4f}', path]
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
    a.connect()
    player = Player(sounds_dir, a)

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
