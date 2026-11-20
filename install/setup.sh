#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# One-time system setup for RGB-Hub. Run once with:
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
if [[ ! "$user" =~ ^[a-z_][a-z0-9_-]*$ ]]; then
    echo "User name '$user' has characters this script does not handle." >&2
    exit 1
fi

warn() { echo "WARNING: $*" >&2; }

# --- Cooler LCD and case lights ---------------------------------------------

echo "==> Allowing $user to talk to the cooler LCD (USB 03f0:7397)"
install -m 0644 "$here/70-omen-lcd.rules" /etc/udev/rules.d/70-omen-lcd.rules
udevadm control --reload
udevadm trigger --subsystem-match=hidraw

echo "==> Installing Python venv support and OpenRGB (case lights)"
apt-get install -y python3-venv python3-pip
apt-get install -y openrgb || warn "could not install openrgb; case lights will be unavailable"

echo "==> Loading i2c-dev now and at every boot (OpenRGB expects it)"
printf 'i2c-dev\n' > /etc/modules-load.d/i2c-dev.conf
modprobe i2c-dev || warn "could not load i2c-dev"

# --- RAM lights (optional: a problem here must not block the rest) ------------

echo "==> Installing acpi_call, which lets the RAM helper ask the BIOS to set the RAM lights"
ram_ok=yes
apt-get install -y acpi-call-dkms || ram_ok=no
modprobe acpi_call || ram_ok=no
if [[ $ram_ok == yes ]]; then
    printf 'acpi_call\n' > /etc/modules-load.d/rgbhub-acpi-call.conf
else
    # Do not ask for a module at every boot that cannot be loaded.
    rm -f /etc/modules-load.d/rgbhub-acpi-call.conf
    warn "acpi_call is not working (new kernel? Secure Boot?). RAM lights will be unavailable; everything else is unaffected."
fi

echo "==> Installing the RAM lighting helper (the only part that runs as root)"
install -d -m 0755 /usr/local/lib/rgbhub
install -m 0755 -o root -g root "$repo/helper/rgbhub_ram_helper.py" /usr/local/lib/rgbhub/
install -m 0644 "$repo/systemd/rgbhub-ram.service" /etc/systemd/system/
sed "s/@USER@/$user/" "$repo/systemd/rgbhub-ram.socket" > /etc/systemd/system/rgbhub-ram.socket
systemctl daemon-reload
# Stop the running helper first: systemd will not restart a socket whose
# service is still active. The next request starts the new copy.
systemctl stop rgbhub-ram.service 2>/dev/null || true
if [[ $ram_ok == yes ]]; then
    systemctl enable rgbhub-ram.socket
    systemctl restart rgbhub-ram.socket
else
    # Without acpi_call the helper could only fail; leave it switched off.
    systemctl disable --now rgbhub-ram.socket 2>/dev/null || true
fi

echo "Done."
