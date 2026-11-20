#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Puts RGB Hub onto a Batocera drive from another Linux PC.
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
app="$share/system/rgb-hub"

echo "==> Program files -> $app"
rm -rf "$app/service" "$app/helper"
mkdir -p "$app/service" "$app/helper"
cp -r "$repo/service/rgbhub" "$app/service/"
find "$app/service" -name __pycache__ -type d -exec rm -rf {} +
cp "$repo/helper/rgbhub_ram_helper.py" "$app/helper/"
rm -rf "$app/modules"
cp -r "$repo/frontends/batocera/modules" "$app/modules"

echo "==> Service switch (System Settings > Services > RGB_HUB)"
mkdir -p "$share/system/services"
install -m 0755 "$repo/frontends/batocera/services/rgb_hub" "$share/system/services/rgb_hub"

echo "==> Game hook"
mkdir -p "$share/system/scripts"
install -m 0755 "$repo/frontends/batocera/scripts/rgb-hub.sh" "$share/system/scripts/rgb-hub.sh"

echo "==> Sleep and wake hooks"
hooks="$share/system/configs/emulationstation/scripts"
mkdir -p "$hooks/suspend" "$hooks/resume"
install -m 0755 "$repo/frontends/batocera/power/suspend/rgb-hub.sh" "$hooks/suspend/rgb-hub.sh"
install -m 0755 "$repo/frontends/batocera/power/resume/rgb-hub.sh" "$hooks/resume/rgb-hub.sh"

echo "==> Settings file (kept if it already exists)"
mkdir -p "$share/system/configs/rgb-hub"
settings="$share/system/configs/rgb-hub/settings.json"
[[ -f "$settings" ]] || printf '{\n}\n' > "$settings"
cp "$repo/frontends/batocera/README.md" "$share/system/configs/rgb-hub/README.md"

echo
echo "Done. Boot Batocera, open Main menu > System Settings > Services,"
echo "and switch RGB_HUB on."
