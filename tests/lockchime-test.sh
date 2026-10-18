#!/bin/bash -eu
#
# Tests for run/lockchime and the lock chime functions in run/archiveloop.
# Runs anywhere (no Pi, root or USB gadget needed); needs python3 to create
# test WAV files.

here="$(cd "$(dirname "$0")" && pwd)"
readonly here
readonly lockchime="$here/../run/lockchime"
readonly archiveloop="$here/../run/archiveloop"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

export LOCKCHIME_ROTATE=true
export LOCKCHIME_DIR="$work/lib"
export LOCKCHIME_TARGET_DIR="$work/drive"
export LOCKCHIME_REQUEST_FILE="$work/lockchime_request"
unset LOCKCHIME_MODE LOCKCHIME_FIXED LOCKCHIME_MAX_SIZE
mkdir -p "$LOCKCHIME_DIR" "$LOCKCHIME_TARGET_DIR"

declare -i failures=0 passes=0
function pass { passes+=1; }
function fail { echo "FAIL: $*"; failures+=1; }
function expect_ok { if "$@" > /dev/null 2>&1; then pass; else fail "expected success: $*"; fi; }
function expect_fail { if "$@" > /dev/null 2>&1; then fail "expected failure: $*"; else pass; fi; }
function expect_eq { if [ "$1" = "$2" ]; then pass; else fail "$3: expected '$2', got '$1'"; fi; }

# mkwav NAME [sampwidth] [seconds] [extra]: create a test file
function mkwav {
  python3 - "$@" << 'PY'
import struct, sys, wave
name = sys.argv[1]
width = int(sys.argv[2]) if len(sys.argv) > 2 else 2
secs = float(sys.argv[3]) if len(sys.argv) > 3 else 0.2
extra = sys.argv[4] if len(sys.argv) > 4 else ""
rate = 44100
frames = int(rate * secs)
if extra == "float":
    data = b"\0" * (frames * 4)
    fmt = struct.pack("<HHIIHH", 3, 1, rate, rate * 4, 4, 32)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    open(name, "wb").write(b"RIFF" + struct.pack("<I", len(body)) + body)
elif extra == "listfirst":
    # a LIST chunk (odd length, padded) before the fmt chunk
    data = b"\0" * (frames * 2)
    lst = b"INFOISFT\x05\0\0\0test\0"
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate * 2, 2, 16)
    body = (b"WAVE" + b"LIST" + struct.pack("<I", len(lst)) + lst + b"\0" +
            b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data)
    open(name, "wb").write(b"RIFF" + struct.pack("<I", len(body)) + body)
else:
    w = wave.open(name, "wb")
    w.setnchannels(1)
    w.setsampwidth(width)
    w.setframerate(rate)
    w.writeframes(b"\0" * (frames * width))
    w.close()
PY
}

lib="$LOCKCHIME_DIR"
mkwav "$lib/a.wav"
mkwav "$lib/b.wav"
mkwav "$lib/c with space.wav"
mkwav "$lib/d_list.wav" 2 0.2 listfirst
mkwav "$work/8bit.wav" 1
mkwav "$work/float.wav" 4 0.2 float
mkwav "$work/big.wav" 2 13      # ~1.1 MB
echo "not a wav" > "$work/text.wav"
cp "$work/8bit.wav" "$lib/e_8bit.wav"
cp "$work/big.wav" "$lib/f_big.wav"
cp "$lib/a.wav" "$lib/._a.wav"   # macOS resource fork file, must be ignored

# validation
expect_ok "$lockchime" check "$lib/a.wav"
expect_ok "$lockchime" check "$lib/d_list.wav"
expect_fail "$lockchime" check "$work/8bit.wav"
expect_fail "$lockchime" check "$work/float.wav"
expect_fail "$lockchime" check "$work/big.wav"
expect_fail "$lockchime" check "$work/text.wav"
expect_fail "$lockchime" check "$work/missing.wav"
expect_ok env LOCKCHIME_MAX_SIZE=2000000 "$lockchime" check "$work/big.wav"

# list only shows valid, non-hidden clips
expect_eq "$("$lockchime" list | tr '\n' '|')" "a.wav|b.wav|c with space.wav|d_list.wav|" "list"

# install + is-current
expect_ok "$lockchime" install "$lib/c with space.wav"
expect_ok cmp "$lib/c with space.wav" "$LOCKCHIME_TARGET_DIR/LockChime.wav"
expect_ok "$lockchime" is-current "$lib/c with space.wav"
expect_fail "$lockchime" is-current "$lib/a.wav"
expect_fail "$lockchime" install "$work/8bit.wav"
expect_eq "$(find "$LOCKCHIME_TARGET_DIR" -name '.*' | wc -l)" "0" "no temp files left behind"

# random never repeats the installed clip
for _ in {1..30}
do
  p=$("$lockchime" pick)
  if [ "$p" = "$lib/c with space.wav" ]; then fail "random picked the installed clip"; break; fi
done
pass

# sequential
expect_eq "$(LOCKCHIME_MODE=sequential "$lockchime" pick)" "$lib/d_list.wav" "sequential after c"
"$lockchime" install "$lib/d_list.wav" > /dev/null 2>&1
expect_eq "$(LOCKCHIME_MODE=sequential "$lockchime" pick)" "$lib/a.wav" "sequential wraps"

# fixed, including fallback for an invalid fixed clip
expect_eq "$(LOCKCHIME_MODE=fixed LOCKCHIME_FIXED=b.wav "$lockchime" pick)" "$lib/b.wav" "fixed"
p=$(LOCKCHIME_MODE=fixed LOCKCHIME_FIXED=e_8bit.wav "$lockchime" pick 2> /dev/null)
case "$p" in
  "$lib/a.wav"|"$lib/b.wav"|"$lib/c with space.wav") pass ;;
  *) fail "invalid fixed clip should fall back to a valid random clip, got $p" ;;
