# SPDX-License-Identifier: GPL-3.0-or-later
"""Keyboard lighting through VIA, the raw-HID protocol of QMK keyboards.

Used for the IQUNIX MG65. Only these standard VIA commands are ever sent,
all on the "RGB matrix" channel:

    0x07 custom_set_value   live brightness, effect, speed or colour
    0x08 custom_get_value   read one of those back
    0x09 custom_save        store the live look in the keyboard itself

A set changes only what the keyboard shows now; a save is what makes it
keep the look after the computer stops talking to it. Saves are sent only
when the hub stops (see core/hub.py), never while drawing.

To lights.py this looks like an OpenRGB client (as backends/tracer.py does):
one device with `modes`, `zones` and `set_mode()`. Every mode carries one
colour, so lights.py hands the colour over with the mode; nothing is drawn
frame by frame. Only values that changed are sent.
"""

import colorsys
import logging
import os
import select
from dataclasses import dataclass, field
from pathlib import Path

from .hidraw import find_hid_node

log = logging.getLogger(__name__)

REPORT_LEN = 32  # VIA's report size, without the report id
REPLY_WAIT_S = 0.05  # each command is answered; wait this long for the answer

SET_VALUE, GET_VALUE, SAVE = 0x07, 0x08, 0x09
CHANNEL_RGB_MATRIX = 3
VALUE_BRIGHTNESS, VALUE_EFFECT, VALUE_SPEED, VALUE_COLOUR = 1, 2, 3, 4

# The interface whose report descriptor starts like this is VIA's
# (usage page 0xFF60, usage 0x61).
VIA_DESCRIPTOR_PREFIX = bytes.fromhex("0660ff0961")

OFF_MODE = "Off"


def set_report(value_id: int, *data: int) -> bytes:
    """The report that sets one RGB-matrix value."""
    return _report(SET_VALUE, CHANNEL_RGB_MATRIX, value_id, *data)


def get_report(value_id: int) -> bytes:
    return _report(GET_VALUE, CHANNEL_RGB_MATRIX, value_id)


def save_report() -> bytes:
    return _report(SAVE, CHANNEL_RGB_MATRIX)


def _report(*payload: int) -> bytes:
    body = bytes(max(0, min(255, b)) for b in payload)
    return b"\x00" + body + bytes(REPORT_LEN - len(body))  # report id 0 first


def colour_to_hsv(
    red: int, green: int, blue: int, saturation_curve: float = 1.0
) -> tuple[int, int, int]:
    """0..255 RGB as the keyboard's hue, saturation and brightness (0..255).

    `saturation_curve` above 1 makes colours more vivid without moving
    their hue: LEDs show a slightly pale colour as a washed-out one (a pale
    orange looks pink), and 1 leaves it as it is.
    """
    hue, sat, val = colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)
    sat = 1 - (1 - sat) ** saturation_curve
    return round(hue * 255) % 256, round(sat * 255), round(val * 255)


@dataclass(frozen=True)
class KeyboardModel:
    """What differs between keyboards: their name and their effects."""

    name: str
    hid_id: str  # as in sysfs uevent files, "0003:VVVVVVVV:PPPPPPPP"
    effects: tuple[tuple[str, int], ...]  # (name, VIA effect number); first is the plain colour
    moving: tuple[str, ...] = ()  # effects that have a speed
    max_brightness: int = 255  # the keyboard's brightest value
    max_speed: int = 255  # its fastest effect speed
    saturation_curve: float = 1.0  # see colour_to_hsv


@dataclass
class _Mode:
    """Just enough of an OpenRGB mode for lights.py."""

    name: str
    number: int | None  # VIA effect number; None for "Off"
    colors: list = field(default_factory=lambda: [(255, 255, 255)])
    color_mode: int = 1
    speed_min: int | None = None
    speed_max: int | None = None
    speed: int = 0


