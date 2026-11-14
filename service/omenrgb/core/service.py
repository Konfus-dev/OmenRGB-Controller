# SPDX-License-Identifier: GPL-3.0-or-later
"""The background service: turns settings into what the hardware shows.

It watches the settings the GNOME Shell extension writes, keeps the chosen
image on the cooler LCD, asks OpenRGB for the chosen lights, and writes a
small status report back for the settings window to display.
"""

import json
import logging
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from . import constants as c
from .image_prep import prepare_jpeg
from .interfaces import ManualScheduler, Scheduler, Settings, WatchFile
from .lcd_streamer import LcdStreamer
from .lights import DeviceInfo, EffectOptions, Lights, UniformColours, zone_key
from .look import Look, Looks
from .ram import RamLights

log = logging.getLogger(__name__)

LCD_IMAGE_KEYS = ("lcd-image", "lcd-fit")
LCD_BRIGHTNESS_KEYS = ("lcd-enabled", "lcd-brightness")
AUTO_KEYS = (
    "auto-enabled",
    "auto-lights",
    "auto-lcd",
    "auto-idle-image",
    "auto-idle-colour",
    "focused-app",
)
# Changes arriving within this time of each other are sent as one. It only
# needs to cover a slider being dragged; fades hide the rest.
LIGHTS_DEBOUNCE_MS = 40
LCD_FADE_STEPS = 8  # frames in a crossfade between two LCD pictures
LCD_FADE_INTERVAL_MS = 45  # time between those frames (about 0.36 s in all)
SLEEP_WAIT_S = 3.0  # how long to wait for the RAM to go dark before sleeping
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
        settings: Settings,
        streamer: LcdStreamer,
        lights: Lights,
        prepare: Callable[[Path, str], bytes] = prepare_jpeg,
        ram: RamLights | None = None,
        display_names: dict[str, str] | None = None,
        looks: Looks | None = None,
        fade: Callable[[bytes | None, bytes | None, int], Iterator[bytes]] | None = None,
        scheduler: Scheduler | None = None,
        watch_file: WatchFile | None = None,
    ):
        """Everything the service needs is handed in, so this class knows
        nothing about any particular operating system or hardware library:

        settings    where the front end's choices live (see interfaces.Settings)
        streamer    keeps a picture on the LCD
        lights      the case lights
        ram         the RAM lights, or None where they cannot be controlled
        prepare     turns a picture file into an LCD-ready JPEG
        looks       auto mode's source of looks, or None for no auto mode
        fade        makes LCD crossfade frames, or None to switch at once
        scheduler   runs our timers (see interfaces.Scheduler)
        watch_file  tells us when a file changes, or None not to watch
        display_names  friendlier names for devices and zones
        """
        self._scheduler: Scheduler = scheduler or ManualScheduler()
        self._watch_file = watch_file
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
        self._target_jpeg: bytes | None = None  # the picture it should end up showing
        self._fade = fade  # makes crossfade frames; None switches pictures at once
        self._fade_iter: Iterator[bytes] | None = None  # frames of a running fade
        self._fade_timer = None
        self._asleep = False  # between "going to sleep" and "woke up"
        self._lights_timer = None  # timer handle while a lights update is waiting
        self._animation_timer = None  # timer handle while an effect is being animated
        self._stop_watching = None  # stops watching the chosen LCD image file
        self._watched_path: str | None = None
        self._error = ""  # last image problem, shown in the settings window
        self._timers: list = []  # the always-running timers, so stop() can cancel them
        self._started = False

    # --- lifecycle -------------------------------------------------------------

    def start(self) -> None:
        """Apply the current settings, then keep everything up to date."""
        self._settings.subscribe(self.on_changed)
        self._started = True
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

        every = self._scheduler.every
        self._timers = [
            every(c.CLAIM_INTERVAL_MS, lambda: self._repeat(self._streamer.tick_claim)),
            every(c.FRAME_INTERVAL_MS, lambda: self._repeat(self._streamer.tick_frame)),
            every(c.RECONNECT_INTERVAL_S * 1000, lambda: self._repeat(self.tick_slow)),
        ]

    def stop(self) -> None:
        for timer in (*self._timers, self._lights_timer, self._animation_timer, self._fade_timer):
            if timer is not None:
                self._scheduler.cancel(timer)
        self._timers = []
        self._lights_timer = self._animation_timer = self._fade_timer = None
        self._watch_image_file(None)
        if self._started:
            self._started = False
            self._settings.unsubscribe(self.on_changed)
            # Tell the front end nobody is driving the hardware now.
            self._settings.set("status", "{}")

    @staticmethod
    def _repeat(tick: Callable[[], None]) -> bool:
        try:
            tick()
        except Exception:
            # A timer whose callback raises is usually dropped for good; one
            # bad tick must not stop the LCD or the reconnect checks.
            log.exception("Unexpected error in a timer; carrying on")
        return True  # tells the scheduler to keep the timer running

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
        """Work out what auto mode wants shown right now.

        An app (or game) with a usable icon gets its own look. Nothing in
        focus, or something without an icon, gets the idle look: the OS
        logo and its colour unless the owner chose otherwise. Apps on the
        ignore list leave the owner's normal settings showing.
        """
        self._look = None
        if not (self._looks and self._settings.get("auto-enabled")):
            return
        subject = self._settings.get("focused-app")
        if self._looks.is_ignored(subject):
            return
        self._look = self._looks.look_for(subject) or self._idle_look()
        log.debug(
            "Auto mode: in focus %r -> colour %s, %s picture",
            subject,
            self._look.colour if self._look else None,
            "a" if self._look else "no",
        )

    def _idle_look(self) -> Look | None:
        idle = self._looks.idle_look(self._settings.get("auto-idle-image"))
        colour = self._settings.get("auto-idle-colour")
        if idle and colour:
            idle = Look(colour, idle.jpeg)
        return idle

    def _auto_lcd(self) -> bytes | None:
        """The picture auto mode wants on the LCD, if any."""
        if self._look and self._settings.get("auto-lcd"):
            return self._look.jpeg
        return None

    def _auto_colour(self) -> str | None:
        """The colour auto mode wants on the lights, if any."""
        if self._look and self._settings.get("auto-lights"):
            return self._look.colour
        return None

    # --- LCD ---------------------------------------------------------------------

    def _watch_image_file(self, path: str | None) -> None:
        """Notice when the chosen image file is edited or replaced."""
        if path == self._watched_path:
            return
        if self._stop_watching:
            self._stop_watching()
        self._stop_watching, self._watched_path = None, path
        if path and self._watch_file:
            self._stop_watching = self._watch_file(
                path, lambda: self._repeat(self.on_image_file_changed)
            )

    def on_image_file_changed(self) -> None:
        """The chosen image was edited on disk: show the new version."""
        self._load_own_image()
        self._show_lcd_image()
        self.publish_status()

    def on_sleep(self) -> None:
        """The computer is about to sleep: switch the LCD and all lights off.

        There are only a few seconds before it sleeps, so nothing fades.
        """
        log.info("Going to sleep; switching LCD and lights off")
        self._asleep = True
        self.finish_lcd_fade()
        self._update_lcd_brightness()
        self._streamer.tick_claim()  # sends the new brightness now
        self.flush_lights()
        self._lights.finish_transitions()
        if self._ram:
            self._ram.wait_idle(SLEEP_WAIT_S)

    def on_resume(self) -> None:
        """The computer woke from sleep: switch everything back on. The
        hardware may have forgotten its settings, so send them all again."""
        log.info("Woke from sleep; restoring LCD and lights")
        self._asleep = False
        self._update_lcd_brightness()
        self._streamer.tick_claim()
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
        path = self._settings.get("lcd-image")
        self._watch_image_file(path or None)
        if not path:
            self._own_jpeg, self._error = None, ""
            return
        try:
            self._own_jpeg = self._prepare(Path(path), self._settings.get("lcd-fit"))
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
        if picture == self._target_jpeg:
            return
        self._target_jpeg = picture
        if self._fade is None:
            self._shown_jpeg = picture
            self._streamer.set_image(picture)
            self._streamer.tick_frame()  # now, not at the next half-second tick
            return
        # Crossfade from whatever is on screen at this moment.
        self._fade_iter = self._fade(self._shown_jpeg, picture, LCD_FADE_STEPS)
        self.tick_lcd_fade()
        if self._fade_iter is not None and self._fade_timer is None:
            self._fade_timer = self._scheduler.every(LCD_FADE_INTERVAL_MS, self._on_fade_timer)

    @property
    def lcd_fading(self) -> bool:
        return self._fade_iter is not None

    def _on_fade_timer(self) -> bool:
        self._repeat(self.tick_lcd_fade)
        if self._fade_iter is None:
            self._fade_timer = None
            return False  # fade finished: stop the timer
        return True

    def tick_lcd_fade(self) -> None:
        """Show the next frame of a running crossfade."""
        if self._fade_iter is None:
            return
        try:
            frame = next(self._fade_iter, None)
        except Exception as error:
            log.warning("LCD fade failed: %s", error)
            frame = None
        if frame is None:
            self._fade_iter = None
            if self._shown_jpeg != self._target_jpeg:
                # Either the fade failed, or there is no picture any more
                # (None lets the LCD go back to its own logo).
                self._shown_jpeg = self._target_jpeg
                self._streamer.set_image(self._target_jpeg)
            return
        self._shown_jpeg = frame
        self._streamer.set_image(frame)
        self._streamer.tick_frame()

    def finish_lcd_fade(self) -> None:
        """Run a crossfade straight to its end."""
        while self._fade_iter is not None:
            self.tick_lcd_fade()

    def _update_lcd_brightness(self) -> None:
        enabled = self._settings.get("lcd-enabled") and not self._asleep
        self._streamer.set_brightness(self._settings.get("lcd-brightness") if enabled else 0)

    def _update_lights(self) -> None:
        self._sent_auto_colour = self._auto_colour()
        self._lights.apply(*self._light_settings())
        self._sync_animation_timer()

    @property
    def lights_update_pending(self) -> bool:
        """True while a batched lights update is waiting to be sent."""
        return self._lights_timer is not None

    @property
    def animation_running(self) -> bool:
        return self._animation_timer is not None

    def _sync_animation_timer(self) -> None:
        """Run the fast animation timer only while something is animated."""
        if self._lights.animating and self._animation_timer is None:
            self._animation_timer = self._scheduler.every(
                ANIMATION_INTERVAL_MS, self._on_animation_timer
            )
        elif not self._lights.animating and self._animation_timer is not None:
            self._scheduler.cancel(self._animation_timer)
            self._animation_timer = None

    def _on_animation_timer(self) -> bool:
        self._repeat(self._tick_animation)
        if not self._lights.animating:
            self._animation_timer = None
            return False  # fade finished and nothing is breathing: stop the timer
        return True

    def _tick_animation(self) -> None:
        self._lights.tick_animation(time.monotonic())

    def _light_settings(self) -> tuple[bool, int, dict, dict, dict]:
        """The arguments for Lights.apply / RamLights.apply."""

        def setting(key: str) -> dict:
            return self._settings.get(key)

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
            self._settings.get("lights-enabled") and not self._asleep,
            self._settings.get("lights-brightness"),
            setting("device-modes"),
            UniformColours(auto) if (auto := self._auto_colour()) else setting("zone-colours"),
            options,
        )

    def _schedule_lights(self) -> None:
        """Update the lights shortly, once, however many changes arrive.

        Setting lights takes a few tenths of a second, so while a slider is
        being dragged only the final value is worth sending.
        """
        if self._lights_timer is None:
            self._lights_timer = self._scheduler.every(LIGHTS_DEBOUNCE_MS, self._on_lights_timer)

    def _on_lights_timer(self) -> bool:
        self._lights_timer = None
        self._repeat(self.flush_lights)
        return False  # one-shot timer

    def flush_lights(self) -> None:
        """Send the current light settings to the case and the RAM now."""
        if self._lights_timer is not None:
            self._scheduler.cancel(self._lights_timer)
            self._lights_timer = None
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
        else:
            self._lights.refresh()  # in case the board forgot its colours
        if self._ram and not self._ram.check() and self._ram.try_connect():
            self._update_ram()
        self._sync_animation_timer()  # the connection may have come or gone
        self.publish_status()

    def _followed_app(self) -> str:
        """The app whose look is showing; empty for the idle look or none."""
        subject = self._settings.get("focused-app")
        if self._look and self._looks and self._looks.look_for(subject) == self._look:
            return subject
        return ""

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
                    "app": self._followed_app(),
                    "colour": (self._look.colour or "") if self._look else "",
                },
                "devices": [
                    self._describe(device)
                    for device in self._lights.devices()
                    + (self._ram.devices() if self._ram else [])
                ],
            }
        )
        if status != self._settings.get("status"):
            self._settings.set("status", status)
