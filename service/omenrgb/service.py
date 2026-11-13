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
from .app_look import AppLooks, Look, load_app_icon
from .image_prep import prepare_jpeg
from .lcd_device import LcdDevice, find_hidraw
from .lcd_streamer import LcdStreamer
from .lights import DeviceInfo, EffectOptions, Lights, UniformColours, zone_key
from .ram import RamLights
from .settings import load_settings

log = logging.getLogger(__name__)

LCD_IMAGE_KEYS = ("lcd-image", "lcd-fit")
LCD_BRIGHTNESS_KEYS = ("lcd-enabled", "lcd-brightness")
AUTO_KEYS = ("auto-enabled", "auto-lights", "auto-lcd", "focused-app")
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
        looks: AppLooks | None = None,
    ):
        self._settings = settings
        self._streamer = streamer
        self._lights = lights
        self._prepare = prepare
        self._ram = ram  # None on machines without controllable RAM lights
        self._display_names = display_names or {}
        self._looks = looks  # None switches auto mode off entirely
        self._look: Look | None = None  # what auto mode wants shown right now
        self._sent_auto_colour: str | None = None  # auto colour last sent to the lights
        self._own_jpeg: bytes | None = None  # the owner's chosen picture, LCD-ready
        self._shown_jpeg: bytes | None = None  # what the LCD was last told to show
        self._lights_timer = 0  # GLib timer id while a lights update is waiting
        self._animation_timer = 0  # GLib timer id while an effect is being animated
        self._image_monitor = None  # watches the chosen LCD image file
        self._watched_path: str | None = None
        self._error = ""  # last image problem, shown in the settings window
        self._sources: list[int] = []  # GLib timer ids, so stop() can remove them
        self._handler = 0

    # --- lifecycle -------------------------------------------------------------

    def start(self) -> None:
        """Apply the current settings, then keep everything up to date."""
        self._handler = self._settings.connect("changed", lambda _s, key: self.on_changed(key))
        self._refresh_look()
        self._load_own_image()
        self._show_lcd_image()
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
        self._watch_image_file(None)
        if self._handler:
            self._settings.disconnect(self._handler)
            self._handler = 0
            # Tell the settings window nobody is driving the hardware now.
            self._settings.set_string("status", "{}")

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
            self._load_own_image()
            self._show_lcd_image()
            self.publish_status()
        elif key in LCD_BRIGHTNESS_KEYS:
            self._update_lcd_brightness()
        elif key in LIGHT_KEYS:
            self._schedule_lights()
        elif key in AUTO_KEYS:
            self._refresh_look()
            self._show_lcd_image()
            if self._auto_colour() != self._sent_auto_colour:
                self._schedule_lights()  # only when the lights would really change
            self.publish_status()

    # --- auto mode -------------------------------------------------------------

    def _refresh_look(self) -> None:
        """Work out what auto mode wants for the app that now has focus."""
        self._look = None
        if self._looks and self._settings.get_boolean("auto-enabled"):
            self._look = self._looks.look_for(self._settings.get_string("focused-app"))

    def _auto_lcd(self) -> bytes | None:
        """The picture auto mode wants on the LCD, if any."""
        if self._look and self._settings.get_boolean("auto-lcd"):
            return self._look.jpeg
        return None

    def _auto_colour(self) -> str | None:
        """The colour auto mode wants on the lights, if any."""
        if self._look and self._settings.get_boolean("auto-lights"):
            return self._look.colour
        return None

    # --- LCD ---------------------------------------------------------------------

    def _watch_image_file(self, path: str | None) -> None:
        """Notice when the chosen image file is edited or replaced."""
        if path == self._watched_path:
            return
        if self._image_monitor:
            self._image_monitor.cancel()
        self._image_monitor, self._watched_path = None, path
        if path:
            try:
                self._image_monitor = Gio.File.new_for_path(path).monitor_file(
                    Gio.FileMonitorFlags.NONE, None
                )
            except GLib.Error as error:
                log.debug("Cannot watch %s: %s", path, error)
                return
            self._image_monitor.connect("changed", self._on_image_monitor_event)

    def _on_image_monitor_event(self, _monitor, _file, _other, event) -> None:
        # CHANGES_DONE_HINT arrives once the program saving the file is done.
        if event in (Gio.FileMonitorEvent.CHANGES_DONE_HINT, Gio.FileMonitorEvent.CREATED):
            self._repeat(self.on_image_file_changed)

    def on_image_file_changed(self) -> None:
        """The chosen image was edited on disk: show the new version."""
        self._load_own_image()
        self._show_lcd_image()
        self.publish_status()

    def on_resume(self) -> None:
        """The computer woke from sleep: the hardware may have forgotten its
        settings, so send them all again."""
        log.info("Woke from sleep; re-applying lights")
        self._lights.forget()
        if self._ram:
            self._ram.forget()
        self.flush_lights()

    def _load_own_image(self) -> None:
        """Read the owner's chosen picture from disk and get it LCD-ready.

        Done only when the choice or the file changes, never on every app
        switch: decoding a large photo takes long enough to make the LCD and
        a breathing effect stutter.
        """
        path = self._settings.get_string("lcd-image")
        self._watch_image_file(path or None)
        if not path:
            self._own_jpeg, self._error = None, ""
            return
        try:
            self._own_jpeg = self._prepare(Path(path), self._settings.get_string("lcd-fit"))
            self._error = ""
        except Exception as error:
            # Normally an ImageError. Either way: keep the previous picture,
            # and never let a bad one stop the service.
            log.warning("%s", error)
            self._error = str(error)

    def _show_lcd_image(self) -> None:
        """Put the right picture on the LCD: auto mode's, else the owner's."""
        auto = self._auto_lcd()
        picture = auto if auto is not None else self._own_jpeg
        if picture != self._shown_jpeg:
            self._shown_jpeg = picture
            self._streamer.set_image(picture)

    def _update_lcd_brightness(self) -> None:
        enabled = self._settings.get_boolean("lcd-enabled")
        self._streamer.set_brightness(self._settings.get_int("lcd-brightness") if enabled else 0)

    def _update_lights(self) -> None:
        self._sent_auto_colour = self._auto_colour()
        self._lights.apply(*self._light_settings())
        self._sync_animation_timer()

    @property
    def lights_update_pending(self) -> bool:
        """True while a batched lights update is waiting to be sent."""
        return bool(self._lights_timer)

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
            UniformColours(auto) if (auto := self._auto_colour()) else setting("zone-colours"),
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
                "auto": {
                    "app": self._settings.get_string("focused-app") if self._look else "",
                    "colour": (self._look.colour or "") if self._look else "",
                },
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

    client = OpenRGBClient(c.OPENRGB_HOST, c.OPENRGB_PORT, "omenrgb")
    # The library waits up to 10 seconds for each answer by default, which
    # would freeze this service (and the LCD) if OpenRGB ever hung.
    client.comms.sock.settimeout(c.OPENRGB_TIMEOUT_S)
    return client