@dataclass
class _Zone:
    name: str = "All keys"
    leds: list = field(default_factory=lambda: [0])

    def set_color(self, colour) -> None:
        """Never used: every mode takes its colour with it."""


class _Keyboard:
    def __init__(self, port, model: KeyboardModel):
        self._port = port
        self._model = model
        self.name = model.name
        self.modes = [
            _Mode(
                name,
                number,
                speed_min=0 if name in model.moving else None,
                speed_max=model.max_speed if name in model.moving else None,
            )
            for name, number in model.effects
        ] + [_Mode(OFF_MODE, None, colors=None, color_mode=0)]
        self.zones = [_Zone()]
        self._sent: dict[int, tuple[int, ...]] = {}  # value id -> what the keyboard holds

    def set_mode(self, mode: _Mode) -> None:
        if mode.number is None:  # Off: QMK cannot select "no effect", so go dark
            self._set(VALUE_BRIGHTNESS, 0)
            return
        red, green, blue = (int(part) for part in tuple(mode.colors[0])[:3])
        hue, sat, val = colour_to_hsv(red, green, blue, self._model.saturation_curve)
        self._set(VALUE_EFFECT, mode.number)
        self._set(VALUE_COLOUR, hue, sat)
        if mode.speed_min is not None:
            self._set(VALUE_SPEED, mode.speed)
        self._set(VALUE_BRIGHTNESS, round(val * self._model.max_brightness / 255))

    def set_colors(self, colours, fast: bool = False) -> None:
        """Not used: this keyboard has no frame-by-frame ("Direct") mode here."""

    def forget(self) -> None:
        self._sent.clear()

    def save(self) -> None:
        """Make the keyboard keep what it shows now."""
        self._port.write(save_report())
        self._port.drain()
        log.info("Keyboard: look stored in the keyboard")

    def _set(self, value_id: int, *data: int) -> None:
        if self._sent.get(value_id) == data:
            return
        self._port.write(set_report(value_id, *data))
        self._port.drain()
        self._sent[value_id] = data
        log.debug("Keyboard: value %d = %s", value_id, data)


class ViaPort:
    """An open VIA interface. write(report), drain() and close(); OSError on failure."""

    def __init__(self, node: Path):
        self._fd: int | None = os.open(node, os.O_RDWR | os.O_NONBLOCK)

    def write(self, report: bytes) -> None:
        if self._fd is None:
            raise OSError("connection is closed")
        os.write(self._fd, report)

    def drain(self) -> None:
        """Read and drop the keyboard's answer, so answers never pile up."""
        if self._fd is None:
            return
        ready, _, _ = select.select([self._fd], [], [], REPLY_WAIT_S)
        while ready:
            try:
                os.read(self._fd, 64)
            except BlockingIOError:
                return
            ready, _, _ = select.select([self._fd], [], [], 0)

    def close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None


class ViaClient:
    def __init__(self, port, model: KeyboardModel, node: Path | None = None):
        self._port = port
        self._node = node
        self.keyboard = _Keyboard(port, model)
        self.devices = [self.keyboard]

    def update(self) -> None:
        """Raise OSError if the keyboard has gone away."""
        if self._node is not None and not self._node.exists():
            raise OSError(f"{self._node} is gone")

    def save(self) -> None:
        self.keyboard.save()

    def disconnect(self) -> None:
        self._port.close()


def opener(
    model: KeyboardModel, sys_root: Path = Path("/sys/class/hidraw"), dev_root: Path = Path("/dev")
):
    """A function that finds and opens the keyboard (raises OSError if absent)."""

    def open_keyboard() -> ViaClient:
        node = find_hid_node(model.hid_id, VIA_DESCRIPTOR_PREFIX, sys_root, dev_root)
        if node is None:
            raise OSError(f"{model.name} not found")
        log.info("%s found at %s", model.name, node)
        return ViaClient(ViaPort(node), model, node)

    return open_keyboard
