#!/usr/bin/env python3
"""MechKeys sound synthesizer.

Generates the click/thock WAV files shipped inside the extension using only
the Python standard library (no numpy needed). Sounds are fully synthesized,
so the extension ship pack is 100% free of copyright/licensing issues.

Usage:
    python3 synth_sounds.py [output_dir]

Default output: ../mechkeys@skyy-07.github.io/sounds
"""

import math
import os
import random
import struct
import sys
import wave

SR = 44100            # sample rate
VARIANTS = 6          # pitch variants per profile/kind
PEAK = 0.90           # normalize peak amplitude

# Sound recipe per profile. Values are tuned by ear; tweak freely.
PROFILES = {
    # deep, rounded "thock" — like a Gateron/black switch on a dense board
    'thock': {
        'body_f':  (150, 195),   # fundamental Hz range
        'harmonic': 2.35,        # overtone multiplier
        'harmonic_amp': 0.38,
        'decay':   0.022,        # body decay time constant (s)
        'noise':   0.50,         # transient noise level
        'noise_decay': 0.0038,   # transient decay (s)
        'lp':      0.30,         # one-pole lowpass coefficient (lower = darker)
        'dur':     0.085,
    },
    # bright, snappy "clack" — like a clicky blue switch
    'clack': {
        'body_f':  (330, 430),
        'harmonic': 2.1,
        'harmonic_amp': 0.30,
        'decay':   0.011,
        'noise':   0.85,
        'noise_decay': 0.0022,
        'lp':      0.55,
        'dur':     0.062,
    },
    # soft, muted "creamy" — like a lubed linear switch with foam
    'creamy': {
        'body_f':  (205, 260),
        'harmonic': 2.5,
        'harmonic_amp': 0.30,
        'decay':   0.026,
        'noise':   0.30,
        'noise_decay': 0.005,
        'lp':      0.22,
        'dur':     0.095,
    },
}

# Spacebars are longer keys: slower, deeper, with a faint "ping" resonance.
SPACE = {
    'body_f':  (100, 135),
    'harmonic': 3.1,
    'harmonic_amp': 0.22,
    'decay':   0.048,
    'noise':   0.40,
    'noise_decay': 0.005,
    'lp':      0.20,
    'dur':     0.145,
    'ping_f':  690,          # spring/wire resonance
    'ping_amp': 0.10,
    'ping_decay': 0.014,
}


def render(params, rng):
    """Render one click into a list of floats in [-1, 1]."""
    n = max(1, int(SR * params['dur']))

    f0 = rng.uniform(*params['body_f'])
    f0 *= 2 ** rng.uniform(-0.03, 0.03)          # tiny global detune
    dec = params['decay'] * rng.uniform(0.85, 1.18)
    noise_lvl = params['noise'] * rng.uniform(0.7, 1.25)
    n_decay = params['noise_decay'] * rng.uniform(0.7, 1.3)
    harm = params['harmonic'] * rng.uniform(0.97, 1.04)
    harm_amp = params['harmonic_amp']
    lp = params['lp']

    click_phase = rng.uniform(0, math.tau)
    sample = []
    for i in range(n):
        t = i / SR
        if t > 0.012:                            # transient lives in the first ~12 ms
            trans = 0.0
        else:
            trans = noise_lvl * math.exp(-t / n_decay) * (
                0.6 + 0.4 * math.sin(click_phase + t * 2 * math.pi * rng.uniform(3000, 5200))
            )
        e = math.exp(-t / dec)
        if e < 0.001:
            body = 0.0
        else:
            body = e * (math.sin(2 * math.pi * f0 * t) + harm_amp * math.sin(2 * math.pi * f0 * harm * t))
            if 'ping_f' in params:
                body += params['ping_amp'] * math.exp(-t / params['ping_decay']) * \
                    math.sin(2 * math.pi * params['ping_f'] * t)
        sample.append(trans + body)

    # one-pole lowpass (simple, dependency-free)
    y = 0.0
    a = lp / (lp + 0.0008)
    for i, x in enumerate(sample):
        y += a * (x - y)
        sample[i] = y

    # normalize + last-millisecond fade to avoid tail pop
    m = max(abs(s) for s in sample) or 1.0
    g = PEAK / m
    fade_n = max(1, int(SR * 0.0012))
    for i in range(n):
        out = sample[i] * g
        if i >= n - fade_n:
            out *= (n - i) / fade_n
        sample[i] = out
    return sample


def write_wav(path, samples):
    data = bytearray()
    for s in samples:
        s = max(-1.0, min(1.0, s))
        data += struct.pack('<h', int(s * 32767.0))
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(bytes(data))


def main():
    out_root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        '..', 'mechkeys@skyy-07.github.io', 'sounds')

    for profile, params in PROFILES.items():
        rng_master = random.Random(profile)
        params['ping_f'] = SPACE['ping_f']
        params.setdefault('ping_amp', SPACE['ping_amp'])
        params.setdefault('ping_decay', SPACE['ping_decay'])
        params.pop('ping_f')

        letters, spaces = [], []
        for press in (True, False):  # False → space variant
            spec = SPACE if not press else params
            for i in range(1, VARIANTS + 1):
                rng = random.Random(f'{profile}-{"press" if press else "space"}-{i}')
                samples = render(spec, rng)
                (letters if press else spaces).append((i, samples))

        pdir = os.path.join(out_root, profile)
        os.makedirs(pdir, exist_ok=True)
        for i, samples in letters:
            write_wav(os.path.join(pdir, f'press-{i}.wav'), samples)
        for i, samples in spaces:
            write_wav(os.path.join(pdir, f'space-{i}.wav'), samples)
        print(f'wrote {pdir} (press x{len(letters)}, space x{len(spaces)})')


if __name__ == '__main__':
    main()
