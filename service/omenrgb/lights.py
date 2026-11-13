# SPDX-License-Identifier: GPL-3.0-or-later
"""Sets the case lights through OpenRGB.

OpenRGB does the real work of talking to the lighting hardware. This file
only decides which effect and colours to ask it for. OpenRGB can be absent
or restart at any time; nothing in here raises because of that.
"""

import logging
from collections.abc import Callable, Collection
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


def scale_colour(hex_colour: str, brightness: int, gamma: float = 1.0) -> tuple[int, int, int]:
    """'#RRGGBB' as the values to send to an LED.

    `gamma` curves each channel first: LEDs put out light in proportion to
    the number they are given, while screens do not, so without a curve a
    dark colour picked on screen looks pale and washed out on the LEDs.
    `brightness` (percent) then dims the result. Bad input counts as white.
    """
    text = hex_colour.strip().lstrip("#")
    try:
        if len(text) != 6:
            raise ValueError
        red, green, blue = (int(text[i : i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        red, green, blue = 255, 255, 255
    factor = max(0, min(100, brightness)) / 100
    return tuple(round(255 * (part / 255) ** gamma * factor) for part in (red, green, blue))


@dataclass(frozen=True)
class EffectOptions:
    """Extra settings for one device's effect. All are percentages."""

    speed: int = 50  # 0 slowest, 100 fastest; only moving effects use it
    min_brightness: int = 0  # how dim a pulsing effect gets
    max_brightness: int = 100  # how bright a pulsing effect gets


@dataclass(frozen=True)
class DeviceInfo:
    """What the settings window needs to know about one lighting device."""

    name: str
    modes: tuple[str, ...]
    zones: tuple[str, ...]
    default_mode: str = ""  # the effect used until the owner picks one
    colourless_modes: tuple[str, ...] = ()  # effects that ignore the zone colours
    speed_modes: tuple[str, ...] = ()  # effects that move, so have a speed
    range_modes: tuple[str, ...] = ()  # effects that pulse between two brightnesses


class Lights:
    def __init__(
        self,
        connect: Callable[[], object],
        hidden_zones: Collection[str] = (),
        gamma: float = 1.0,
    ):
        """`connect` returns an openrgb.OpenRGBClient, or raises if OpenRGB
        is not running. `hidden_zones` are zone_key() names that OpenRGB
        reports but that have no lights attached; they are never shown or
        written to."""
        self._connect = connect
        self._hidden = frozenset(hidden_zones)
        self._gamma = gamma  # see scale_colour
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

    def check(self) -> bool:
        """Ask OpenRGB for fresh device data to find out if it is still there.

        Without this we would only notice OpenRGB had stopped the next time
        a light setting changed.
        """
        if self._client is None:
            return False
        try:
            self._client.update()
        except Exception as error:
            log.warning("Lost connection to OpenRGB: %s", error)
            _disconnect(self._client)
            self._client = None
            return False
        return True

    def devices(self) -> list[DeviceInfo]:
        if self._client is None:
            return []
        return [
            DeviceInfo(
                device.name,
                tuple(mode.name for mode in device.modes),
                tuple(zone.name for zone in self._visible_zones(device)),
                default_mode=DEFAULT_MODE,
                colourless_modes=tuple(
                    mode.name for mode in device.modes if _takes_no_colours(mode)
                ),
                speed_modes=tuple(mode.name for mode in device.modes if _has_speed(mode)),
            )
            for device in self._client.devices
        ]

    def _visible_zones(self, device) -> list:
        return [z for z in device.zones if zone_key(device.name, z.name) not in self._hidden]

    def apply(
        self,
        enabled: bool,
        brightness: int,
        modes: dict[str, str],
        colours: dict[str, str],
        options: dict[str, EffectOptions] | None = None,
    ) -> None:
        """Make the lights match the settings.

        modes:   device name -> effect name
        colours: zone_key(device, zone) -> '#RRGGBB'
        options: device name -> EffectOptions (speed and so on)
        """
        if self._client is None:
            return
        failed = False
        for device in self._client.devices:
            try:
                device_options = (options or {}).get(device.name, EffectOptions())
                self._apply_device(device, enabled, brightness, modes, colours, device_options)
            except Exception as error:
                log.warning("Could not set lights on %s: %s", device.name, error)
                failed = True
        if failed:
            # Drop the connection so the service reconnects and tries again.
            _disconnect(self._client)
            self._client = None

    def _apply_device(self, device, enabled, brightness, modes, colours, options) -> None:
        by_name = {mode.name: mode for mode in device.modes}
        if not by_name:
            return
        zones = self._visible_zones(device)
        zone_colours = [
            RGBColor(
                *scale_colour(
                    colours.get(zone_key(device.name, zone.name), DEFAULT_COLOUR),
                    brightness if enabled else 0,
                    self._gamma,
                )
            )
            for zone in zones
        ]

        if not enabled and OFF_MODE in by_name:
            device.set_mode(by_name[OFF_MODE])
            return

        wanted = modes.get(device.name, DEFAULT_MODE) if enabled else DEFAULT_MODE
        mode = by_name.get(wanted) or by_name.get(FALLBACK_MODE) or device.modes[0]

        if _has_speed(mode):
            mode.speed = _speed_value(mode, options.speed)

        if mode.colors is not None and zone_colours:
            # An effect with its own palette (Breathing, Wave, ...): fill the
            # palette by repeating the zone colours, then start the effect.
            mode.colors = [zone_colours[i % len(zone_colours)] for i in range(len(mode.colors))]
            device.set_mode(mode)
            return

        # A plain mode (Static, Direct): one colour per zone.
        device.set_mode(mode)
        for zone, colour in zip(zones, zone_colours, strict=True):
            zone.set_color(colour)


def _takes_no_colours(mode) -> bool:
    """OpenRGB marks effects without any colour setting with color_mode 0."""
    try:
        return int(getattr(mode, "color_mode", 1)) == 0
    except (TypeError, ValueError):
        return False


def _has_speed(mode) -> bool:
    slowest, fastest = getattr(mode, "speed_min", None), getattr(mode, "speed_max", None)
    return slowest is not None and fastest is not None and slowest != fastest


def _speed_value(mode, percent: int) -> int:
    """Turn 0..100 into the effect's own speed number.

    OpenRGB's speed_min is the slowest setting and speed_max the fastest,
    whichever of the two is the larger number.
    """
    fraction = max(0, min(100, percent)) / 100
    return round(mode.speed_min + (mode.speed_max - mode.speed_min) * fraction)


def _disconnect(client) -> None:
    try:
        client.disconnect()
    except Exception:
        pass
