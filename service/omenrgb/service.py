# SPDX-License-Identifier: GPL-3.0-or-later
"""The background service: turns settings into what the hardware shows.

It watches the settings the GNOME Shell extension writes, keeps the chosen
image on the cooler LCD, asks OpenRGB for the chosen lights, and writes a
small status report back for the settings window to display.
"""

import argparse
import json
import logging
import signal
import time
from collections.abc import Callable
from pathlib import Path

from gi.repository import Gio, GLib

from . import constants as c
from .image_prep import prepare_jpeg
from .lcd_device import LcdDevice, find_hidraw
from .lcd_streamer import LcdStreamer
from .lights import DeviceInfo, EffectOptions, Lights, zone_key
from .ram import RamLights
from .settings import load_settings

log = logging.getLogger(__name__)

LCD_IMAGE_KEYS = ("lcd-image", "lcd-fit")
LCD_BRIGHTNESS_KEYS = ("lcd-enabled", "lcd-brightness")
LIGHTS_DEBOUNCE_MS = 250
ANIMATION_INTERVAL_MS = 33  # about 30 frames a second while an effect is animated
LIGHT_KEYS = (
    "lights-enabled",
    "lights-brightness",
    "device-modes",
    "zone-colours",
    "device-speeds",
    "device-min-brightness",
    "device-max-brightness",
)


