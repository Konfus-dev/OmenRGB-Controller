# SPDX-License-Identifier: GPL-3.0-or-later
"""Every setting the hub understands, with its default.

This is the contract between the hub and its front ends: a front end
writes these, the hub acts on them and writes "status" back.

Stores that need their own description of the keys (the GSettings schema in
frontends/gnome-shell/.../schemas) must match this list; a test checks it.

Settings that belong to one hub device are tables keyed by its id
("OMEN-PC", "IQUNIX-KEYBOARD"). Lighting settings are keyed by the name of
the lighting device inside it ("HP Omen 30L", "RAM", "IQUNIX MG65").
"""

import json
from typing import Any

DEFAULTS: dict[str, bool | int | str | list | dict] = {
    # Per hub device
    "device-enabled": {},  # device id -> lights on (missing = on)
    "device-brightness": {},  # device id -> percent (missing = 100)
    # Per lighting device
    "device-modes": {},  # lighting device name -> effect name
    "zone-colours": {},  # "Device/Zone" -> "#RRGGBB"
    "device-speeds": {},  # lighting device name -> 0 (slowest) .. 100 (fastest)
    "device-min-brightness": {},  # lighting device name -> percent; pulsing effects
    "device-max-brightness": {},  # lighting device name -> percent; pulsing effects
    # Cooler LCD (OMEN-PC)
    "lcd-enabled": True,  # off shows a black screen
    "lcd-brightness": 80,  # percent
    "lcd-image": "",  # absolute path; empty leaves the built-in logo
    "lcd-fit": "fill",  # "fill" crops, "fit" shows everything with black bars
    "lcd-recent-images": [],  # newest first; kept by the front end
    # Auto mode: follow what is in focus (an app, a game)
    "auto-devices": {},  # device id -> follows what is in focus (missing = no)
    "auto-lights": True,  # OMEN-PC: its lights follow
    "auto-lcd": True,  # OMEN-PC: its LCD shows what is in focus
    "auto-idle-image": "",  # shown when nothing is in focus; empty = the system's own idle picture
    "auto-idle-colour": "",  # "#RRGGBB"; empty = taken from the idle picture
    "focused-app": "",  # written by the front end: what is in focus now
    # The Default look, shown on every device when the hub stops
    "default-look": "{}",  # JSON: a copy of the DEFAULT_KEYS settings
    # The front end asks: "save" (current look -> Default), "load" (Default -> current)
    "default-action": "",
    # Written by the hub only: a JSON report for the front end
    "status": "{}",
}

# What the entries of each table setting hold (their keys are always names).
# A colour where a number belongs, or the other way round, would trip up the
# code that uses it, so stores that cannot enforce types themselves (a JSON
# file) check against this.
TABLE_VALUES: dict[str, type] = {
    "device-enabled": bool,
    "device-brightness": int,
    "device-modes": str,
    "zone-colours": str,
    "device-speeds": int,
    "device-min-brightness": int,
    "device-max-brightness": int,
    "auto-devices": bool,
}

# The hub's devices, in the order front ends show them. A front end lists
# them even when one is not plugged in.
DEVICE_IDS = ("OMEN-PC", "IQUNIX-KEYBOARD")

# What makes up a look; "Save as Default" copies these.
DEFAULT_KEYS = (
    "device-enabled",
    "device-brightness",
    "device-modes",
    "zone-colours",
    "device-speeds",
    "device-min-brightness",
    "device-max-brightness",
    "lcd-enabled",
    "lcd-brightness",
    "lcd-image",
    "lcd-fit",
)


def valid(key: str, value: Any, default: Any = None) -> bool:
    """Is `value` the right kind of value for setting `key`? Looks inside
    lists and tables too. `default` stands in for the standard default where
    a platform has its own (of the same kind)."""
    default = DEFAULTS[key] if default is None else default
    # type(...) is, not isinstance: true and false must not pass as numbers.
    if type(value) is not type(default):
        return False
    if isinstance(value, dict):
        kind = TABLE_VALUES.get(key, str)
        return all(isinstance(k, str) and type(v) is kind for k, v in value.items())
    if isinstance(value, list):
        return all(isinstance(item, str) for item in value)
    return True


def parse_default(text: str) -> dict[str, Any]:
    """The saved Default as a table, keeping only known keys of the right kind."""
    try:
        data = json.loads(text or "{}")
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items() if key in DEFAULT_KEYS and valid(key, value)}


# Settings from before RGB-Hub (OmenRGB-Controller) and where they went.
def migrate(old: dict[str, Any]) -> dict[str, Any]:
    """Old settings (OmenRGB-Controller) as RGB-Hub settings.

    Keys that did not change are copied; the few that became per device
    are moved into their tables. Unknown keys are dropped.
    """
    new = {key: value for key, value in old.items() if key in DEFAULTS and key != "status"}
    if "lights-enabled" in old:
        new["device-enabled"] = {"OMEN-PC": bool(old["lights-enabled"])}
    if "lights-brightness" in old:
        new["device-brightness"] = {"OMEN-PC": int(old["lights-brightness"])}
    if "auto-enabled" in old:
        new["auto-devices"] = {device: bool(old["auto-enabled"]) for device in DEVICE_IDS}
    return new
