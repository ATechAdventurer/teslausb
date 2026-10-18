#!/bin/bash -eu
#
# Copy lock chime clips into teslausb's clip library, skipping anything that
# isn't a usable lock chime (16-bit PCM WAV, at most 1 MB).
#
# Usage:
#   load-lockchimes.sh [-n] DEST SOURCE...
#   load-lockchimes.sh [-n] DEST --ingress [INGRESS_DIR]
#
# DEST is a local directory (e.g. /mutable/LockChimes when run on the Pi) or
# an scp destination such as root@teslausb.local:/mutable/LockChimes.
# SOURCE is any mix of .wav files and directories containing .wav files.
# --ingress loads the ready-made Ingress ADA clips from the v2, combos, mash
# and out folders of INGRESS_DIR (default /workspace/ingress), prefixing each
# file name with its folder name to avoid collisions.
# -n only shows what would be copied.

here="$(cd "$(dirname "$0")" && pwd)"
readonly here
lockchime="$here/../run/lockchime"
if [ ! -x "$lockchime" ]
then
  lockchime=/root/bin/lockchime
fi

dryrun=false
if [ "${1:-}" = "-n" ]
then
  dryrun=true
  shift
fi

if (( $# < 2 ))
then
  sed -n '/^# Usage:/,/^# -n/p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
fi

readonly dest="$1"
shift

# list "source-path<TAB>destination-name" pairs
function list_sources {
  if [ "$1" = "--ingress" ]
  then
    local -r ingress="${2:-/workspace/ingress}"
    local sub f
    for sub in v2 combos mash out
    do
      [ -d "$ingress/$sub" ] || continue
      while IFS= read -r -d '' f
      do
        printf '%s\t%s\n' "$f" "${sub}_${f##*/}"
      done < <(find "$ingress/$sub" -maxdepth 1 -type f -iname '*.wav' ! -name '.*' -print0 | sort -z)
    done
    return
  fi
  local src f
  for src in "$@"
  do
    if [ -d "$src" ]
    then
      while IFS= read -r -d '' f
      do
        printf '%s\t%s\n' "$f" "${f##*/}"
      done < <(find "$src" -maxdepth 1 -type f -iname '*.wav' ! -name '.*' -print0 | sort -z)
    else
      printf '%s\t%s\n' "$src" "${src##*/}"
    fi
  done
}

staging=$(mktemp -d)
trap 'rm -rf "$staging"' EXIT

declare -i good=0 bad=0
while IFS=$'\t' read -r src name
do
  if [ "${name,,}" = "lockchime.wav" ]
  then
    echo "skipping $src: LockChime.wav is the name of the installed chime, rename it"
    bad+=1
    continue
  fi
  if reason=$("$lockchime" check "$src" 2>&1)
  then
    echo "adding   $name"
    cp "$src" "$staging/$name"
    good+=1
  else
    echo "skipping ${reason}"
    bad+=1
  fi
done < <(list_sources "$@")

echo "$good clip(s) to copy, $bad skipped"
if [ "$dryrun" = "true" ] || (( good == 0 ))
then
  exit 0
fi

if [[ "$dest" == *:* ]]
then
  scp -p "$staging"/*.wav "$dest/"
else
  mkdir -p "$dest"
  cp -p "$staging"/*.wav "$dest/"
fi
echo "copied $good clip(s) to $dest"
