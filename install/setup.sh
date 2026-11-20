#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# One-time system setup for RGB-Hub. Run once with:
#     sudo bash install/setup.sh
# It is safe to run again. Add --no-apt to leave the installed packages
# alone (apt-get would also upgrade OpenRGB, which then needs a fresh
# detector check by install-user.sh). Apart from the small RAM lighting helper it
# installs, everything in this project runs without root.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run this with sudo: sudo bash install/setup.sh" >&2
    exit 1
fi

apt=yes
[[ "${1:-}" == --no-apt ]] && apt=no

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

# --- From before RGB-Hub (OmenRGB-Controller) -----------------------------------

if [[ -e /etc/systemd/system/omenrgb-ram.socket || -e /usr/local/lib/omenrgb ]]; then
    echo "==> Removing the old OmenRGB-Controller system parts"
    systemctl disable --now omenrgb-ram.socket omenrgb-ram.service 2>/dev/null || true
    rm -f /etc/systemd/system/omenrgb-ram.socket /etc/systemd/system/omenrgb-ram.service
    rm -rf /usr/local/lib/omenrgb
    rm -f /etc/modules-load.d/omenrgb-acpi-call.conf
fi
rm -f /etc/udev/rules.d/70-omen-lcd.rules

# --- Cooler LCD, keyboard and case lights ---------------------------------------

echo "==> Allowing $user to talk to the cooler LCD (USB 03f0:7397) and the IQUNIX keyboard's lights (320f:5055)"
install -m 0644 "$here/70-rgb-hub.rules" /etc/udev/rules.d/70-rgb-hub.rules
udevadm control --reload
udevadm trigger --subsystem-match=hidraw

if [[ $apt == yes ]]; then
    echo "==> Installing Python venv support and OpenRGB (case lights)"
    apt-get install -y python3-venv python3-pip
    apt-get install -y openrgb || warn "could not install openrgb; case lights will be unavailable"
fi

echo "==> Loading i2c-dev now and at every boot (OpenRGB expects it; monitor brightness over DDC uses it)"
printf 'i2c-dev\n' > /etc/modules-load.d/i2c-dev.conf
modprobe i2c-dev || warn "could not load i2c-dev"

echo "==> Locking the RAM's bus (SMBus) to root only (71-rgbhub-ram-bus-lock.rules explains why)"
install -m 0644 "$here/71-rgbhub-ram-bus-lock.rules" /etc/udev/rules.d/71-rgbhub-ram-bus-lock.rules
udevadm control --reload
udevadm trigger --subsystem-match=i2c-dev --action=change
# Access given before the rule existed is taken back too.
for bus in /sys/class/i2c-dev/*; do
    case "$(cat "$bus/name" 2>/dev/null)" in
        SMBus*) setfacl -b "/dev/$(basename "$bus")" 2>/dev/null || true ;;
    esac
done

# --- RAM lights (optional: a problem here must not block the rest) ------------

echo "==> Installing acpi_call, which lets the RAM helper ask the BIOS to set the RAM lights"
ram_ok=yes
if [[ $apt == yes ]]; then
    apt-get install -y acpi-call-dkms || ram_ok=no
fi
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
# Keep the helper running until the user's own services (the hub) have
# stopped at shutdown: the hub sends the RAM its Default look then.
uid="$(id -u "$user")"
install -d /etc/systemd/system/rgbhub-ram.service.d
printf '[Unit]\nBefore=user@%s.service\n' "$uid" > /etc/systemd/system/rgbhub-ram.service.d/after-user.conf
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
