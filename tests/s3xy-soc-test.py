#!/usr/bin/env python3
import os, struct, sys, threading, socket, json, io, contextlib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'run'))
import s3xy_soc as m

def leg(fid, data, bus=0):
    return struct.pack('<II', fid << 21, len(data) | (bus << 4)) + data.ljust(8, b'\0')

def v2(fid, data, bus=0):
    h = bytes([(m.DLC_TO_LEN.index(len(data)) << 4) | (bus << 1)]) + struct.pack('<I', fid << 3)
    ck = 0
    for b in h + data: ck ^= b
    return h + bytes([ck]) + data

def pack(vals):  # list of (start,len,raw) -> 8 bytes LE
    v = 0
    for st, ln, raw in vals: v |= (raw & ((1 << ln) - 1)) << st
    return v.to_bytes(8, 'little')

soc = pack([(10, 10, 473), (30, 10, 480)])            # 47.3 %
hv = pack([(0, 16, 36512), (16, 16, 125)])             # 365.12 V, -12.5 A
fails = 0
def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(('PASS' if ok else 'FAIL'), name, got if not ok else '')

for fmt, enc in (('legacy', leg), ('v2', v2)):
    frames = m.parse(enc(0x292, soc) + enc(0x132, hv) + enc(0x123, b'\1\2'), 'auto' if fmt == 'legacy' else 'packed') or m.parse(enc(0x292, soc) + enc(0x132, hv) + enc(0x123, b'\1\2'))
    st = {}
    for b, f, d in frames: m.decode(st, f, d)
    check(fmt + ' soc', st.get('soc'), 47)
    check(fmt + ' voltage', st.get('voltage'), 365.12)
    check(fmt + ' current', st.get('current'), -12.5)
    check(fmt + ' charging', st.get('charging'), True)
check('legacy skips ack', m.parse(struct.pack('<II', 6 << 21, 15 << 4) + bytes(8)), [])
check('short frame ignored', m.decode({}, 0x292, b'\1\2'), False)
check('extended id', m.parse_legacy(struct.pack('<II', (0x18DAF1 << 3) | 4, 2) + bytes(8))[0][1], 0x18DAF1)
st = {}; m.decode(st, 0x292, soc[:3]); check('short 0x292 keeps ui', (st.get('soc'), 'soc_avg' in st), (47, False))
check('round half up', [m.pct(r) for r in (575, 574, 5, 4, 1000)], [58, 57, 1, 0, 100])
st = {}; m.decode(st, 0x292, pack([(10, 10, 575), (30, 10, 485)])); check('json ints', (st['soc'], st['soc_avg'], type(st['soc'])), (58, 49, int))
check('signed', m.bits((0xffff).to_bytes(2, 'little'), 0, 16, True), -1)

# end to end against a fake Commander on localhost
srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); srv.bind(('127.0.0.1', 0))
port = srv.getsockname()[1]; seen = []
def fake():
    data, addr = srv.recvfrom(100); seen.append(data)
    srv.sendto(leg(0x292, soc) + leg(0x132, hv), addr)
threading.Thread(target=fake, daemon=True).start()
out = io.StringIO()
with contextlib.redirect_stdout(out):
    rc = m.main(['--host', '127.0.0.1', '--port', str(port), '--json', '--timeout', '3'])
check('e2e rc', rc, 0)
check('e2e only hello sent', seen, [b'ehllo'])
check('e2e soc', json.loads(out.getvalue())['soc'], 47)
with contextlib.redirect_stderr(io.StringIO()):
    check('timeout rc', m.main(['--host', '127.0.0.1', '--port', '9', '--timeout', '1']), 2)
print('s3xy tests: %d failed' % fails)
sys.exit(1 if fails else 0)
