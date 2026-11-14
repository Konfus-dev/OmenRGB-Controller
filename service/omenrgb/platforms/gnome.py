# SPDX-License-Identifier: GPL-3.0-or-later
"""The service on a GNOME desktop (developed on Ubuntu).

What this platform chooses:
- settings:     GSettings, shared with the GNOME Shell extension
                (frontends/gnome-shell), which is the front end
- case lights:  an OpenRGB server
- RAM lights:   the root helper, which asks the BIOS
- what is in focus: the app the extension reports; its icon comes from the
                icon theme. With nothing in focus the LCD shows what is in
                ~/.config/omenrgb/lcd/ if anything, else the wallpaper, and
                the lights take that picture's main colour
- sleep/wake:   systemd-logind
"""

import argparse
import logging
from pathlib import Path

from gi.repository import GLib

from .. import omen45l
from ..backends import hidraw, openrgb, ram_bios
from ..core.lcd_fade import fade_frames
from ..core.lcd_folder import load_lcd_folder
from ..core.lcd_streamer import LcdStreamer
from ..core.lights import Lights
from ..core.look import Looks, cover_lcd
from ..core.ram import RamLights
from ..core.service import Service
from . import glib_runtime, gnome_theme, gtk_icons, logind
from .gsettings_store import GSettingsStore, load_settings

log = logging.getLogger(__name__)

EXTENSION_UUID = "omenrgb@konfus-dev.github.io"

# Auto mode leaves the normal look alone while one of these apps has focus.
# The first is the window GNOME shows this project's settings in, so that
# you see the colours you are choosing while you choose them.
AUTO_IGNORED_APPS = ("org.gnome.Shell.Extensions.desktop", "org.gnome.Extensions.desktop")


def default_schema_dir() -> Path:
    extensions = Path(GLib.get_user_data_dir()) / "gnome-shell" / "extensions"
    return extensions / EXTENSION_UUID / "schemas"


def default_lcd_folder() -> Path:
    """Where the owner can put an animation or picture for the LCD to show
    when nothing is in focus (see core/lcd_folder.py)."""
    return Path(GLib.get_user_config_dir()) / "omenrgb" / "lcd"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema-dir", type=Path, default=default_schema_dir())
    parser.add_argument("--lcd-folder", type=Path, default=default_lcd_folder())
    parser.add_argument("--verbose", action="store_true", help="log more detail")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        settings = GSettingsStore(load_settings(args.schema_dir))
    except FileNotFoundError as error:
        log.error("%s", error)
        return 1

    theme = gnome_theme.DesktopTheme()

    def idle_picture():
        """What the owner put in the LCD folder; else the wallpaper; else
        the OS logo."""
        override = load_lcd_folder(args.lcd_folder, cover_lcd)
        if override is not None:
            return override
        wallpaper = theme.wallpaper()
        if wallpaper:
            try:
                return gtk_icons.draw_image_file(wallpaper)
            except Exception as error:  # GLib raises its own error types
                log.warning("Cannot use the wallpaper %s: %s", wallpaper, error)
        return gtk_icons.load_os_logo()

    looks = Looks(
        gtk_icons.load_app_icon,
        ignore=AUTO_IGNORED_APPS,
        load_idle=idle_picture,
        compose_idle=cover_lcd,
    )
    service = Service(
        settings,
        LcdStreamer(hidraw.open_lcd),
        Lights(openrgb.connect, omen45l.HIDDEN_ZONES, omen45l.LED_GAMMA),
        ram=RamLights(
            ram_bios.send_to_helper,
            ram_bios.find_sticks,
            gamma=omen45l.LED_GAMMA,
            background=True,
        ),
        display_names=omen45l.DISPLAY_NAMES,
        looks=looks,
        fade=fade_frames,
        scheduler=glib_runtime.GLibScheduler(),
        watch_file=glib_runtime.watch_file,
    )
    service.start()

    def theme_changed() -> None:
        looks.forget_idle()
        service.on_changed("auto-idle-image")  # makes the service look again

    theme.on_change(theme_changed)
    # Kept in a variable until we exit: the notifications stop once nothing
    # refers to the connection any more.
    sleep_watch = logind.watch_for_sleep(service.on_sleep, service.on_resume)
    log.info("omenrgb service running (GNOME)")
    glib_runtime.run_until_stopped()
    del sleep_watch
    service.stop()
    return 0
