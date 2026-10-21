# Rotating lock chime

Tesla cars can play a custom lock sound: a file called `LockChime.wav` in the
root of a USB drive, selected in the car under
**Toybox > Boombox > Lock Sound > USB**. teslausb can keep a library of clips
and swap a different one in as `LockChime.wav` automatically, so the car's
lock sound changes over time.

## Requirements

- A lightshow, music or boombox drive (`LIGHTSHOW_SIZE`, `MUSIC_SIZE` or
  `BOOMBOX_SIZE`). The chime is never put on the cam drive, because the car is
  constantly writing to that one.
- Clips must be 16-bit PCM WAV files of at most 1 MB (any sample rate, mono
  or stereo). Anything else is skipped and reported in the archiveloop log.

## Setup

1. Add the following to `teslausb_setup_variables.conf` and run setup again
   (or `/root/bin/setup-teslausb upgrade`):

   ```
   export LOCKCHIME_ROTATE=true
   ```

2. Put your clips in `/mutable/LockChimes` on the Pi. You can use the helper
   script, which skips anything the car can't use:

   ```
   tools/load-lockchimes.sh root@teslausb.local:/mutable/LockChimes ~/my-chimes/*.wav
   ```

   or, when run on the Pi itself, give it a local directory as the
   destination. `-n` shows what would be copied without copying anything.

3. In the car, select **Toybox > Boombox > Lock Sound > USB**.

### Ingress ADA clips

There is a set of ready-made, Tesla-ready Ingress ADA voice line clips in
`v2/`, `combos/`, `mash/` and `out/` of the Ingress clip folder
(`/workspace/ingress` on the box they were made on). They are not part of
this repo. Load all of them with:

```
tools/load-lockchimes.sh root@teslausb.local:/mutable/LockChimes --ingress /workspace/ingress
```

Each file gets its folder name as a prefix (e.g. `combos_02_portal_key_acquired.wav`)
so files with the same name in different folders don't overwrite each other.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `LOCKCHIME_ROTATE` | `false` | `true` turns rotation on |
| `LOCKCHIME_MODE` | `random` | `random` (never the same clip twice in a row), `sequential` (alphabetical order), `fixed` (always `LOCKCHIME_FIXED`) or `schedule` (see below) |
| `LOCKCHIME_FIXED` | | file name of the clip for `fixed` mode |
| `LOCKCHIME_TRIGGERS` | `boot archive` | when to rotate, any of `boot`, `archive`, `timer` |
| `LOCKCHIME_INTERVAL` | `3600` | seconds between rotations for the `timer` trigger (at least 300) |
| `LOCKCHIME_DRIVE` | `auto` | `auto` (first of lightshow, music, boombox that exists), `lightshow`, `music` or `boombox` |
| `LOCKCHIME_DIR` | `/mutable/LockChimes` | the clip library |
| `LOCKCHIME_MAX_SIZE` | `1048576` | maximum clip size in bytes |

### Schedule

With `LOCKCHIME_MODE=schedule`, `/mutable/LockChimes/schedule.txt` decides
which clip to use. Each line is `WHEN CLIP`, where `WHEN` is a date (`MM-DD`),
a day of the week (`Mon` to `Sun`) or `*`, and `CLIP` is a file name in the
library or `random`. A date match wins over a day of the week, which wins over
`*`. With no match, a random clip is used.

```
# Christmas
12-25 combos_08_welcome_back_tesla_online.wav
Fri   mash_shutdown_epic.wav
*     random
```

## Triggers

- **boot**: when teslausb starts, which normally happens when the car wakes up
  and powers its USB ports. The swap happens before the drives are connected
  to the car, so it doesn't cause an extra disconnect.
- **archive**: every time the car gets home (the archive server becomes
  reachable) and archiving runs. The swap happens right before archiveloop
  reconnects the drives after archiving.
- **timer**: every `LOCKCHIME_INTERVAL` seconds. Off by default, because each
  swap briefly disconnects the drives.
- **Web UI**: when rotation is enabled, the Tools tab has a
  **Rotate lock chime** button. From the command line, `lockchime request [CLIP]`
  does the same. Picking a specific file in the Files tab and choosing
  "Use as lock sound" still works as before, but the car won't notice it until
  the drives are reconnected.

`lockchime status` shows the current settings and the installed clip, and
`lockchime check FILE...` tells you whether files are usable.

## How the swap works, and why it doesn't affect recordings

The car caches the drive's directory, so a changed `LockChime.wav` is only
noticed after the drive is disconnected and reconnected. Changing files on a
drive while the car has it mounted can also corrupt it. So teslausb only
writes `LockChime.wav` while the drives are disconnected from the car, then
reconnects them so the car rescans:

- The `boot` and `archive` swaps happen at points where archiveloop has
  already disconnected the drives for its own reasons (filesystem check and
  cleanup), so they add no extra disconnect.
- For any other swap (web UI, timer, or an `archive` swap when the drives are
  still connected), archiveloop first waits for the car to stop writing,
  using the same idle check it uses before taking snapshots. Only then does it
  disconnect the drives with its regular disconnect function (which also
  checks the filesystems), copy the clip, and reconnect the drives.
- If the car keeps writing (for example while driving with dashcam on), the
  swap is put off and retried every 5 minutes instead of cutting a recording
  short.
