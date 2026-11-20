#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Tells RGB Hub which game is running. Batocera runs every script in
# /userdata/system/scripts like this:
#   <script> gameStart|gameStop <system> <emulator> <core> <rom path>

now_playing=/var/run/rgb-hub/now-playing
mkdir -p "$(dirname "$now_playing")"

case "$1" in
    gameStart) printf '%s\n' "$5" >"$now_playing" ;;
    gameStop) : >"$now_playing" ;;
    *) exit 0 ;;
esac

# Nudge the service so it looks at the file straight away.
pidfile=/var/run/rgb-hub.pid
ours() { [ -f "$pidfile" ] && grep -qs rgbhub "/proc/$(cat "$pidfile")/cmdline"; }
ours && kill -HUP "$(cat "$pidfile")" 2>/dev/null
exit 0
