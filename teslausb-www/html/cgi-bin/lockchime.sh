#!/bin/bash

# Ask archiveloop to swap in a new lock chime. archiveloop does the actual
# swap and reconnects the drives once the car isn't writing to them.
# An optional query string names the clip to use, otherwise LOCKCHIME_MODE
# decides.

clip="${QUERY_STRING//+/ }"
clip="$(echo -e "${clip//%/\\x}")"
clip="${clip##*/}"

cat << EOF
HTTP/1.0 200 OK
Content-type: text/plain

EOF
if result=$(sudo /root/bin/lockchime request "$clip" 2>&1)
then
  echo "OK"
else
  echo "FAILED: $result"
fi
