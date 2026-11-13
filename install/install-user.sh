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
ln -sfn "$repo/extension/$uuid" "$extensions/$uuid"
glib-compile-schemas --strict "$repo/extension/$uuid/schemas"

echo "==> Background services"
mkdir -p "$units"
install -m 0644 "$repo/systemd/openrgb-server.service" "$repo/systemd/omenrgb.service" "$units/"
systemctl --user daemon-reload
systemctl --user enable --now openrgb-server.service omenrgb.service
systemctl --user restart omenrgb.service

echo
echo "Done. If this is the first install: log out and back in once, then run"
echo "    gnome-extensions enable $uuid"
