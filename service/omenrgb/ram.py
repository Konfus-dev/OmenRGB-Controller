# SPDX-License-Identifier: GPL-3.0-or-later
"""RAM stick lighting (Kingston Fury DDR5, as fitted by HP).

The sticks are never spoken to directly: on the OMEN 45L that jams the
RAM's control bus until the next reboot. Every command goes to the small
root helper (helper/omenrgb_ram_helper.py), which asks the BIOS to deliver
it, the way HP's own software does.

Register meanings come from HP's OMEN Gaming Hub and from OpenRGB's
KingstonFuryDRAMController.
"""

import logging
import socket
from collections.abc import Callable
from pathlib import Path

from .lights import DEFAULT_COLOUR, DeviceInfo, scale_colour, zone_key

log = logging.getLogger(__name__)

HELPER_SOCKET = "/run/omenrgb-ram.sock"
I2C_DEVICES = Path("/sys/bus/i2c/devices")
SPD_DRIVER_NAME = "spd5118"  # the kernel's name for a DDR5 stick's info chip

DEVICE_NAME = "RAM"
MODES = ("Static", "Rainbow", "Breathing")
DEFAULT_MODE = "Rainbow"  # what the sticks do out of the box

# --- registers -----------------------------------------------------------------
REG_TRANSFER = 0x08  # every change is wrapped in begin ... end
TRANSFER_BEGIN, TRANSFER_END = 0x53, 0x44
REG_EFFECT = 0x09
EFFECT_STATIC, EFFECT_RAINBOW, EFFECT_BREATHING = 0x00, 0x01, 0x03
REG_STICK_POSITION = 0x0B  # lets an effect flow from one stick to the next
REG_DIRECTION = 0x0C
DIRECTION_UP = 0x01
REG_DELAY = 0x0D
REG_SPEED = 0x0E
REG_BREATH_TIMES = (0x16, 0x17, 0x18, 0x19)  # dim->mid, mid->bright, bright->mid, mid->dim
REG_BREATH_HOLD_DIM = 0x1A
REG_BREATH_LEVELS = (0x1B, 0x1C, 0x1D)  # bright, mid, dim (percent)
REG_BRIGHTNESS = 0x20  # 0..100
REG_STICK_COUNT = 0x27
REG_COLOUR_COUNT = 0x30
REG_COLOUR = (0x31, 0x32, 0x33)  # red, green, blue of the first effect colour

# --- effect tuning (change these to taste) -------------------------------------
RAINBOW_SPEED = 25  # 0 is fastest, 60 slowest
BREATH_TIMES = (15, 5, 5, 15)  # larger is slower
BREATH_HOLD_DIM = 1
BREATH_LEVELS = (100, 64, 0)

Command = tuple[int, int, int]  # (stick address, register, value)


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


