#!/usr/bin/env python3
"""Read battery state from an Enhauto S3XY Commander over Wi-Fi (Panda UDP).

Read-only: the only thing ever sent is the 'ehllo' keepalive datagram; no CAN
frames are transmitted and no filters are configured. The Pi must be on the
Commander's AP (SSID S3XY_OBD, Commander at 192.168.4.1) and the car awake.

  s3xy_soc.py                 print SOC once a frame arrives (default --once)
  s3xy_soc.py --json          JSON with soc/voltage/current/charging
  s3xy_soc.py --watch         keep printing updates
Exit 0 on success, 2 on timeout (Commander unreachable / car asleep).

Protocol and bit layouts follow V's TeslaRead tool (src/panda.ts, can.ts,
decode.ts): kick "ehllo" to UDP 1338 every 3 s from an ephemeral port, legacy
16-byte panda records, bus 0. Decoding (Model 3 community DBC, little-endian):
  0x33A UI display SOC: 20|7 % (what the car's screen shows)
  0x292 ID292BMS_SOC: SOCUI292 10|10 x0.1 %, SOCave292 30|10 x0.1 %
  0x132 ID132HVBattAmpVolt: BattVoltage132 0|16 x0.01 V,
        SmoothBattCurrent132 16|16 signed x-0.1 A (negative = into pack)
  0x352 ID352BMS_energyStatus: nominalFullPack 0|11, nominalRemaining 11|11 x0.1 kWh
        (multiplexed on some newer cars; only used if values look sane)
"""
import argparse
import json
import os
import socket
import struct
import sys
import time

HOST, PORT = '192.168.4.1', 1338
HELLO = b'ehllo'
HELLO_EVERY = 3.0
DLC_TO_LEN = [0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64]


def bits(data, start, length, signed=False):
    """Intel bitfield; None if the (possibly short) frame doesn't cover it."""
    if start + length > len(data) * 8:
        return None
    v = (int.from_bytes(bytes(data), 'little') >> start) & ((1 << length) - 1)
    if signed and v & (1 << (length - 1)):
        v -= 1 << length
    return v


def parse_legacy(buf):
    """Legacy panda: 16-byte records (id<<21 | ..., len | bus<<4, 8 data bytes)."""
    out = []
    if len(buf) == 0 or len(buf) % 16:
        return None
    for o in range(0, len(buf), 16):
        w0, w1 = struct.unpack_from('<II', buf, o)
        fid = w0 >> 3 if w0 & 4 else w0 >> 21   # bit 2 = extended id
        ln, bus = w1 & 0x0F, (w1 >> 4) & 0xFF
        if ln > 8:
            return None
        if fid == 0 or bus == 15:   # bus 15 = panda ack/heartbeat
            continue
        out.append((bus, fid, buf[o + 8:o + 8 + ln]))
    return out


def parse_v2(buf):
    """Newer panda CANPacket: 6-byte header + payload, XOR checksum in byte 5."""
    out, o = [], 0
    while o + 6 <= len(buf):
        h = buf[o:o + 6]
        ln = DLC_TO_LEN[h[0] >> 4]
        if o + 6 + ln > len(buf):
            return None
        data = buf[o + 6:o + 6 + ln]
        w = struct.unpack_from('<I', h, 1)[0]
        out.append(((h[0] >> 1) & 7, w >> 3, data))
        o += 6 + ln
    return out if o == len(buf) and out else None


def parse(buf, fmt='auto'):
    if fmt == 'legacy':
        return parse_legacy(buf) or []
    if fmt == 'packed':
        return parse_v2(buf) or []
    r = parse_legacy(buf)
    return r if r is not None else (parse_v2(buf) or [])


def pct(tenths):
    """Whole percent from a 0.1 % raw value, rounding half up (57.5 -> 58)."""
    return (tenths + 5) // 10


