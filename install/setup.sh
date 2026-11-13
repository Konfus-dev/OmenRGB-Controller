#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# One-time system setup for OmenRGB-Linux-Controller. Run once with:
#     sudo bash install/setup.sh
# It is safe to run again. Everything else in this project runs without root.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run this with sudo: sudo bash install/setup.sh" >&2
    exit 1
fi

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Installing OpenRGB (drives the fan, case and RAM lights) and Python venv support"
apt-get install -y openrgb python3-venv python3-pip

echo "==> Allowing the logged-in user to talk to the cooler LCD (USB 03f0:7397)"
install -m 0644 "$here/70-omen-lcd.rules" /etc/udev/rules.d/70-omen-lcd.rules

echo "==> Loading the i2c-dev module now and at every boot (OpenRGB needs it for RAM lights)"
echo i2c-dev > /etc/modules-load.d/i2c-dev.conf
modprobe i2c-dev

echo "==> Reloading device rules"
udevadm control --reload
udevadm trigger

echo "Done."
