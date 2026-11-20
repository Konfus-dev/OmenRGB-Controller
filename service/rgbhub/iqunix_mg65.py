# SPDX-License-Identifier: GPL-3.0-or-later
"""Facts about the IQUNIX MG65 keyboard that are the same on every system.

USB 320f:5055. It runs VIA-compatible firmware (VIA protocol 11), with its
lights on VIA's "RGB matrix" channel (see backends/via.py).
"""

from .backends.via import KeyboardModel

DEVICE_NAME = "IQUNIX MG65"  # the key for its lighting settings

# QMK numbers the effects the firmware was built with: 1 is a plain colour.
# This one accepts 1..18 (checked 2026-11-19: higher numbers are refused).
# Which number is which is not known yet, so the others are listed by
# number; give them names here once they are seen.
EFFECTS = (("Static", 1),) + tuple((f"Effect {n}", n) for n in range(2, 19))

MG65 = KeyboardModel(
    name=DEVICE_NAME,
    hid_id="0003:0000320F:00005055",
    effects=EFFECTS,
    moving=tuple(name for name, _ in EFFECTS[1:]),
    max_brightness=9,  # its brightness runs 0..9 (higher values read back as 9)
)

DISPLAY_NAMES = {
    DEVICE_NAME: "Keyboard lights",
    f"{DEVICE_NAME}/All keys": "All keys",
}
