#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Batocera runs this just before the computer sleeps. It tells OMEN Lights
# to switch the LCD and lights off, and waits (3 seconds at most) until
# that is done, so nothing is left glowing while the PC sleeps.

pidfile=/var/run/omen-lights.pid
asleep=/var/run/omen-lights/asleep

[ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null || exit 0
rm -f "$asleep"
kill -USR1 "$(cat "$pidfile")"
for _ in $(seq 30); do
    [ -e "$asleep" ] && break
    sleep 0.1
done
exit 0
