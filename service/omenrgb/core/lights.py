# SPDX-License-Identifier: GPL-3.0-or-later
"""Sets the case lights through OpenRGB.

OpenRGB does the real work of talking to the lighting hardware. This file
only decides which effect and colours to ask it for. OpenRGB can be absent
or restart at any time; nothing in here raises because of that.
"""

import logging
import math
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from typing import NamedTuple

log = logging.getLogger(__name__)

DEFAULT_MODE = "Static"  # used for a device with no saved effect
FALLBACK_MODE = "Direct"  # used when the saved effect does not exist
OFF_MODE = "Off"
DEFAULT_COLOUR = "#FFFFFF"  # used for a zone with no saved colour

# If a connection attempt hangs this long before failing, OpenRGB is in a
# bad way; do not try again for a while, because each try holds up the rest
# of the service (which has an LCD to keep fed).
SLOW_FAILURE_S = 2.0
RETRY_AFTER_SLOW_FAILURE_S = 60

# Plain colours and breathing are drawn by this program, with the lighting
# board in FALLBACK_MODE ("Direct"), where it simply shows the colours it is
# sent. That lets a colour change glide from old to new instead of jumping,
# and gives breathing a real speed, dimmest and brightest setting (the
# board's own breathing always fades to black and has three speeds).
SOFTWARE_BREATHING = "Breathing"
SOFTWARE_MODES = (DEFAULT_MODE, FALLBACK_MODE, SOFTWARE_BREATHING)
FADE_S = 0.4  # how long a colour change takes
BREATH_SLOWEST_S, BREATH_FASTEST_S = 8.0, 1.0  # length of one breath in and out


def zone_key(device_name: str, zone_name: str) -> str:
    """The key under which a zone's colour is saved in the settings."""
    return f"{device_name}/{zone_name}"


class Colour(NamedTuple):
    """One colour as sent to a lighting device.

    Shaped so that the OpenRGB library accepts it in place of its own
    colour type; that keeps this file free of any OpenRGB import, which
    matters where OpenRGB is not installed.
    """

    red: int
    green: int
    blue: int

    def pack(self, version: int = 0) -> bytes:
        return bytes((self.red, self.green, self.blue, 0))


class UniformColours(dict):
    """Zone colours in which every zone has the same colour (auto mode)."""

    def __init__(self, colour: str):
        super().__init__()
        self.colour = colour

    def get(self, key, default=None):
        return self.colour


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


Rgb = tuple[float, float, float]


