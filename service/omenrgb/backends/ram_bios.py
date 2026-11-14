# SPDX-License-Identifier: GPL-3.0-or-later
"""Delivering RAM lighting writes through the BIOS, and finding the sticks.

The sticks are never spoken to directly over SMBus: on the OMEN 45L that
jams the RAM's control bus until the next reboot. Every write is handed to
the BIOS, the way HP's own software does it. Only root can do that, so an
ordinary user's service goes through the small root helper
(helper/omenrgb_ram_helper.py).
"""

import socket
from pathlib import Path

from ..core.ram import Command

HELPER_SOCKET = "/run/omenrgb-ram.sock"
# The helper normally answers within a tenth of a second. Waiting longer
# would hold up the rest of the service, which has an LCD to keep fed.
HELPER_TIMEOUT_S = 1.0

I2C_DEVICES = Path("/sys/bus/i2c/devices")
SPD_DRIVER_NAME = "spd5118"  # the kernel's name for a DDR5 stick's info chip


def find_sticks(i2c_devices: Path = I2C_DEVICES) -> list[int]:
    """Addresses of the sticks that are fitted, lowest slot first.

    A stick in slot N has its info chip at 0x50 + N and is addressed for
    lighting as 0xC0 + 2 * N.
    """
    sticks = []
    for slot in range(4):
        for node in i2c_devices.glob(f"*-00{0x50 + slot:02x}"):
            try:
                if (node / "name").read_text().strip() == SPD_DRIVER_NAME:
                    sticks.append(0xC0 + 2 * slot)
                    break
            except OSError:
                continue
    return sticks


def send_to_helper(batch: list[Command], path: str = HELPER_SOCKET) -> None:
    """Send commands to the root helper. Raises OSError if anything fails.

    An empty batch just checks that the helper answers.
    """
    lines = [f"W {a:02x} {r:02x} {v:02x}" for a, r, v in batch] or ["PING"]
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(HELPER_TIMEOUT_S)
        connection.connect(path)
        with connection.makefile("rw", encoding="ascii") as stream:
            for line in lines:
                stream.write(line + "\n")
                stream.flush()
                reply = stream.readline().strip()
                if reply != "OK":
                    raise OSError(f"RAM helper refused '{line}': {reply or 'no answer'}")
