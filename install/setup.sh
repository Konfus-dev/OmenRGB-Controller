#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# One-time system setup for OmenRGB-Linux-Controller. Run once with:
#     sudo bash install/setup.sh
# It is safe to run again. Apart from the small RAM lighting helper it
# installs, everything in this project runs without root.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run this with sudo: sudo bash install/setup.sh" >&2
    exit 1
fi

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(dirname "$here")"

# Who is installing? (works with both sudo and pkexec)
user="${SUDO_USER:-}"
[[ -z "$user" && -n "${PKEXEC_UID:-}" ]] && user="$(id -un "$PKEXEC_UID")"
if [[ -z "$user" || "$user" == root ]]; then
    echo "Could not tell which user this is for. Run it with sudo from your own account." >&2
    exit 1
fi

echo "==> Installing OpenRGB (case lights), acpi_call (RAM lights) and Python venv support"
apt-get install -y openrgb acpi-call-dkms python3-venv python3-pip

echo "==> Allowing $user to talk to the cooler LCD (USB 03f0:7397)"
install -m 0644 "$here/70-omen-lcd.rules" /etc/udev/rules.d/70-omen-lcd.rules

echo "==> Loading kernel modules now and at every boot"
# i2c-dev: OpenRGB expects it. acpi_call: lets the RAM helper ask the BIOS
# to set the RAM lights, the way HP's own software does.
printf 'i2c-dev\n' > /etc/modules-load.d/i2c-dev.conf
printf 'acpi_call\n' > /etc/modules-load.d/omenrgb-acpi-call.conf
modprobe i2c-dev
modprobe acpi_call

echo "==> Installing the RAM lighting helper (the only part that runs as root)"
install -d -m 0755 /usr/local/lib/omenrgb
install -m 0755 -o root -g root "$repo/helper/omenrgb_ram_helper.py" /usr/local/lib/omenrgb/
install -m 0644 "$repo/systemd/omenrgb-ram.service" /etc/systemd/system/
sed "s/@USER@/$user/" "$repo/systemd/omenrgb-ram.socket" > /etc/systemd/system/omenrgb-ram.socket
systemctl daemon-reload
systemctl enable omenrgb-ram.socket
systemctl restart omenrgb-ram.socket
systemctl stop omenrgb-ram.service 2>/dev/null || true   # next request starts the new copy

echo "==> Reloading device rules"
udevadm control --reload
udevadm trigger

echo "Done."