- Swaps are handled inside archiveloop, never while archiving runs, so they
  can't collide with the archive or cleanup steps.
- The clip is copied under a temporary name, flushed to disk, and then renamed
  to `LockChime.wav`, so an interrupted copy never leaves a half-written chime.
- If the selected clip is already the installed one (e.g. `fixed` mode), nothing
  is written and the drives aren't disconnected.

The drive is written through whatever mount is already active (for example the
web UI's), the web UI's automount, or the regular `/mnt/<drive>` mount, so the
drive image is never mounted twice.

## Picking the chime over Bluetooth (BLE)

With `export LOCKCHIME_BLE=true` (and `LOCKCHIME_ROTATE=true`), setup installs
`/root/bin/lockchime_ble.py` as the `lockchime-ble` systemd service. It makes
the Pi a Bluetooth LE peripheral named **TeslaUSB**, no Wi-Fi needed. Setup
installs `bluez`, `python3-dbus` and `python3-gi`, and re-enables Bluetooth if
`dtoverlay=disable-bt` is in `config.txt`.

| | UUID |
|---|---|
| Service | `7e1a0001-5c3b-4d6a-9f2e-1d7c0b4a7e55` |
| Chime characteristic (read, write, write without response) | `7e1a0002-5c3b-4d6a-9f2e-1d7c0b4a7e55` |

Write a number N from 1 to 10, either as UTF-8 text (`3`) or as a single raw
byte (`0x03`), and `chime_N.wav` from `/mutable/LockChimes` becomes the lock
chime. Reading the characteristic returns the last number written. Numbers out
of range are rejected; numbers with no valid `chime_N.wav` are logged and
ignored. The upper limit is `MAX_CHIME` at the top of the script (raise it to
100 later).

The clip list is validated once at startup. After adding clips, run
`sudo systemctl reload lockchime-ble` (or restart it) so it rescans.

### Low-latency swap, and the tradeoff

A write runs `lockchime install-now chime_N.wav` straight away, which
disconnects the drives from the car, copies the clip (temp file, sync,
rename) and reconnects them, typically within a couple of seconds. Unlike the
regular swap it does **not** wait for the car to be idle (`waitforidle`), does
not go through archiveloop's request queue, and does not fsck the drives.
That means a swap during driving or Sentry recording **could cut off a dashcam
write in progress** (the clip being written may be lost or need repair later).
If archiveloop has the drives disconnected (archiving, fsck) or a snapshot is
being taken, `install-now` doesn't wait: it falls back to the queued
`lockchime request`, which archiveloop handles safely. Both sides use the lock
file `/tmp/lockchime.lock`.

### Security gap

There is no pairing or encryption, so anyone within Bluetooth range can change
your lock chime (only to a clip already in the library). Don't rely on this
for anything more sensitive.

### Testing from an iPhone

1. Install **nRF Connect** or **LightBlue** from the App Store.
2. Scan and connect to **TeslaUSB**.
3. Open the service `7E1A0001-5C3B-4D6A-9F2E-1D7C0B4A7E55`.
4. On characteristic `7E1A0002-…`, tap write (the up arrow), choose UTF-8 /
   Text, enter `3` and send.
5. On the Pi: `sudo tail /mutable/archiveloop.log` should show
   `lockchime_ble: write 3 -> chime_3.wav` and
   `lockchime: install-now chime_3.wav done (rc=0)`, and
   `sudo /root/bin/lockchime status` should show `installed: chime_3.wav`.
   Reading the characteristic returns `3`. Service logs:
   `journalctl -u lockchime-ble`.

## Things to test

- **How quickly the car notices a swapped file is unconfirmed.** It isn't
  known yet whether the car picks up the new `LockChime.wav` on every USB
  reconnect, only when it wakes up, or only when **Lock Sound > USB** is
  selected again. Check the archiveloop log for `Lock chime` lines, then lock
  the car after the reconnect and see which clip plays.
- Which drive the car reads `LockChime.wav` from when several drives are
  connected. The default is the lightshow drive; try `LOCKCHIME_DRIVE` if the
  car doesn't pick it up.

## Battery-% chime from an S3XY Commander

With `export CHARGECHIME_AUTO=true` (and `LOCKCHIME_ROTATE=true`), setup installs
`s3xy_soc.py`, `chime_assemble.py` and `chargechime_auto` into `/root/bin`, the
voice/effect parts into `/mutable/ChargeChimeParts`, and `chargechime.timer`
(2 min after boot, then every 10 min). Each run reads the battery % (whole
percent, rounded half up) from the Commander's Wi-Fi Panda stream; with no
reading (Pi not on `S3XY_OBD`, car asleep) it does nothing. When the % differs
from `/mutable/chargechime.last`, it rebuilds
`/mutable/LockChimes/charge_chime.wav` ("Tesla offline. XM reserves, [critical,]
N percent." in ADA's voice, Ingress effects) and runs
`lockchime request charge_chime.wav`, so archiveloop swaps it in the usual safe
way. `charge_chime.wav` is never picked by the random/sequential rotation, but a
later rotation (boot/archive trigger) will replace it with another clip.

The parts are pre-processed once with ffmpeg by `tools/build_chime_parts.py`
(from the Ingress 1.88 sounds); the Pi only mixes them with Python's `audioop`.
