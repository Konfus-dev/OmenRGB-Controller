#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Puts OMEN Lights onto a Batocera drive from another Linux PC.
#
#     sudo bash install/install-batocera.sh /path/where/SHARE/is/mounted
#
# (SHARE is the Batocera data partition; inside Batocera it is /userdata.)
# Needs root because Batocera's system folder is owned by root.
# Safe to run again; your settings.json is never overwritten.
set -euo pipefail

share="${1:-}"
if [[ -z "$share" || ! -d "$share/system" ]]; then
    echo "Usage: sudo bash install/install-batocera.sh <mounted SHARE partition>" >&2
    echo "       (the folder that contains 'system', 'roms' and 'bios')" >&2
    exit 1
fi

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
app="$share/system/omen-lights"

echo "==> Program files -> $app"
rm -rf "$app/service" "$app/helper"
mkdir -p "$app/service" "$app/helper"
cp -r "$repo/service/omenrgb" "$app/service/"
find "$app/service" -name __pycache__ -type d -exec rm -rf {} +
cp "$repo/helper/omenrgb_ram_helper.py" "$app/helper/"
rm -rf "$app/modules"
cp -r "$repo/frontends/batocera/modules" "$app/modules"

echo "==> Service switch (System Settings > Services > OMEN_LIGHTS)"
mkdir -p "$share/system/services"
install -m 0755 "$repo/frontends/batocera/services/omen_lights" "$share/system/services/omen_lights"

echo "==> Game hook"
mkdir -p "$share/system/scripts"
install -m 0755 "$repo/frontends/batocera/scripts/omen-lights.sh" "$share/system/scripts/omen-lights.sh"

echo "==> Sleep and wake hooks"
hooks="$share/system/configs/emulationstation/scripts"
mkdir -p "$hooks/suspend" "$hooks/resume"
install -m 0755 "$repo/frontends/batocera/power/suspend/omen-lights.sh" "$hooks/suspend/omen-lights.sh"
install -m 0755 "$repo/frontends/batocera/power/resume/omen-lights.sh" "$hooks/resume/omen-lights.sh"

echo "==> Settings file (kept if it already exists)"
mkdir -p "$share/system/configs/omen-lights"
settings="$share/system/configs/omen-lights/settings.json"
[[ -f "$settings" ]] || printf '{\n}\n' > "$settings"
cp "$repo/frontends/batocera/README.md" "$share/system/configs/omen-lights/README.md"

echo
echo "Done. Boot Batocera, open Main menu > System Settings > Services,"
echo "and switch OMEN_LIGHTS on."
