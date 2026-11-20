#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# The one full OpenRGB hardware scan, run by install-user.sh when OpenRGB's
# list of detectors is missing or OpenRGB has been updated:
#
#     bash install/openrgb-detector-scan.sh <OpenRGB config folder> [--yes]
#
# OpenRGB only writes down the names of its detectors after running them
# all once; after that, every one but the case-light board is switched off.
# That one run probes the RAM too, which on the OMEN 45L jams the RAM's
# control bus until the next reboot. So it never happens without the
# owner's say-so: typed at the prompt, or --yes (install-user.sh passes it
# for --allow-detector-scan).
#
# This is the only place in the project that runs OpenRGB without its
# detector list. Nothing here runs as root.
set -euo pipefail

folder="${1:-}"
if [[ -z "$folder" ]]; then
    echo "Usage: bash install/openrgb-detector-scan.sh <OpenRGB config folder> [--yes]" >&2
    exit 2
fi
openrgb="${OPENRGB:-openrgb}"

cat >&2 <<'EOF'

    OpenRGB needs one full hardware scan (about 30 seconds) so that RGB Hub
    can switch off every detector except the case lights. The scan also
    probes the RAM: on the OMEN 45L that jams the RAM's control bus, so the
    RAM temperature sensors (and RAM lights) may stop until you reboot.
    Reboot once it is done.

EOF

if [[ "${2:-}" != --yes ]]; then
    if [[ ! -t 0 ]]; then
        echo "Not scanning: nobody to ask. Run the installer in a terminal, or pass" >&2
        echo "--allow-detector-scan to install-user.sh to agree in advance." >&2
        exit 1
    fi
    read -r -p "Type 'scan' to run it now, anything else to stop: " answer
    if [[ "$answer" != scan ]]; then
        echo "Stopped; nothing was scanned." >&2
        exit 1
    fi
fi

timeout 120 "$openrgb" --config "$folder" --noautoconnect --list-devices >/dev/null 2>&1 || true