def commands(
    mode: str, brightness: int, colours: list[tuple[int, int, int]], sticks: list[int]
) -> list[Command]:
    """The register writes that put `sticks` into `mode`.

    `colours` holds one (red, green, blue) per stick; missing ones are white.
    `mode` is one of MODES or "Off"; anything else means "Static".
    """
    if not sticks:
        return []
    brightness = max(0, min(100, brightness))
    colours = [
        tuple(max(0, min(255, part)) for part in (colours[i] if i < len(colours) else (255,) * 3))
        for i in range(len(sticks))
    ]
    result: list[Command] = []

    def everywhere(register: int, value: int) -> None:
        result.extend((stick, register, value) for stick in sticks)

    def colour_per_stick() -> None:
        everywhere(REG_COLOUR_COUNT, 1)
        for part, register in enumerate(REG_COLOUR):
            result.extend((stick, register, colours[i][part]) for i, stick in enumerate(sticks))

    def stick_positions() -> None:
        # HP numbers the sticks from the far end, so effects flow across them.
        result.extend(
            (stick, REG_STICK_POSITION, len(sticks) - 1 - i) for i, stick in enumerate(sticks)
        )
        everywhere(REG_STICK_COUNT, len(sticks))

    everywhere(REG_TRANSFER, TRANSFER_BEGIN)
    if mode == "Off":
        everywhere(REG_BRIGHTNESS, 0)
    elif mode == "Rainbow":
        everywhere(REG_EFFECT, EFFECT_RAINBOW)
        stick_positions()
        everywhere(REG_DIRECTION, DIRECTION_UP)
        everywhere(REG_DELAY, 0)
        everywhere(REG_SPEED, RAINBOW_SPEED)
        everywhere(REG_BRIGHTNESS, brightness)
    elif mode == "Breathing":
        everywhere(REG_EFFECT, EFFECT_BREATHING)
        stick_positions()
        everywhere(REG_DIRECTION, DIRECTION_UP)
        everywhere(REG_DELAY, 0)
        everywhere(REG_SPEED, 0)
        for register, value in zip(REG_BREATH_TIMES, BREATH_TIMES, strict=True):
            everywhere(register, value)
        everywhere(REG_BREATH_HOLD_DIM, BREATH_HOLD_DIM)
        for register, value in zip(REG_BREATH_LEVELS, BREATH_LEVELS, strict=True):
            everywhere(register, value)
        everywhere(REG_BRIGHTNESS, brightness)
        colour_per_stick()
    else:  # Static: exactly the sequence HP's software sends
        everywhere(REG_EFFECT, EFFECT_STATIC)
        everywhere(REG_BRIGHTNESS, brightness)
        colour_per_stick()
    everywhere(REG_TRANSFER, TRANSFER_END)
    return result


def send_to_helper(batch: list[Command], path: str = HELPER_SOCKET) -> None:
    """Send commands to the root helper. Raises OSError if anything fails.

    An empty batch just checks that the helper answers.
    """
    lines = [f"W {a:02x} {r:02x} {v:02x}" for a, r, v in batch] or ["PING"]
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(5)
        connection.connect(path)
        with connection.makefile("rw", encoding="ascii") as stream:
            for line in lines:
                stream.write(line + "\n")
                stream.flush()
                reply = stream.readline().strip()
                if reply != "OK":
                    raise OSError(f"RAM helper refused '{line}': {reply or 'no answer'}")


class RamLights:
    """The RAM sticks, presented like any other lighting device."""

    def __init__(
        self,
        send: Callable[[list[Command]], None] = send_to_helper,
        find: Callable[[], list[int]] = find_sticks,
    ):
        self._send = send
        self._find = find
        self._sticks: list[int] = []
        self._last_sent: list[Command] | None = None

    @property
    def connected(self) -> bool:
        return bool(self._sticks)

    def try_connect(self) -> bool:
        """True once there are sticks and the root helper answers."""
        if self._sticks:
            return True
        sticks = self._find()
        if not sticks:
            return False
        try:
            self._send([])
        except OSError as error:
            log.debug("RAM helper not available: %s", error)
            return False
        self._sticks = sticks
        self._last_sent = None
        log.info("RAM lights connected, %d stick(s)", len(sticks))
        return True

    def check(self) -> bool:
        return self.connected

    def devices(self) -> list[DeviceInfo]:
        if not self._sticks:
            return []
        zones = tuple(f"Stick {i + 1}" for i in range(len(self._sticks)))
        return [DeviceInfo(DEVICE_NAME, MODES, zones)]

    def apply(
        self, enabled: bool, brightness: int, modes: dict[str, str], colours: dict[str, str]
    ) -> None:
        """Same call as Lights.apply, so the service treats both alike."""
        if not self._sticks:
            return
        mode = modes.get(DEVICE_NAME, DEFAULT_MODE) if enabled else "Off"
        per_stick = [
            scale_colour(colours.get(zone_key(DEVICE_NAME, f"Stick {i + 1}"), DEFAULT_COLOUR), 100)
            for i in range(len(self._sticks))
        ]
        batch = commands(mode, brightness, [] if mode == "Off" else per_stick, self._sticks)
        if batch == self._last_sent:
            return  # nothing changed; do not bother the BIOS
        try:
            self._send(batch)
        except OSError as error:
            log.warning("Could not set RAM lights: %s", error)
            self._sticks = []  # reconnect and resend on the next check
            return
        self._last_sent = batch