@dataclass
class _Glow:
    """One device whose colours we are drawing ourselves, in direct mode."""

    device: object
    shown: list[Rgb]  # colour of every LED right now (before any breathing)
    target: list[Rgb]  # where a fade is heading
    fade_from: list[Rgb] = field(default_factory=list)
    fading: bool = False
    fade_started_at: float | None = None  # set on the fade's first frame
    breath: EffectOptions | None = None  # set while the device is breathing
    breath_started_at: float | None = None

    @property
    def active(self) -> bool:
        """True while frames have to keep being sent."""
        return self.fading or self.breath is not None


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
        clock: Callable[[], float] = time.monotonic,
    ):
        """`connect` returns an openrgb.OpenRGBClient, or raises if OpenRGB
        is not running. `hidden_zones` are zone_key() names that OpenRGB
        reports but that have no lights attached; they are never shown or
        written to."""
        self._connect = connect
        self._hidden = frozenset(hidden_zones)
        self._gamma = gamma  # see scale_colour
        self._glows: dict[str, _Glow] = {}  # device name -> device we draw ourselves
        self._clock = clock
        self._retry_at = 0.0  # do not try to connect before this time
        self._client = None

    @property
    def connected(self) -> bool:
        return self._client is not None

    def try_connect(self) -> bool:
        """Connect if not connected. True once OpenRGB has devices for us."""
        if self._client is not None:
            return True
        started = self._clock()
        if started < self._retry_at:
            return False
        try:
            client = self._connect()
            if not client.devices:
                # OpenRGB is up but still detecting hardware; ask again later.
                _disconnect(client)
                return False
        except Exception as error:  # the library raises many different types
            log.debug("Case lights not available: %s", error)
            if self._clock() - started > SLOW_FAILURE_S:
                self._retry_at = self._clock() + RETRY_AFTER_SLOW_FAILURE_S
            return False
        self._client = client
        log.info("Case lights connected, %d device(s)", len(client.devices))
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
            log.warning("Lost connection to the case lights: %s", error)
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
        self._glows.clear()

    def forget(self) -> None:
        """Assume the hardware has lost its state (after sleep): the next
        apply sets everything up from scratch, including direct mode."""
        self._glows.clear()

    # --- colours drawn by us: fades and breathing --------------------------------

    @property
    def animating(self) -> bool:
        """True while tick_animation() needs to be called many times a second."""
        return any(glow.active for glow in self._glows.values())

    def tick_animation(self, now: float) -> None:
        """Send the next frame of every running fade or breath. `now` is in seconds."""
        try:
            for glow in self._glows.values():
                if glow.active:
                    self._advance(glow, now)
        except Exception as error:
            log.warning("Lost connection to the case lights while animating: %s", error)
            self._drop()

    def refresh(self) -> None:
        """Send the steady colours again.

        A lighting board can reset itself (while the computer is still
        starting up, or when USB settles) and forget what it was showing.
        Called every few seconds, this brings the colours back. Devices that
        are fading or breathing are being sent frames anyway.
        """
        try:
            for glow in self._glows.values():
                if not glow.active:
                    _send_frame(glow, 1.0)
        except Exception as error:
            log.warning("Lost connection to the case lights: %s", error)
            self._drop()

    def finish_transitions(self) -> None:
        """Jump every running fade to its end (used when there is no time)."""
        try:
            for glow in self._glows.values():
                if glow.fading:
                    glow.shown, glow.fading = list(glow.target), False
                    _send_frame(glow, 1.0)
        except Exception as error:
            log.warning("Lost connection to the case lights: %s", error)
            self._drop()

    def _advance(self, glow: _Glow, now: float) -> None:
        if glow.fading:
            if glow.fade_started_at is None:
                glow.fade_started_at = now
            progress = min(1.0, (now - glow.fade_started_at) / FADE_S)
            glow.shown = [
                tuple(a + (b - a) * progress for a, b in zip(start, end, strict=True))
                for start, end in zip(glow.fade_from, glow.target, strict=True)
            ]
            glow.fading = progress < 1.0
        level = 1.0
        if glow.breath is not None:
            if glow.breath_started_at is None:
                glow.breath_started_at = now
            level = breath_level(
                now - glow.breath_started_at,
                glow.breath.speed,
                glow.breath.min_brightness,
                glow.breath.max_brightness,
            )
        _send_frame(glow, level)

    def _draw(self, device, by_name, enabled, brightness, wanted, colours, options) -> None:
        """Show `device` in a plain colour or breathing, gliding to the new look."""
        target: list[Rgb] = []
        for zone in device.zones:
            key = zone_key(device.name, zone.name)
            if key in self._hidden or not enabled:
                colour = (0, 0, 0)
            else:
                colour = scale_colour(colours.get(key, DEFAULT_COLOUR), brightness, self._gamma)
            target.extend([colour] * len(zone.leds))
        breath = options if enabled and wanted == SOFTWARE_BREATHING else None

        glow = self._glows.get(device.name)
        if glow is None:
            # First time: there is no old look to glide from.
            device.set_mode(by_name[FALLBACK_MODE])
            glow = self._glows[device.name] = _Glow(device, list(target), list(target))
            glow.breath = breath
            if breath is None:
                _send_frame(glow, 1.0)
            return
        if target != glow.target:
            glow.fade_from, glow.target = list(glow.shown), target
            glow.fading, glow.fade_started_at = True, None
        if breath is None and glow.breath is not None and not glow.fading:
            _send_frame(glow, 1.0)  # breathing stopped: settle at full level
        if breath is None:
            glow.breath_started_at = None
        glow.breath = breath  # a breath already running keeps its rhythm

    def _apply_device(self, device, enabled, brightness, modes, colours, options) -> None:
        by_name = {mode.name: mode for mode in device.modes}
        if not by_name:
            return
        zones = self._visible_zones(device)
        zone_colours = [
            Colour(
                *scale_colour(
                    colours.get(zone_key(device.name, zone.name), DEFAULT_COLOUR),
                    brightness if enabled else 0,
                    self._gamma,
                )
            )
            for zone in zones
        ]

        wanted = modes.get(device.name, DEFAULT_MODE) if enabled else DEFAULT_MODE
        if FALLBACK_MODE in by_name and wanted in SOFTWARE_MODES and wanted in by_name:
            self._draw(device, by_name, enabled, brightness, wanted, colours, options)
            return
        self._glows.pop(device.name, None)

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


def _send_frame(glow: _Glow, level: float) -> None:
    """Send the glow's current colours, dimmed to `level` (0.0 .. 1.0)."""
    frame = [Colour(*(round(part * level) for part in colour)) for colour in glow.shown]
    glow.device.set_colors(frame, fast=True)


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
