# SPDX-License-Identifier: GPL-3.0-or-later
"""Every setting the service understands, with its default.

This is the contract between the service and its front ends: a front end
writes these, the service acts on them and writes "status" back.

Stores that need their own description of the keys (the GSettings schema in
frontends/gnome-shell/.../schemas) must match this list; a test checks it.
"""

DEFAULTS: dict[str, bool | int | str | list | dict] = {
    # Lights (case and RAM)
    "lights-enabled": True,
    "lights-brightness": 100,  # percent
    "device-modes": {},  # device name -> effect name
    "zone-colours": {},  # "Device/Zone" -> "#RRGGBB"
    "device-speeds": {},  # device name -> 0 (slowest) .. 100 (fastest)
    "device-min-brightness": {},  # device name -> percent; pulsing effects
    "device-max-brightness": {},  # device name -> percent; pulsing effects
    # Cooler LCD
    "lcd-enabled": True,  # off shows a black screen
    "lcd-brightness": 80,  # percent
    "lcd-image": "",  # absolute path; empty leaves the built-in logo
    "lcd-fit": "fill",  # "fill" crops, "fit" shows everything with black bars
    "lcd-recent-images": [],  # newest first; kept by the front end
    # Auto mode: follow what is in focus (an app, a game)
    "auto-enabled": False,
    "auto-lights": True,
    "auto-lcd": True,
    "auto-idle-image": "",  # shown when nothing is in focus; empty = OS logo
    "auto-idle-colour": "",  # "#RRGGBB"; empty = taken from the idle picture
    "focused-app": "",  # written by the front end: what is in focus now
    # Written by the service only: a JSON report for the front end
    "status": "{}",
}
