#! /bin/bash

shopt -s globstar nullglob extglob

# print shellcheck version so we know what Github uses
shellcheck -V

rc=0

# SC1091 - Don't complain about not being able to find files that don't exist.
shellcheck --exclude=SC1091 \
           ./setup/pi/setup-teslausb \
           ./pi-gen-sources/00-teslausb-tweaks/files/rc.local \
           ./run/archiveloop \
           ./run/auto.teslausb \
           ./run/awake_start \
           ./run/awake_stop \
           ./run/lockchime \
           ./run/mountimage \
           ./run/mountoptsforimage \
           ./run/remountfs_rw \
           ./run/send-push-message \
           ./run/temperature_monitor \
           ./run/waitforidle \
           ./teslausb-www/html/cgi-bin/lockchime.sh \
           ./tools/load-lockchimes.sh \
           ./tests/lockchime-test.sh || rc=$?

# BLE lock chime service (Python)
python3 -m py_compile ./run/lockchime_ble.py || rc=$?
rm -rf ./run/__pycache__

# lock chime selection/validation tests (no Pi needed)
./tests/lockchime-test.sh || rc=$?

exit $rc
