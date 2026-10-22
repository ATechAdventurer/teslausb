#!/usr/bin/env python3
"""One-time (box/desktop, needs ffmpeg): turn the Ingress 1.88 .ogg clips into
the pre-processed 16-bit mono 44.1 kHz WAV parts that run/chime_assemble.py
mixes on the Pi without ffmpeg. Mirrors Random Thought bot's charge_chime.py v3:
per-word silence trim + voice chain (highpass, compressor, echo), the "Tesla"
glitch stutter, and every effect with its trim/reverse/sub-drop, fade, gain and
the fx-bus EQ baked in.

usage: build_chime_parts.py SOUND_DIR OUT_DIR"""
import os, subprocess, sys

src, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
NUMS = list(range(1, 21)) + [25, 30, 40, 50, 60, 70, 75, 80, 90, 100]
WORDS = ['tesla', 'offline', 'turret', 'deployed', 'xm_reserves', 'critical', 'percent']
VOICE = ('highpass=f=100,acompressor=threshold=-20dB:ratio=3:attack=5:release=80,'
         'aecho=0.8:0.5:70|140:0.22|0.12')
TRIM = ('aresample=44100,aformat=channel_layouts=mono,'
        'silenceremove=start_periods=1:start_threshold=-50dB,areverse,'
        'silenceremove=start_periods=1:start_threshold=-50dB,areverse')
# per-word post chain: 'tesla' sits under the failed/power-down fx, so lift it
# and add presence (2-5 kHz) for clarity
POST = {'tesla': 'equalizer=f=3500:t=q:w=0.8:g=4,volume=4dB,alimiter=limit=0.89'}
FXEQ = 'equalizer=f=2800:t=q:w=1.2:g=-4'
# name: (source, gain dB, trim start, length or None, extra pre-filter)
FX = {
    'major_action_failed': ('sfx_major_action_failed', -2, 0, None, ''),
    'power_down': ('sfx_mod_power_down', -6, 0.35, 2.0, ''),
    'portal_ping': ('sfx_portal_ping', -3, 0, None, ''),
    'xm_pickup': ('sfx_xm_pickup', -6, 0, None, ''),
    'capsule_close': ('sfx_capsule_close', -5, 0, None, ''),
    'hack_reverse': ('sfx_hack_popout', -8, 0.95, None, 'areverse,'),
    'sub_drop': ('sfx_mod_power_down', -4, 0.3, 2.2, 'asetrate=44100*0.5,aresample=44100,lowpass=f=400,'),
    'whale': ('sfx_ambient_neutral_whale_alt', -2, 0, None, ''),
    'wraith': ('sfx_ambient_alien_wraith_alt', -6, 0.8, 2.4, ''),
    'calculating': ('sfx_glyphgame_calculating_score', -14, 0, 0.9, ''),
    'resonator_hum': ('sfx_resonator_power_up', -12, 0, 1.6, ''),
    'target_lock': ('sfx_zoom_acquire_target', -5, 0, None, ''),
    'mod_deploy': ('sfx_mod_deploy', -3, 0, None, ''),
    'damage': ('sfx_resonator_damage_high', -6, 0, None, ''),
    'ui_fail': ('sfx_ui_fail', -6, 0, None, ''),
}


def ff(args):
    subprocess.run(['ffmpeg', '-v', 'error', '-y'] + args, check=True)


def wav(path):
    return ['-ac', '1', '-ar', '44100', '-sample_fmt', 's16', '-fflags', '+bitexact',
            '-map_metadata', '-1', path]


for w in WORDS + NUMS:
    name = w if isinstance(w, str) else 'n%03d' % w
    s = '%s/speech_%s.ogg' % (src, w) if isinstance(w, str) else '%s/speech_number_%03d.ogg' % (src, w)
    if w == 'tesla':
        fc = ('[0:a]%s,asplit=3[a][b][c];[a]atrim=0:0.09,asetpts=PTS-STARTPTS,apad=pad_dur=0.04[g];'
              '[b]atrim=0:0.09,asetpts=PTS-STARTPTS,asetrate=44100*0.94,aresample=44100[g2];'
              '[g][g2][c]concat=n=3:v=0:a=1,%s,%s[o]' % (TRIM, VOICE, POST['tesla']))
        ff(['-i', s, '-filter_complex', fc, '-map', '[o]'] + wav('%s/v_%s.wav' % (out, name)))
    else:
        ff(['-i', s, '-af', '%s,%s' % (TRIM, VOICE)] + wav('%s/v_%s.wav' % (out, name)))

for name, (fn, db, ss, ln, pre) in FX.items():
    tr = ('atrim=%s:%s,asetpts=PTS-STARTPTS,afade=t=out:st=%s:d=0.3,' % (ss, ss + ln, ln - 0.3) if ln
          else ('atrim=start=%s,asetpts=PTS-STARTPTS,' % ss if ss else ''))
    ff(['-i', '%s/%s.ogg' % (src, fn), '-af',
        'aresample=44100,aformat=channel_layouts=mono,%s%svolume=%sdB,%s' % (pre, tr, db, FXEQ)]
       + wav('%s/fx_%s.wav' % (out, name)))

# background bed, long enough for the longest chime; faded at assembly time
ff(['-i', '%s/sfx_ambient_scanner_base.ogg' % src, '-af',
    'aresample=44100,aformat=channel_layouts=mono,atrim=start=2.0,asetpts=PTS-STARTPTS,'
    'aloop=loop=-1:size=2e6,atrim=0:11.5,lowpass=f=2500,volume=-17dB,afade=t=in:d=0.5,' + FXEQ]
   + wav('%s/bed.wav' % out))
print('parts written to', out)
