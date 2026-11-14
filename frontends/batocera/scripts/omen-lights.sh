#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Tells OMEN Lights which game is running. Batocera runs every script in
# /userdata/system/scripts like this:
#   <script> gameStart|gameStop <system> <emulator> <core> <rom path>

now_playing=/var/run/omen-lights/now-playing
mkdir -p "$(dirname "$now_playing")"

case "$1" in
    gameStart) printf '%s\n' "$5" >"$now_playing" ;;
    gameStop) : >"$now_playing" ;;
esac
