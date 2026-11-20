#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Batocera runs this just before the computer sleeps. It tells RGB Hub
# to switch the LCD and lights off, and waits (3 seconds at most) until
# that is done, so nothing is left glowing while the PC sleeps.

pidfile=/var/run/rgb-hub.pid
asleep=/var/run/rgb-hub/asleep

# Only signal the number in the file if it is still our program; after a
# crash it could belong to something else, which the signal would end.
ours() { [ -f "$pidfile" ] && grep -qs rgbhub "/proc/$(cat "$pidfile")/cmdline"; }
ours || exit 0
rm -f "$asleep"
kill -USR1 "$(cat "$pidfile")"
for _ in $(seq 30); do
    [ -e "$asleep" ] && break
    sleep 0.1
done
exit 0