RESUME_SETTLE_S = 3  # give USB devices a moment to come back after sleep


def _watch_for_resume(service: Service) -> Gio.DBusConnection | None:
    """Call service.on_resume() whenever the computer wakes from sleep.

    The caller must keep the returned connection for as long as it wants the
    notifications: GLib drops the connection, and the subscription with it,
    once nothing refers to it any more.
    """

    def on_signal(_bus, _sender, _path, _iface, _signal, parameters) -> None:
        (going_to_sleep,) = parameters.unpack()
        if not going_to_sleep:
            GLib.timeout_add_seconds(RESUME_SETTLE_S, _once, service.on_resume)

    def _once(callback) -> bool:
        Service._repeat(callback)
        return False

    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    except GLib.Error as error:
        log.warning("Cannot watch for sleep/resume: %s", error)
        return None
    bus.signal_subscribe(
        "org.freedesktop.login1",
        "org.freedesktop.login1.Manager",
        "PrepareForSleep",
        "/org/freedesktop/login1",
        None,
        Gio.DBusSignalFlags.NONE,
        on_signal,
    )
    return bus


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
        looks=AppLooks(load_app_icon, ignore=c.AUTO_IGNORED_APPS),
    )
    loop = GLib.MainLoop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, loop.quit)
    service.start()
    system_bus = _watch_for_resume(service)  # kept alive until we exit
    log.info("omenrgb service running")
    loop.run()
    del system_bus
    service.stop()
    return 0
