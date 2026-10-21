#!/usr/bin/env python3
"""Assemble the ADA (Ingress) battery lock chime on the Pi, stdlib only.

  chime_assemble.py PERCENT [--sentry] [-o OUT.wav] [--parts DIR]
  chime_assemble.py --from-car [--sentry] [-o OUT.wav]   (SOC via s3xy_soc.py)

Says "Tesla offline. [Turret deployed.] XM reserves, [critical,] N percent."
Port of Random Thought bot's charge_chime.py v3: the parts in --parts were
pre-processed once by tools/build_chime_parts.py (ffmpeg), so here we only
place, mix, duck and normalise 16-bit mono 44.1 kHz PCM with audioop.
Output is capped below Tesla's 1 MB LockChime limit.
"""
import argparse
import audioop
import json
import os
import subprocess
import sys
import wave

RATE, WIDTH = 44100, 2
PARTS = os.environ.get('CHARGECHIME_PARTS', '/mutable/ChargeChimeParts')
SOC_CMD = ['/usr/bin/python3', '/root/bin/s3xy_soc.py', '--json', '--timeout', '15']
MAX_BYTES = 1000 * 1000 - 1024          # stay under 1 MB including the header
VOICE_START, TAIL = 1.0, 2.4
DIRECT = set(range(1, 21)) | {25, 30, 40, 50, 60, 70, 75, 80, 90, 100}
PADS = {'offline': 0.55, 'deployed': 0.5, 'xm_reserves': 0.15, 'critical': 0.12, 'percent': 0.05}
DUCK_GAIN, DUCK_THRESH, BLOCK = 0.35, 1600, 441   # fx ducked to ~-9 dB under voice


def number_words(n):
    return [n] if n in DIRECT else [n // 10 * 10, n % 10]


def words_for(pct, sentry):
    w = ['tesla', 'offline'] + (['turret', 'deployed'] if sentry else []) + ['xm_reserves']
    return w + (['critical'] if pct < 20 else []) + number_words(pct) + ['percent']


def load(parts, name):
    with wave.open(os.path.join(parts, name + '.wav'), 'rb') as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, WIDTH, RATE):
            raise ValueError('%s: need mono 16-bit 44.1 kHz' % name)
        return w.readframes(w.getnframes())


def sec(t):
    return int(round(t * RATE)) * WIDTH


def add_at(buf, clip, t):
    o = max(0, sec(t))
    if o >= len(buf):
        return
    clip = clip[:len(buf) - o]
    buf[o:o + len(clip)] = audioop.add(bytes(buf[o:o + len(clip)]), clip, WIDTH)


def fade_out(buf, start, length):
    n = len(buf) // WIDTH
    a, steps = max(0, start // WIDTH), 20
    span = max(1, (n - a) // steps)
    for i in range(steps):
        s, e = (a + i * span) * WIDTH, (a + (i + 1) * span if i < steps - 1 else n) * WIDTH
        buf[s:e] = audioop.mul(bytes(buf[s:e]), WIDTH, 1 - (i + 1) / steps)


def assemble(pct, sentry=False, parts=PARTS):
    pct = max(1, min(100, int(pct)))
    words = words_for(pct, sentry)
    # voice track
    t, starts, ends, segs = VOICE_START, {}, {}, []
    for w in words:
        name = 'v_' + (w if isinstance(w, str) else 'n%03d' % w)
        pcm = load(parts, name) + b'\0' * sec(PADS.get(w, 0.05))
        starts.setdefault(w, t)
        segs.append((t, pcm))
        t += len(pcm) / (RATE * WIDTH)
        ends[w] = t
    voice_end = t
    total = min(voice_end + TAIL, MAX_BYTES / (RATE * WIDTH))
    voice = bytearray(sec(total))
    for st, pcm in segs:
        add_at(voice, pcm, st)
    # effects bus
    fx = [('major_action_failed', 0.0), ('power_down', starts['tesla'] - 0.15),
          ('portal_ping', starts['xm_reserves'] - 0.35), ('xm_pickup', ends['percent'] - 0.25),
          ('capsule_close', voice_end + 0.05), ('hack_reverse', 0.05),
          ('sub_drop', starts['offline'] - 0.45), ('whale', voice_end - 0.35),
          ('wraith', starts['offline'] - 0.2), ('calculating', starts['xm_reserves'] + 0.1)]
    if sentry:
        fx += [('resonator_hum', starts['turret'] - 0.3), ('target_lock', starts['turret'] - 0.1),
               ('mod_deploy', starts['deployed'] - 0.05)]
    if pct < 20:
        fx += [('damage', starts['critical'] - 0.05), ('ui_fail', ends['percent'] + 0.05)]
    bus = bytearray(len(voice))
    bed = bytearray(load(parts, 'bed')[:len(voice)])
    fade_out(bed, len(bed) - sec(1.0), sec(1.0))
    add_at(bus, bytes(bed), 0)
    for name, st in fx:
        add_at(bus, load(parts, 'fx_' + name), st)
    # duck fx under the voice (block RMS keyed, smoothed attack/release)
    g, step = 1.0, BLOCK * WIDTH
    for o in range(0, len(bus), step):
        target = DUCK_GAIN if audioop.rms(bytes(voice[o:o + step]), WIDTH) > DUCK_THRESH else 1.0
        g += (target - g) * (0.5 if target < g else 0.05)
        if g < 0.999:
            bus[o:o + step] = audioop.mul(bytes(bus[o:o + step]), WIDTH, g)
    # mix at half gain to keep headroom, fade, normalise to -1 dBFS
    mix = bytearray(audioop.add(audioop.mul(bytes(voice), WIDTH, 0.5),
                                audioop.mul(bytes(bus), WIDTH, 0.5), WIDTH))
    fade_out(mix, len(mix) - sec(0.4), sec(0.4))
    peak = audioop.max(bytes(mix), WIDTH) or 1
    return audioop.mul(bytes(mix), WIDTH, 32767 * 0.891 / peak), words, fx, total


def write(path, pcm):
    tmp = path + '.tmp'
    with wave.open(tmp, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(WIDTH)
        w.setframerate(RATE)
        w.writeframes(pcm)
    os.replace(tmp, path)


def soc_from_car():
    try:
        r = subprocess.run(SOC_CMD, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30)
        return int(json.loads(r.stdout.decode())['soc']) if r.returncode == 0 else None
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        return None


def main(argv=None):
    ap = argparse.ArgumentParser(description='Assemble the ADA battery lock chime.')
    ap.add_argument('percent', nargs='?', type=float)
    ap.add_argument('--from-car', action='store_true')
    ap.add_argument('--sentry', action='store_true')
    ap.add_argument('-o', '--out', default='charge_chime.wav')
    ap.add_argument('--parts', default=PARTS)
    ap.add_argument('-v', '--verbose', action='store_true')
    a = ap.parse_args(argv)
    pct = soc_from_car() if a.from_car else a.percent
    if pct is None:
        sys.stderr.write('chime_assemble: no percentage (car not reachable?)\n')
        return 2
    pct = max(1, min(100, int(round(pct))))
    pcm, words, fx, total = assemble(pct, a.sentry, a.parts)
    write(a.out, pcm)
    if a.verbose:
        print('%d%% %s -> %s (%.2fs, %d bytes); fx: %s' % (
            pct, ' '.join(map(str, words)), a.out, total, os.path.getsize(a.out),
            ', '.join(n for n, _ in fx)))
    else:
        print(pct)
    return 0


if __name__ == '__main__':
    sys.exit(main())
