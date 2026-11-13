# SPDX-License-Identifier: GPL-3.0-or-later
"""Sets the case lights through OpenRGB.

OpenRGB does the real work of talking to the lighting hardware. This file
only decides which effect and colours to ask it for. OpenRGB can be absent
or restart at any time; nothing in here raises because of that.
"""

import logging
import math
from collections.abc import Callable, Collection
from dataclasses import dataclass, field

from openrgb.utils import RGBColor

log = logging.getLogger(__name__)

DEFAULT_MODE = "Static"  # used for a device with no saved effect
FALLBACK_MODE = "Direct"  # used when the saved effect does not exist
OFF_MODE = "Off"
DEFAULT_COLOUR = "#FFFFFF"  # used for a zone with no saved colour

# "Breathing" is animated by this program instead of by the lighting board.
# The board's own breathing always fades to black and has three speeds;
# doing it ourselves gives a real speed, dimmest and brightest setting. It
# needs FALLBACK_MODE ("Direct"), in which the board shows whatever colours
# it is sent.
SOFTWARE_BREATHING = "Breathing"
BREATH_SLOWEST_S, BREATH_FASTEST_S = 8.0, 1.0  # length of one breath in and out


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


def breath_level(elapsed_s: float, speed: int, min_brightness: int, max_brightness: int) -> float:
    """How bright (0.0 .. 1.0) a breath is `elapsed_s` seconds after it began.

    It starts at its dimmest, swells smoothly to its brightest and back.
    `speed`, `min_brightness` and `max_brightness` are percentages.
    """

    def fraction(percent: int) -> float:
        return max(0, min(100, percent)) / 100

    brightest = fraction(max_brightness)
    dimmest = min(fraction(min_brightness), brightest)
    period = BREATH_SLOWEST_S + (BREATH_FASTEST_S - BREATH_SLOWEST_S) * fraction(speed)
    swell = 0.5 - 0.5 * math.cos(2 * math.pi * elapsed_s / period)  # 0 -> 1 -> 0
    return dimmest + (brightest - dimmest) * swell


@dataclass
class _Breath:
    """One device that is currently being breathed by us."""

    device: object
    colours: list[tuple[int, int, int]]  # full-brightness colour of every LED
    options: EffectOptions
    started_at: float | None = field(default=None)  # set on the first frame


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
        self._breaths: dict[str, _Breath] = {}  # device name -> running breath
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
            self._drop()
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
                range_modes=(SOFTWARE_BREATHING,) if _can_breathe(device) else (),
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
            self._drop()

    def _drop(self) -> None:
        _disconnect(self._client)
        self._client = None
        self._breaths.clear()

    # --- breathing, animated by us -------------------------------------------------

    @property
    def animating(self) -> bool:
        """True while tick_animation() needs to be called many times a second."""
        return bool(self._breaths)

    def tick_animation(self, now: float) -> None:
        """Send the next frame of every running breath. `now` is in seconds."""
        try:
            for breath in self._breaths.values():
                if breath.started_at is None:
                    breath.started_at = now
                level = breath_level(
                    now - breath.started_at,
                    breath.options.speed,
                    breath.options.min_brightness,
                    breath.options.max_brightness,
                )
                frame = [RGBColor(*(round(part * level) for part in c)) for c in breath.colours]
                breath.device.set_colors(frame, fast=True)
        except Exception as error:
            log.warning("Lost connection to OpenRGB while animating: %s", error)
            self._drop()

    def _start_breath(self, device, by_name, brightness, colours, options) -> None:
        """Begin breathing `device`, or update a breath that is already running."""
        led_colours = []
        for zone in device.zones:
            key = zone_key(device.name, zone.name)
            if key in self._hidden:
                colour = (0, 0, 0)
            else:
                colour = scale_colour(colours.get(key, DEFAULT_COLOUR), brightness, self._gamma)
            led_colours.extend([colour] * len(zone.leds))
        running = self._breaths.get(device.name)
        if running is None:
            device.set_mode(by_name[FALLBACK_MODE])
            self._breaths[device.name] = _Breath(device, led_colours, options)
        else:
            # Same breath, new settings: keep its rhythm instead of restarting.
            running.colours, running.options = led_colours, options

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

        wanted = modes.get(device.name, DEFAULT_MODE) if enabled else DEFAULT_MODE
        if enabled and wanted == SOFTWARE_BREATHING and _can_breathe(device):
            self._start_breath(device, by_name, brightness, colours, options)
            return
        self._breaths.pop(device.name, None)

        if not enabled and OFF_MODE in by_name:
            device.set_mode(by_name[OFF_MODE])
            return

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


def _can_breathe(device) -> bool:
    """True if we can animate this device's breathing ourselves."""
    names = {mode.name for mode in device.modes}
    return SOFTWARE_BREATHING in names and FALLBACK_MODE in names


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
