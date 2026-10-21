#!/usr/bin/env python3
"""lockchime_ble: minimal BLE GATT peripheral to pick the lock chime.

Advertises as "TeslaUSB" with one custom service and one characteristic.
Writing a number N (UTF-8 text like "3", or a single raw byte 0x03) runs
`lockchime install-now chime_N.wav`, which swaps LockChime.wav right away.
Reading the characteristic returns the last requested number as text.

Uses only BlueZ's D-Bus API (python3-dbus, python3-gi). No pairing or
encryption: anyone in range can change the chime (see the doc).
"""

import os
import subprocess
import sys
import threading
import time

import dbus
import dbus.exceptions
import dbus.mainloop.glib
import dbus.service
from gi.repository import GLib

SERVICE_UUID = '7e1a0001-5c3b-4d6a-9f2e-1d7c0b4a7e55'
CHIME_CHAR_UUID = '7e1a0002-5c3b-4d6a-9f2e-1d7c0b4a7e55'
LOCAL_NAME = os.environ.get('LOCKCHIME_BLE_NAME', 'TeslaUSB')
MIN_CHIME = 1
MAX_CHIME = 10  # raise to 100 later
LIBRARY = os.environ.get('LOCKCHIME_DIR', '/mutable/LockChimes')
LOCKCHIME = '/root/bin/lockchime'
LOG_FILE = os.environ.get('LOG_FILE', '/mutable/archiveloop.log')

BLUEZ = 'org.bluez'
ADAPTER_IFACE = 'org.bluez.Adapter1'
GATT_MANAGER_IFACE = 'org.bluez.GattManager1'
AD_MANAGER_IFACE = 'org.bluez.LEAdvertisingManager1'
GATT_SERVICE_IFACE = 'org.bluez.GattService1'
GATT_CHRC_IFACE = 'org.bluez.GattCharacteristic1'
AD_IFACE = 'org.bluez.LEAdvertisement1'
PROPS_IFACE = 'org.freedesktop.DBus.Properties'
OM_IFACE = 'org.freedesktop.DBus.ObjectManager'


def log(msg):
    line = '%s: lockchime_ble: %s\n' % (time.strftime('%a %d %b %H:%M:%S %Z %Y'), msg)
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(line)
    except OSError:
        pass
    sys.stderr.write(line)


# Valid clip names, validated once (at startup / on SIGHUP), so the write
# path does no WAV checks.
valid_clips = set()


def load_clips():
    global valid_clips
    try:
        out = subprocess.run([LOCKCHIME, 'list'], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, check=False).stdout
        valid_clips = set(os.path.basename(l) for l in out.decode().splitlines() if l)
    except OSError as e:
        log('could not list clips: %s' % e)
    log('%d valid clips cached' % len(valid_clips))
    return True


def parse_value(data):
    raw = bytes(data)
    text = raw.decode('ascii', 'ignore').strip()
    if text.isdigit():
        return int(text)
    if len(raw) == 1:
        return raw[0]
    return None


class InvalidArgs(dbus.exceptions.DBusException):
    _dbus_error_name = 'org.bluez.Error.InvalidValueLength'


class Application(dbus.service.Object):
    def __init__(self, bus):
        self.path = '/teslausb'
        self.services = []
        dbus.service.Object.__init__(self, bus, self.path)

    @dbus.service.method(OM_IFACE, out_signature='a{oa{sa{sv}}}')
    def GetManagedObjects(self):
        resp = {}
        for s in self.services:
            resp[s.path] = s.props()
            for c in s.chrcs:
                resp[c.path] = c.props()
        return resp


class Service(dbus.service.Object):
    def __init__(self, bus, app):
        self.path = app.path + '/service0'
        self.chrcs = []
        dbus.service.Object.__init__(self, bus, self.path)

    def props(self):
        return {GATT_SERVICE_IFACE: {
            'UUID': SERVICE_UUID, 'Primary': dbus.Boolean(True),
            'Characteristics': dbus.Array([c.path for c in self.chrcs], signature='o')}}