class Service:
    def __init__(
        self,
        settings: Gio.Settings,
        streamer: LcdStreamer,
        lights: Lights,
        prepare: Callable[[Path, str], bytes] = prepare_jpeg,
        ram: RamLights | None = None,
        display_names: dict[str, str] | None = None,
    ):
        self._settings = settings
        self._streamer = streamer
        self._lights = lights
        self._prepare = prepare
        self._ram = ram  # None on machines without controllable RAM lights
        self._display_names = display_names or {}
        self._lights_timer = 0  # GLib timer id while a lights update is waiting
        self._animation_timer = 0  # GLib timer id while an effect is being animated
        self._error = ""  # last image problem, shown in the settings window
        self._sources: list[int] = []  # GLib timer ids, so stop() can remove them
        self._handler = 0

    # --- lifecycle -------------------------------------------------------------

    def start(self) -> None:
        """Apply the current settings, then keep everything up to date."""
        self._handler = self._settings.connect("changed", lambda _s, key: self.on_changed(key))
        self._update_lcd_image()
        self._update_lcd_brightness()
        self._streamer.tick_reconnect()
        if self._lights.try_connect():
            self._update_lights()
        if self._ram and self._ram.try_connect():
            self._update_ram()
        self.publish_status()

        self._sources = [
            GLib.timeout_add(c.CLAIM_INTERVAL_MS, self._repeat, self._streamer.tick_claim),
            GLib.timeout_add(c.FRAME_INTERVAL_MS, self._repeat, self._streamer.tick_frame),
            GLib.timeout_add_seconds(c.RECONNECT_INTERVAL_S, self._repeat, self.tick_slow),
        ]

    def stop(self) -> None:
        for source in self._sources:
            GLib.source_remove(source)
        self._sources = []
        for timer in (self._lights_timer, self._animation_timer):
            if timer:
                GLib.source_remove(timer)
        self._lights_timer = self._animation_timer = 0
        if self._handler:
            self._settings.disconnect(self._handler)
            self._handler = 0

    @staticmethod
    def _repeat(tick: Callable[[], None]) -> bool:
        try:
            tick()
        except Exception:
            # GLib silently drops a timer whose callback raises; a single
            # bad tick must not stop the LCD or the reconnect checks for good.
            log.exception("Unexpected error in a timer; carrying on")
        return True  # tells GLib to keep the timer running

    # --- reacting to settings ------------------------------------------------

    def on_changed(self, key: str) -> None:
        """Called whenever one setting changes."""
        if key in LCD_IMAGE_KEYS:
            self._update_lcd_image()
            self.publish_status()
        elif key in LCD_BRIGHTNESS_KEYS:
            self._update_lcd_brightness()
        elif key in LIGHT_KEYS:
            self._schedule_lights()

    def _update_lcd_image(self) -> None:
        path = self._settings.get_string("lcd-image")
        if not path:
            self._streamer.set_image(None)
            self._error = ""
            return
        try:
            jpeg = self._prepare(Path(path), self._settings.get_string("lcd-fit"))
        except Exception as error:
            # Normally an ImageError. Either way: keep showing whatever was
            # there before, and never let a bad picture stop the service.
            log.warning("%s", error)
            self._error = str(error)
            return
        self._error = ""
        self._streamer.set_image(jpeg)

    def _update_lcd_brightness(self) -> None:
        enabled = self._settings.get_boolean("lcd-enabled")
        self._streamer.set_brightness(self._settings.get_int("lcd-brightness") if enabled else 0)

    def _update_lights(self) -> None:
        self._lights.apply(*self._light_settings())
        self._sync_animation_timer()

    @property
    def animation_running(self) -> bool:
        return bool(self._animation_timer)

    def _sync_animation_timer(self) -> None:
        """Run the fast animation timer only while something is animated."""
        if self._lights.animating and not self._animation_timer:
            self._animation_timer = GLib.timeout_add(
                ANIMATION_INTERVAL_MS, self._repeat, self._tick_animation
            )
        elif not self._lights.animating and self._animation_timer:
            GLib.source_remove(self._animation_timer)
            self._animation_timer = 0

    def _tick_animation(self) -> None:
        self._lights.tick_animation(time.monotonic())

    def _light_settings(self) -> tuple[bool, int, dict, dict, dict]:
        """The arguments for Lights.apply / RamLights.apply."""

        def setting(key: str) -> dict:
            return self._settings.get_value(key).unpack()

        speeds = setting("device-speeds")
        dimmest = setting("device-min-brightness")
        brightest = setting("device-max-brightness")
        defaults = EffectOptions()
        options = {
            name: EffectOptions(
                speed=speeds.get(name, defaults.speed),
                min_brightness=dimmest.get(name, defaults.min_brightness),
                max_brightness=brightest.get(name, defaults.max_brightness),
            )
            for name in {*speeds, *dimmest, *brightest}
        }
        return (
            self._settings.get_boolean("lights-enabled"),
            self._settings.get_int("lights-brightness"),
            setting("device-modes"),
            setting("zone-colours"),
            options,
        )

    def _schedule_lights(self) -> None:
        """Update the lights shortly, once, however many changes arrive.

        Setting lights takes a few tenths of a second, so while a slider is
        being dragged only the final value is worth sending.
        """
        if not self._lights_timer:
            self._lights_timer = GLib.timeout_add(LIGHTS_DEBOUNCE_MS, self._on_lights_timer)

    def _on_lights_timer(self) -> bool:
        self._lights_timer = 0
        self._repeat(self.flush_lights)
        return False  # one-shot timer

    def flush_lights(self) -> None:
        """Send the current light settings to the case and the RAM now."""
        if self._lights_timer:
            GLib.source_remove(self._lights_timer)
            self._lights_timer = 0
        self._update_lights()
        self._update_ram()

    def _update_ram(self) -> None:
        if self._ram:
            self._ram.apply(*self._light_settings())

    # --- housekeeping --------------------------------------------------------

    def tick_slow(self) -> None:
        """Every few seconds: reconnect whatever went away, refresh status."""
        self._streamer.tick_reconnect()
        if not self._lights.check() and self._lights.try_connect():
            self._update_lights()
        if self._ram and not self._ram.check() and self._ram.try_connect():
            self._update_ram()
        self._sync_animation_timer()  # the connection may have come or gone
        self.publish_status()

    def _describe(self, device: DeviceInfo) -> dict:
        """One lighting device as the settings window needs it.

        "name" and "zones" are the real names, used as keys when saving.
        "label" and "zone_labels" are what a person should read.
        """
        names = self._display_names
        return {
            "name": device.name,
            "label": names.get(device.name, device.name),
            "modes": list(device.modes),
            "default_mode": device.default_mode,
            "colourless_modes": list(device.colourless_modes),
            "speed_modes": list(device.speed_modes),
            "range_modes": list(device.range_modes),
            "zones": list(device.zones),
            "zone_labels": [names.get(zone_key(device.name, zone), zone) for zone in device.zones],
        }

    def publish_status(self) -> None:
        """Tell the settings window what we found. Only writes on change."""
        status = json.dumps(
            {
                "lcd": self._streamer.connected,
                "openrgb": self._lights.connected,
                "ram": bool(self._ram and self._ram.connected),
                "error": self._error,
                "devices": [
                    self._describe(device)
                    for device in self._lights.devices()
                    + (self._ram.devices() if self._ram else [])
                ],
            }
        )
        if status != self._settings.get_string("status"):
            self._settings.set_string("status", status)


def _open_lcd() -> LcdDevice | None:
    path = find_hidraw()
    return LcdDevice(path) if path else None


def _connect_openrgb():
    from openrgb import OpenRGBClient  # imported late: it is slow to load

    return OpenRGBClient(c.OPENRGB_HOST, c.OPENRGB_PORT, "omenrgb")


def default_schema_dir() -> Path:
    return (
        Path(GLib.get_user_data_dir()) / "gnome-shell" / "extensions" / c.EXTENSION_UUID / "schemas"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema-dir", type=Path, default=default_schema_dir())
    parser.add_argument("--verbose", action="store_true", help="log more detail")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        settings = load_settings(args.schema_dir)
    except FileNotFoundError as error:
        log.error("%s", error)
        return 1

    service = Service(
        settings,
        LcdStreamer(_open_lcd),
        Lights(_connect_openrgb, c.HIDDEN_ZONES, c.LED_GAMMA),
        ram=RamLights(gamma=c.LED_GAMMA),
        display_names=c.DISPLAY_NAMES,
    )
    loop = GLib.MainLoop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, loop.quit)
    service.start()
    log.info("omenrgb service running")
    loop.run()
    service.stop()
    return 0
