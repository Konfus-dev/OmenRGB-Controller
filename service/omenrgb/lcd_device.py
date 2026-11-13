# SPDX-License-Identifier: GPL-3.0-or-later
"""Finds the cooler LCD and reads/writes raw reports to it.

Uses the kernel's hidraw interface directly, so no extra library is needed.
"""

import os
import select
from pathlib import Path

from .constants import LCD_DESCRIPTOR_PREFIX, LCD_HID_ID, LONG_REPORT_LEN


def find_hidraw(
    sys_root: Path = Path("/sys/class/hidraw"), dev_root: Path = Path("/dev")
) -> Path | None:
    """Return the /dev/hidrawN node of the LCD's data interface, or None.

    The number N changes whenever the LCD reconnects, so always look it up.
    """
    try:
        nodes = sorted(sys_root.iterdir())
    except OSError:
        return None
    for node in nodes:
        try:
            is_lcd = LCD_HID_ID in (node / "device" / "uevent").read_text()
            descriptor = (node / "device" / "report_descriptor").read_bytes()
        except OSError:
            continue
        if is_lcd and descriptor.startswith(LCD_DESCRIPTOR_PREFIX):
            return dev_root / node.name
    return None


class LcdDevice:
    """An open connection to the LCD. All failures raise OSError."""

    def __init__(self, path: Path):
        self._fd: int | None = os.open(path, os.O_RDWR | os.O_NONBLOCK)

    @classmethod
    def from_fd(cls, fd: int) -> "LcdDevice":
        """Wrap an already open file descriptor (used by the tests)."""
        device = cls.__new__(cls)
        device._fd = fd
        return device

    def write(self, report: bytes) -> None:
        if self._fd is None:
            raise OSError("LCD connection is closed")
        os.write(self._fd, report)

    def request(self, report: bytes, timeout_s: float = 0.5) -> bytes | None:
        """Send `report` and return the next thing the LCD says, or None.

        The LCD answers slowly and sometimes one message behind, so check
        that the reply is the one you wanted before trusting it.
        """
        if self._fd is None:
            raise OSError("LCD connection is closed")
        while select.select([self._fd], [], [], 0)[0]:  # discard old messages
            os.read(self._fd, LONG_REPORT_LEN)
        os.write(self._fd, report)
        if select.select([self._fd], [], [], timeout_s)[0]:
            return os.read(self._fd, LONG_REPORT_LEN)
        return None

    def close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