class ChimeCharacteristic(dbus.service.Object):
    def __init__(self, bus, service):
        self.path = service.path + '/char0'
        self.service = service
        self.last = 0
        self.worker = None
        self.pending = None
        self.lock = threading.Lock()
        dbus.service.Object.__init__(self, bus, self.path)

    def props(self):
        return {GATT_CHRC_IFACE: {
            'Service': dbus.ObjectPath(self.service.path), 'UUID': CHIME_CHAR_UUID,
            'Flags': dbus.Array(['read', 'write', 'write-without-response'], signature='s')}}

    @dbus.service.method(PROPS_IFACE, in_signature='s', out_signature='a{sv}')
    def GetAll(self, interface):
        return self.props()[GATT_CHRC_IFACE]

    @dbus.service.method(GATT_CHRC_IFACE, in_signature='a{sv}', out_signature='ay')
    def ReadValue(self, options):
        return dbus.Array(str(self.last).encode(), signature='y')

    @dbus.service.method(GATT_CHRC_IFACE, in_signature='aya{sv}')
    def WriteValue(self, value, options):
        n = parse_value(value)
        if n is None or not MIN_CHIME <= n <= MAX_CHIME:
            log('ignoring invalid write %r' % bytes(value))
            raise InvalidArgs('expected %d-%d' % (MIN_CHIME, MAX_CHIME))
        clip = 'chime_%d.wav' % n
        if clip not in valid_clips:
            log('%s not in %s (or not valid), ignoring' % (clip, LIBRARY))
            return
        self.last = n
        log('write %d -> %s' % (n, clip))
        # return to BlueZ immediately; the swap runs in a thread. If a swap is
        # already running, only the newest request is kept and runs next.
        with self.lock:
            self.pending = clip
            if self.worker is None or not self.worker.is_alive():
                self.worker = threading.Thread(target=self.run, daemon=True)
                self.worker.start()

    def run(self):
        while True:
            with self.lock:
                clip, self.pending = self.pending, None
                if clip is None:
                    return
            t = time.time()
            rc = subprocess.call([LOCKCHIME, 'install-now', clip])
            log('install-now %s rc=%d in %.1fs' % (clip, rc, time.time() - t))


class Advertisement(dbus.service.Object):
    def __init__(self, bus):
        self.path = '/teslausb/advertisement0'
        dbus.service.Object.__init__(self, bus, self.path)

    @dbus.service.method(PROPS_IFACE, in_signature='s', out_signature='a{sv}')
    def GetAll(self, interface):
        return {'Type': 'peripheral',
                'ServiceUUIDs': dbus.Array([SERVICE_UUID], signature='s'),
                'LocalName': dbus.String(LOCAL_NAME)}

    @dbus.service.method(AD_IFACE)
    def Release(self):
        log('advertisement released')


def find_adapter(bus):
    om = dbus.Interface(bus.get_object(BLUEZ, '/'), OM_IFACE)
    for path, ifaces in om.GetManagedObjects().items():
        if GATT_MANAGER_IFACE in ifaces and AD_MANAGER_IFACE in ifaces:
            return path
    return None


def main():
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    adapter_path = find_adapter(bus)
    if not adapter_path:
        log('no BLE adapter found')
        return 1
    adapter = bus.get_object(BLUEZ, adapter_path)
    props = dbus.Interface(adapter, PROPS_IFACE)
    props.Set(ADAPTER_IFACE, 'Powered', dbus.Boolean(True))
    props.Set(ADAPTER_IFACE, 'Alias', dbus.String(LOCAL_NAME))
    props.Set(ADAPTER_IFACE, 'Pairable', dbus.Boolean(False))

    load_clips()
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, 1, load_clips)  # SIGHUP

    app = Application(bus)
    svc = Service(bus, app)
    svc.chrcs.append(ChimeCharacteristic(bus, svc))
    app.services.append(svc)
    adv = Advertisement(bus)

    # log connects/disconnects (BlueZ 5.50 doesn't expose the HCI reason;
    # use `sudo btmon -w /mutable/btmon.snoop` for that)
    def on_dev(iface, changed, invalidated, path=None):
        if iface == 'org.bluez.Device1' and 'Connected' in changed:
            log('%s %s' % ('connected' if changed['Connected'] else 'disconnected',
                           path.rsplit('/', 1)[-1]))
    bus.add_signal_receiver(on_dev, dbus_interface=PROPS_IFACE,
                            signal_name='PropertiesChanged', bus_name=BLUEZ,
                            path_keyword='path')

    loop = GLib.MainLoop()
    stopping = []

    def on_term():
        stopping.append(True)
        loop.quit()
        return False
    GLib.unix_signal_add(GLib.PRIORITY_HIGH, 15, on_term)  # SIGTERM

    def fail(what):
        def cb(err):
            log('%s failed: %s' % (what, err))
            loop.quit()
        return cb

    dbus.Interface(adapter, GATT_MANAGER_IFACE).RegisterApplication(
        app.path, {}, reply_handler=lambda: log('GATT service registered'),
        error_handler=fail('RegisterApplication'))
    dbus.Interface(adapter, AD_MANAGER_IFACE).RegisterAdvertisement(
        adv.path, {}, reply_handler=lambda: log('advertising as %s' % LOCAL_NAME),
        error_handler=fail('RegisterAdvertisement'))
    try:
        loop.run()
    finally:
        for mgr, method, path in ((AD_MANAGER_IFACE, 'UnregisterAdvertisement', adv.path),
                                  (GATT_MANAGER_IFACE, 'UnregisterApplication', app.path)):
            try:
                getattr(dbus.Interface(adapter, mgr), method)(path)
            except dbus.exceptions.DBusException:
                pass
    return 0 if stopping else 1  # systemd restarts us on failure


if __name__ == '__main__':
    sys.exit(main())