esac

# explicit request overrides the mode, and path components are stripped
expect_eq "$(LOCKCHIME_MODE=fixed LOCKCHIME_FIXED=b.wav "$lockchime" pick ../../a.wav)" "$lib/a.wav" "explicit pick"

# schedule: date beats weekday beats "*"
printf '# comment\n* a.wav\n%s b.wav\n%s c with space.wav\n' "$(date +%a)" "$(date +%m-%d)" > "$lib/schedule.txt"
expect_eq "$(LOCKCHIME_MODE=schedule "$lockchime" pick)" "$lib/c with space.wav" "schedule date"
printf '* a.wav\n%s b.wav\n' "$(date +%a | tr '[:upper:]' '[:lower:]')" > "$lib/schedule.txt"
expect_eq "$(LOCKCHIME_MODE=schedule "$lockchime" pick)" "$lib/b.wav" "schedule weekday"
printf '01-01 b.wav\n* a.wav\n' > "$lib/schedule.txt"
if [ "$(date +%m-%d)" != "01-01" ]
then
  expect_eq "$(LOCKCHIME_MODE=schedule "$lockchime" pick)" "$lib/a.wav" "schedule default"
fi
rm "$lib/schedule.txt"

# empty library
expect_fail env LOCKCHIME_DIR="$work/empty" "$lockchime" pick

# --- archiveloop integration: run rotate_lock_chime/poll_lock_chime with stubs
stubs="$work/stubs"
mkdir -p "$stubs"
ln -s "$lockchime" "$stubs/lockchime"
cat > "$stubs/waitforidle" << 'EOF'
#!/bin/bash
echo waitforidle >> "$CALLS"
[ "${IDLE:-yes}" = "yes" ]
EOF
chmod +x "$stubs/waitforidle"

export CALLS="$work/calls"
export LOG_FILE="$work/archiveloop.log"
function log { echo "$*" >> "$LOG_FILE"; }
export -f log
function disconnect_usb_drives_from_host { echo disconnect >> "$CALLS"; connected=no; }
function connect_usb_drives_to_host { echo connect >> "$CALLS"; connected=yes; }

# extract the lock chime functions from archiveloop, pointing them at the stubs
# and at private state files
fns=$(sed -n '/^readonly LOCKCHIME_PENDING=/,/^function convert_seconds_to_nice_time/p' "$archiveloop" | sed '$d' |
      sed -e "s|/root/bin/|$stubs/|g" -e "s|/tmp/lockchime_|$work/lockchime_|g")
eval "$fns"
function usb_drives_are_connected { [ "$connected" = "yes" ]; }

function calls { if [ -e "$CALLS" ]; then tr '\n' ' ' < "$CALLS"; fi; rm -f "$CALLS"; }

# boot: drives not connected yet, so no extra disconnect/reconnect
connected=no
LOCKCHIME_MODE=fixed LOCKCHIME_FIXED=a.wav rotate_lock_chime_on boot
expect_eq "$(calls)" "" "boot swap with drives disconnected"
expect_ok "$lockchime" is-current "$lib/a.wav"

# trigger not enabled
LOCKCHIME_TRIGGERS="archive" rotate_lock_chime_on boot
expect_eq "$(calls)" "" "disabled trigger"

# drives connected and car idle: wait, disconnect, swap, reconnect
connected=yes
LOCKCHIME_MODE=fixed LOCKCHIME_FIXED=b.wav rotate_lock_chime_on archive
expect_eq "$(calls)" "waitforidle disconnect connect " "swap with drives connected"
expect_ok "$lockchime" is-current "$lib/b.wav"

# same clip again: nothing to do, no reconnect
LOCKCHIME_MODE=fixed LOCKCHIME_FIXED=b.wav rotate_lock_chime_on archive
expect_eq "$(calls)" "" "no-op when clip already installed"

# car busy: defer, don't disconnect
IDLE=no rotate_lock_chime request a.wav
expect_eq "$(calls)" "waitforidle " "deferred when car is writing"
expect_ok test -e "$work/lockchime_pending"
expect_ok "$lockchime" is-current "$lib/b.wav"

# deferred swap is retried after 5 minutes
echo $(( $(uptime_seconds) - 301 )) > "$work/lockchime_last"
poll_lock_chime
expect_eq "$(calls)" "waitforidle disconnect connect " "deferred retry"
expect_ok "$lockchime" is-current "$lib/a.wav"
expect_fail test -e "$work/lockchime_pending"

# web UI request
"$lockchime" request "c with space.wav" > /dev/null
poll_lock_chime
expect_eq "$(calls)" "waitforidle disconnect connect " "web request"
expect_ok "$lockchime" is-current "$lib/c with space.wav"

# timer
export LOCKCHIME_TRIGGERS="boot archive timer" LOCKCHIME_INTERVAL=600
echo $(( $(uptime_seconds) - 10 )) > "$work/lockchime_last"
poll_lock_chime
expect_eq "$(calls)" "" "timer not due"
echo $(( $(uptime_seconds) - 601 )) > "$work/lockchime_last"
poll_lock_chime
expect_eq "$(calls)" "waitforidle disconnect connect " "timer due"

# request refused when rotation is disabled
expect_fail env LOCKCHIME_ROTATE=false "$lockchime" request

echo "lockchime tests: $passes passed, $failures failed"
(( failures == 0 ))
