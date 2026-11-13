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
from collections.abc import Callable
from pathlib import Path

from gi.repository import Gio, GLib

from . import constants as c
from .image_prep import ImageError, prepare_jpeg
from .lcd_device import LcdDevice, find_hidraw
from .lcd_streamer import LcdStreamer
from .lights import Lights
from .settings import load_settings

log = logging.getLogger(__name__)

LCD_IMAGE_KEYS = ("lcd-image", "lcd-fit")
LCD_BRIGHTNESS_KEYS = ("lcd-enabled", "lcd-brightness")
LIGHT_KEYS = ("lights-enabled", "lights-brightness", "device-modes", "zone-colours")


class Service:
    def __init__(
        self,
        settings: Gio.Settings,
        streamer: LcdStreamer,
        lights: Lights,
        prepare: Callable[[Path, str], bytes] = prepare_jpeg,
    ):
        self._settings = settings
        self._streamer = streamer
        self._lights = lights
        self._prepare = prepare
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
        if self._handler:
            self._settings.disconnect(self._handler)
            self._handler = 0

    @staticmethod
    def _repeat(tick: Callable[[], None]) -> bool:
        tick()
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
            self._update_lights()

    def _update_lcd_image(self) -> None:
        path = self._settings.get_string("lcd-image")
        if not path:
            self._streamer.set_image(None)
            self._error = ""
            return
        try:
            jpeg = self._prepare(Path(path), self._settings.get_string("lcd-fit"))
        except ImageError as error:
            # Keep showing whatever was there before.
            log.warning("%s", error)
            self._error = str(error)
            return
        self._error = ""
        self._streamer.set_image(jpeg)

    def _update_lcd_brightness(self) -> None:
        enabled = self._settings.get_boolean("lcd-enabled")
        self._streamer.set_brightness(self._settings.get_int("lcd-brightness") if enabled else 0)

    def _update_lights(self) -> None:
        self._lights.apply(
            self._settings.get_boolean("lights-enabled"),
            self._settings.get_int("lights-brightness"),
            self._settings.get_value("device-modes").unpack(),
            self._settings.get_value("zone-colours").unpack(),
        )

    # --- housekeeping --------------------------------------------------------

    def tick_slow(self) -> None:
        """Every few seconds: reconnect whatever went away, refresh status."""
        self._streamer.tick_reconnect()
        if not self._lights.check() and self._lights.try_connect():
            self._update_lights()
        self.publish_status()

    def publish_status(self) -> None:
        """Tell the settings window what we found. Only writes on change."""
        status = json.dumps(
            {
                "lcd": self._streamer.connected,
                "openrgb": self._lights.connected,
                "error": self._error,
                "devices": [
                    {"name": d.name, "modes": list(d.modes), "zones": list(d.zones)}
                    for d in self._lights.devices()
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

    service = Service(settings, LcdStreamer(_open_lcd), Lights(_connect_openrgb, c.HIDDEN_ZONES))
    loop = GLib.MainLoop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, loop.quit)
    service.start()
    log.info("omenrgb service running")
    loop.run()
    service.stop()
    return 0
