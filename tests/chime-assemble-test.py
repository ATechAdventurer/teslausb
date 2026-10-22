#!/usr/bin/env python3
"""Tests for run/chime_assemble.py and run/chargechime_auto (needs audioop:
built in up to Python 3.12, `pip install audioop-lts` on 3.13+)."""
import os, subprocess, sys, tempfile, wave
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'run'))
try:
    import chime_assemble as m
except ImportError as e:
    print('SKIP chime_assemble tests:', e); sys.exit(0)
PARTS = os.path.join(ROOT, 'assets', 'ingress')
fails = 0
def check(name, got, want):
    global fails
    ok = got == want; fails += not ok
    print('PASS' if ok else 'FAIL', name, '' if ok else '%r != %r' % (got, want))

check('47 words', m.words_for(47, False), ['tesla', 'offline', 'xm_reserves', 40, 7, 'percent'])
check('75 direct', m.number_words(75), [75])
check('100 direct', m.number_words(100), [100])
check('19 critical', m.words_for(19, False)[3], 'critical')
check('20 not critical', 'critical' in m.words_for(20, False), False)
check('sentry', m.words_for(87, True)[2:4], ['turret', 'deployed'])
for n in range(1, 101):
    for w in m.words_for(n, True):
        name = 'v_' + (w if isinstance(w, str) else 'n%03d' % w)
        if not os.path.exists(os.path.join(PARTS, name + '.wav')):
            check('part exists %s' % name, False, True)
with tempfile.TemporaryDirectory() as d:
    sizes = {}
    for pct, sentry in ((1, False), (12, True), (47, False), (77, True), (100, False), (99, True)):
        out = os.path.join(d, 'c.wav')
        pcm, words, fx, total = m.assemble(pct, sentry, PARTS)
        m.write(out, pcm)
        with wave.open(out) as w:
            check('%d format' % pct, (w.getnchannels(), w.getsampwidth(), w.getframerate()), (1, 2, 44100))
        sizes[(pct, sentry)] = os.path.getsize(out)
        check('%d%s < 1MB' % (pct, sentry), os.path.getsize(out) < 1000000, True)
        check('%d peak -1dB' % pct, abs(m.audioop.max(pcm, 2) - 29195) < 40, True)
        check('%d fx' % pct, ('damage' in [n for n, _ in fx]), pct < 20)
    check('deterministic', m.assemble(47, False, PARTS)[0] == m.assemble(47, False, PARTS)[0], True)
    check('clamp', m.assemble(250, False, PARTS)[1][-2:], [100, 'percent'])

    # chargechime_auto end to end with a fake SOC reader and lockchime
    b = os.path.join(d, 'bin'); lib = os.path.join(d, 'lib'); os.makedirs(b); os.makedirs(lib)
    os.symlink(os.path.join(ROOT, 'run', 'chime_assemble.py'), os.path.join(b, 'chime_assemble.py'))
    with open(os.path.join(b, 'lockchime'), 'w') as f:
        f.write('#!/bin/bash\n[ "$1" = is-current ] && exit "${NOTCUR:-0}"\necho "$@" >> "%s/requests"\n' % d)
    os.chmod(os.path.join(b, 'lockchime'), 0o755)
    socf = os.path.join(d, 'soc')
    env = dict(os.environ, CHARGECHIME_BIN=b, LOCKCHIME_DIR=lib, CHARGECHIME_LAST=os.path.join(d, 'last'),
               CHARGECHIME_LOG=os.path.join(d, 'log'), CHARGECHIME_PARTS=PARTS,
               CHARGECHIME_SOC_CMD='cat ' + socf, PYTHONPATH='',
               CHARGECHIME_WIFI_HOP='false', CHARGECHIME_STATUS=os.path.join(d, 'status'),
               LOCKCHIME_REQUEST_FILE=os.path.join(d, 'nonexistent_req'))
    def run(soc):
        if soc is None:
            os.chmod(socf, 0o644) if os.path.exists(socf) else None; open(socf, 'w').close(); os.chmod(socf, 0)
        else:
            os.chmod(socf, 0o644) if os.path.exists(socf) else None
            open(socf, 'w').write('{"soc": %d, "voltage": 355.0}' % soc)
        return subprocess.call([os.path.join(ROOT, 'run', 'chargechime_auto')], env=env)
    def reqs():
        p = os.path.join(d, 'requests')
        return open(p).read().split('\n')[:-1] if os.path.exists(p) else []
    check('auto first', (run(58), reqs()), (0, ['request charge_chime.wav']))
    check('auto last stored', open(os.path.join(d, 'last')).read().strip(), '58')
    check('auto chime built', os.path.getsize(os.path.join(lib, 'charge_chime.wav')) > 100000, True)
    check('auto unchanged skips', (run(58), len(reqs())), (0, 1))
    check('auto changed', (run(57), len(reqs())), (0, 2))
    if os.geteuid() != 0:
        check('auto no reading', (run(None), len(reqs())), (0, 2))
        run(None)
        check('failure logged once', open(os.path.join(d, 'log')).read().count('no battery reading'), 1)
        check('recovery logged', (run(57), 'reading again (57%)' in open(os.path.join(d, 'log')).read()), (0, True))
    env['NOTCUR'] = '1'
    check('unchanged but not installed re-requests', (run(57), len(reqs())), (0, 3))
    del env['NOTCUR']
    check('auto log', 'battery 57% (was 58)' in open(os.path.join(d, 'log')).read(), True)
    check('no tmp left', sorted(os.listdir(lib)), ['charge_chime.wav'])

    # rotation never picks the generated chime
    for n in ('chime_1.wav', 'charge_chime.wav'):
        subprocess.call(['cp', os.path.join(PARTS, 'v_tesla.wav'), os.path.join(lib, n)])
    out = subprocess.run(['bash', os.path.join(ROOT, 'run', 'lockchime'), 'list'], stdout=subprocess.PIPE,
                         env=dict(os.environ, LOCKCHIME_DIR=lib, LOCKCHIME_ROTATE='false')).stdout.decode()
    check('rotation excludes charge_chime', out.split(), ['chime_1.wav'])
print('chime_assemble tests: %d failed' % fails)
sys.exit(1 if fails else 0)