def decode(state, fid, d):
    """Update state from one frame; True if anything was decoded."""
    if fid == 0x292:            # BMS_socStatus
        ui, avg = bits(d, 10, 10), bits(d, 30, 10)
        if ui is None:
            return False
        state['soc_bms'] = pct(ui)
        if state.get('soc_src') != 'display':   # fallback until 0x33A shows up
            state['soc'], state['soc_src'] = pct(ui), 'bms'
        if avg is not None:
            state['soc_avg'] = pct(avg)
    elif fid == 0x33A:          # UI_range: on-screen battery %, bits 20|7
        ui = bits(d, 20, 7)        # (opendbc's 56|7 doesn't match a 2023 M3)
        if ui is None or ui > 100:
            return False
        state['soc'], state['soc_src'] = ui, 'display'
    elif fid == 0x132:          # BMS_HVBatAmpVolt
        v, a = bits(d, 0, 16), bits(d, 16, 16, True)
        if v is None or a is None:
            return False
        state['voltage'] = round(v * 0.01, 2)
        state['current'] = round(a * -0.1, 1)
        state['charging'] = state['current'] < -1.0   # negative = into the pack
    elif fid == 0x352:          # BMS_energyStatus
        full, rem = bits(d, 0, 11), bits(d, 11, 11)
        if full is None or rem is None:
            return False
        full, rem = full * 0.1, rem * 0.1
        # 0x352 is multiplexed on newer cars (2023 M3 gave 102.7/19.3 at 57.5%);
        # only trust it when it agrees with the SOC
        soc = state.get('soc')
        if 40 < full < 90 and 0 <= rem <= full and soc is not None and abs(rem / full * 100 - soc) < 8:
            state['kwh_full'], state['kwh_remaining'] = round(full, 1), round(rem, 1)
    else:
        return False
    return True


def run(host, port, timeout, watch, cb, bus=None, fmt='auto'):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(0.5)
    s.bind(('', 0))   # ephemeral port; binding 1338 makes the Commander go silent
    state, last_hello, start, got, last_print = {}, 0.0, time.time(), False, 0.0
    try:
        while True:
            now = time.time()
            if now - last_hello >= HELLO_EVERY:
                try:
                    s.sendto(HELLO, (host, port))
                except OSError:
                    pass   # no route yet; keep trying until timeout
                last_hello = now
            if not got and now - start > timeout:
                return None
            try:
                buf, addr = s.recvfrom(65535)
            except (socket.timeout, OSError):
                continue
            if addr[0] != host:
                continue
            for b, fid, data in parse(buf, fmt):
                if bus is not None and b != bus:
                    continue
                if decode(state, fid, data) and 'soc' in state:
                    got = True
                    state['ts'] = int(time.time())
                    if not watch:
                        # give 0x132 a moment to show up too
                        # wait for the on-screen % (0x33A); 0x292 only if it never comes
                        if state.get('soc_src') == 'display' and 'voltage' in state:
                            return state
                    elif time.time() - last_print >= 1:
                        last_print = time.time()
                        cb(state)
            if got and not watch and time.time() - start > timeout:
                return state
    finally:
        s.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--host', default=HOST)
    ap.add_argument('--port', type=int, default=PORT)
    ap.add_argument('--timeout', type=float, default=15)
    ap.add_argument('--bus', type=int, default=0, help='CAN bus to read, -1 = all (default 0)')
    ap.add_argument('--format', choices=('auto', 'legacy', 'packed'), default='legacy')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--once', action='store_true', help='(default) exit after first reading')
    ap.add_argument('--watch', action='store_true')
    a = ap.parse_args(argv)
    show = (lambda st: print(json.dumps(st), flush=True)) if a.json else \
           (lambda st: print(st['soc'], flush=True))
    try:
        st = run(a.host, a.port, a.timeout, a.watch, show,
                 None if a.bus < 0 else a.bus, a.format)
    except (BrokenPipeError, KeyboardInterrupt):
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except OSError:
            pass
        return 0
    if not st:
        sys.stderr.write('s3xy_soc: no battery frames from %s:%d within %ds '
                         '(not on S3XY_OBD, Commander Wi-Fi off, or car asleep)\n'
                         % (a.host, a.port, a.timeout))
        return 2
    show(st)
    return 0


if __name__ == '__main__':
    sys.exit(main())
