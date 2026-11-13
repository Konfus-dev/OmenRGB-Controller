# SPDX-License-Identifier: GPL-3.0-or-later
"""Sets the case lights through OpenRGB.

OpenRGB does the real work of talking to the lighting hardware. This file
only decides which effect and colours to ask it for. OpenRGB can be absent
or restart at any time; nothing in here raises because of that.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from openrgb.utils import RGBColor

log = logging.getLogger(__name__)

DEFAULT_MODE = "Static"  # used for a device with no saved effect
FALLBACK_MODE = "Direct"  # used when the saved effect does not exist
OFF_MODE = "Off"
DEFAULT_COLOUR = "#FFFFFF"  # used for a zone with no saved colour


def zone_key(device_name: str, zone_name: str) -> str:
    """The key under which a zone's colour is saved in the settings."""
    return f"{device_name}/{zone_name}"


def scale_colour(hex_colour: str, brightness: int) -> tuple[int, int, int]:
    """'#RRGGBB' dimmed to `brightness` percent. Bad input counts as white."""
    text = hex_colour.strip().lstrip("#")
    try:
        if len(text) != 6:
            raise ValueError
        red, green, blue = (int(text[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        red, green, blue = 255, 255, 255
    factor = max(0, min(100, brightness)) / 100
    return (round(red * factor), round(green * factor), round(blue * factor))


@dataclass(frozen=True)
class DeviceInfo:
    """What the settings window needs to know about one lighting device."""

    name: str
    modes: tuple[str, ...]
    zones: tuple[str, ...]


class Lights:
    def __init__(self, connect: Callable[[], object]):
        """`connect` returns an openrgb.OpenRGBClient, or raises if OpenRGB
        is not running."""
        self._connect = connect
        self._client = None

    @property
    def connected(self) -> bool:
        return self._client is not None

    def try_connect(self) -> bool:
        """Connect if not connected. True once OpenRGB has devices for us."""
        if self._client is not None:
            return True
        try:
            client = self._connect()
            if not client.devices:
                # OpenRGB is up but still detecting hardware; ask again later.
                _disconnect(client)
                return False
        except Exception as error:  # the library raises many different types
            log.debug("OpenRGB not available: %s", error)
            return False
        self._client = client
        log.info("OpenRGB connected, %d device(s)", len(client.devices))
        return True

    def devices(self) -> list[DeviceInfo]:
        if self._client is None:
            return []
        return [
            DeviceInfo(
                device.name,
                tuple(mode.name for mode in device.modes),
                tuple(zone.name for zone in device.zones),
            )
            for device in self._client.devices
        ]

    def apply(
        self, enabled: bool, brightness: int, modes: dict[str, str], colours: dict[str, str]
    ) -> None:
        """Make the lights match the settings.

        modes:   device name -> effect name
        colours: zone_key(device, zone) -> '#RRGGBB'
        """
        if self._client is None:
            return
        failed = False
        for device in self._client.devices:
            try:
                self._apply_device(device, enabled, brightness, modes, colours)
            except Exception as error:
                log.warning("Could not set lights on %s: %s", device.name, error)
                failed = True
        if failed:
            # Drop the connection so the service reconnects and tries again.
            _disconnect(self._client)
            self._client = None

    def _apply_device(self, device, enabled, brightness, modes, colours) -> None:
        by_name = {mode.name: mode for mode in device.modes}
        if not by_name:
            return
        zone_colours = [
            RGBColor(
                *scale_colour(
                    colours.get(zone_key(device.name, zone.name), DEFAULT_COLOUR),
                    brightness if enabled else 0,
                )
            )
            for zone in device.zones
        ]

        if not enabled and OFF_MODE in by_name:
            device.set_mode(by_name[OFF_MODE])
            return

        wanted = modes.get(device.name, DEFAULT_MODE) if enabled else DEFAULT_MODE
        mode = by_name.get(wanted) or by_name.get(FALLBACK_MODE) or device.modes[0]

        if mode.colors is not None and zone_colours:
            # An effect with its own palette (Breathing, Wave, ...): fill the
            # palette by repeating the zone colours, then start the effect.
            mode.colors = [zone_colours[i % len(zone_colours)] for i in range(len(mode.colors))]
            device.set_mode(mode)
            return

        # A plain mode (Static, Direct): one colour per zone.
        device.set_mode(mode)
        for zone, colour in zip(device.zones, zone_colours, strict=True):
            zone.set_color(colour)


def _disconnect(client) -> None:
    try:
        client.disconnect()
    except Exception:
        pass
