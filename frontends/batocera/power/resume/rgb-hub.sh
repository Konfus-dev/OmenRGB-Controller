#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Batocera runs this after the computer wakes. It tells RGB Hub to
# switch the LCD and lights back on.

pidfile=/var/run/rgb-hub.pid
ours() { [ -f "$pidfile" ] && grep -qs rgbhub "/proc/$(cat "$pidfile")/cmdline"; }
ours && kill -USR2 "$(cat "$pidfile")" 2>/dev/null
exit 0
