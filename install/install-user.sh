#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Per-user install for OmenRGB-Linux-Controller. Run WITHOUT sudo:
#     bash install/install-user.sh
# Safe to run again after pulling changes or editing the settings schema.
set -euo pipefail

if [[ $EUID -eq 0 ]]; then
    echo "Do not run this with sudo. Run: bash install/install-user.sh" >&2
    exit 1
fi

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
uuid="omenrgb@konfus-dev.github.io"
venv="$HOME/.local/share/omenrgb/venv"
extensions="$HOME/.local/share/gnome-shell/extensions"
units="$HOME/.config/systemd/user"

echo "==> Python environment in $venv"
# --system-site-packages lets it use Ubuntu's own Pillow and GTK bindings.
[[ -x "$venv/bin/python" ]] || python3 -m venv --system-site-packages "$venv"
# Editable install: the service runs straight from this repo, so after
# editing the code you only need: systemctl --user restart omenrgb
"$venv/bin/pip" install --quiet --editable "$repo/service"

echo "==> GNOME Shell extension (linked, not copied)"
mkdir -p "$extensions"
if [[ -e "$extensions/$uuid" && ! -L "$extensions/$uuid" ]]; then
    echo "ERROR: $extensions/$uuid exists and is not a link made by this script. Remove it first." >&2
    exit 1
fi
ln -sfn "$repo/extension/$uuid" "$extensions/$uuid"
glib-compile-schemas --strict "$repo/extension/$uuid/schemas"

echo "==> OpenRGB limited to the case-light board"
orgb="$HOME/.config/omenrgb/openrgb"
known="$("$venv/bin/python" -m omenrgb.openrgb_config "$orgb" "$HOME/.config/OpenRGB/OpenRGB.json")"
if (( known < 100 )); then
    # OpenRGB only writes down the names of its detectors after one full
    # hardware scan. Do that scan once, then switch the detectors off.
    echo "    One-time full hardware scan (takes about 30 seconds)."
    echo "    RAM temperature sensors may stop reporting until the next reboot."
    systemctl --user stop openrgb-server.service 2>/dev/null || true
    timeout 120 openrgb --config "$orgb" --noautoconnect --list-devices >/dev/null 2>&1 || true
    known="$("$venv/bin/python" -m omenrgb.openrgb_config "$orgb")"
    if (( known < 100 )); then
        # Starting OpenRGB without that list would let it probe everything,
        # RAM included, at every login. Better to stop here.
        echo "ERROR: could not get OpenRGB's detector list (is openrgb installed?)." >&2
        echo "       Run 'sudo bash install/setup.sh' first, then this script again." >&2
        exit 1
    fi
fi

echo "==> Background services"
mkdir -p "$units"
install -m 0644 "$repo/systemd/openrgb-server.service" "$repo/systemd/omenrgb.service" "$units/"
systemctl --user daemon-reload
systemctl --user enable --now openrgb-server.service omenrgb.service
systemctl --user restart openrgb-server.service omenrgb.service

echo
echo "Done. If this is the first install: log out and back in once, then run"
echo "    gnome-extensions enable $uuid"
