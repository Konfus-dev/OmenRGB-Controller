#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Batocera runs this after the computer wakes. It tells OMEN Lights to
# switch the LCD and lights back on.

pidfile=/var/run/omen-lights.pid
[ -f "$pidfile" ] && kill -USR2 "$(cat "$pidfile")" 2>/dev/null
exit 0
