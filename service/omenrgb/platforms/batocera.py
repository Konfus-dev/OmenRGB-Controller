# SPDX-License-Identifier: GPL-3.0-or-later
"""The service on Batocera (a console-style system; everything runs as root).

What this platform chooses:
- settings:     a JSON file the owner can edit,
                /userdata/system/configs/omen-lights/settings.json
- front end:    the "OMEN_LIGHTS" switch under System Settings > Services,
                plus a game hook (frontends/batocera) that writes the path
                of the running game to a small file
- case lights:  our own driver (backends/tracer.py); Batocera has no OpenRGB
- RAM lights:   straight through the BIOS, if the acpi_call kernel module
                is available; otherwise the RAM is left alone
- what is in focus: the running game. Its picture is the artwork Batocera
                scraped for it; with no game running, the Batocera logo.
- sleep/wake:   Batocera runs the scripts in frontends/batocera/power
                before sleeping and after waking; they signal this process
                (SIGUSR1 = going to sleep, SIGUSR2 = woke up)
"""

import argparse
import importlib.util
import logging
import signal
from collections.abc import Callable
from pathlib import Path

from PIL import Image

from .. import omen45l
from ..backends import hidraw, ram_bios, tracer
from ..core.lcd_fade import fade_frames
from ..core.lcd_streamer import LcdStreamer
from ..core.lights import Lights
from ..core.look import Looks, cover_lcd
from ..core.ram import RamLights
from ..core.service import Service
from . import batocera_games, glib_runtime
from .json_store import JsonSettings

log = logging.getLogger(__name__)

SETTINGS_FILE = Path("/userdata/system/configs/omen-lights/settings.json")
SUBJECT_FILE = Path("/var/run/omen-lights/now-playing")  # written by the game hook
POLL_MS = 1000  # how often the game hook's file and the settings are re-read
ASLEEP_MARKER = Path("/var/run/omen-lights/asleep")  # the suspend script waits for this
OS_LOGO = Path("/usr/share/batocera/splash/boot-logo.png")
ACPI_CALL = Path("/proc/acpi/call")
# The helper sits beside the "service" folder, in the repo and on the drive.
RAM_HELPER = Path(__file__).resolve().parents[3] / "helper" / "omenrgb_ram_helper.py"

# On a games machine, following the game is the point: on unless switched off.
DEFAULTS = {"auto-enabled": True}


def _load_picture(path: Path) -> Image.Image:
    with Image.open(path) as opened:
        return opened.convert("RGBA")


def _game_artwork(roms: Path):
    def load(rom: str) -> Image.Image | None:
        artwork = batocera_games.find_artwork(rom, roms)
        return _load_picture(artwork) if artwork else None

    return load


def _find_sticks() -> list[int]:
    """The fitted RAM sticks. Batocera's kernel does not list them, so the
    BIOS's tables are asked; the kernel's list is still tried first."""
    return ram_bios.find_sticks() or ram_bios.find_sticks_dmi()


def _ram_lights() -> RamLights | None:
    """RAM lights if the BIOS can be reached from here, else None."""
    if not (ACPI_CALL.exists() and RAM_HELPER.exists()):
        log.info("RAM lights unavailable (no acpi_call module); leaving the RAM alone")
        return None
    spec = importlib.util.spec_from_file_location("omenrgb_ram_helper", RAM_HELPER)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return RamLights(
        ram_bios.direct_sender(helper, _find_sticks),
        _find_sticks,
        gamma=omen45l.LED_GAMMA,
        background=True,
    )


def subject_reader(path: Path, settings) -> Callable[[], None]:
    """A function that copies the game hook's file into "focused-app"."""

    def read() -> None:
        try:
            subject = path.read_text().strip()
        except OSError:
            subject = ""
        settings.set("focused-app", subject)

    return read


def power_handlers(service, marker: Path) -> tuple[Callable[[], None], Callable[[], None]]:
    """What to do when told the system is going to sleep, and that it woke.

    Going to sleep ends by creating `marker`: the suspend script holds the
    system back until it appears, so the lights are really off first.
    """

    def sleep() -> None:
        try:
            service.on_sleep()
        except Exception:
            log.exception("Could not switch everything off before sleep")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()

    def wake() -> None:
        marker.unlink(missing_ok=True)
        try:
            service.on_resume()
        except Exception:
            log.exception("Could not restore the lights after waking")

    return sleep, wake


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, default=SETTINGS_FILE)
    parser.add_argument("--subject-file", type=Path, default=SUBJECT_FILE)
    parser.add_argument("--roms", type=Path, default=batocera_games.ROMS)
    parser.add_argument("--logo", type=Path, default=OS_LOGO)
    parser.add_argument("--verbose", action="store_true", help="log more detail")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    settings = JsonSettings(args.settings, DEFAULTS)
    service = Service(
        settings,
        LcdStreamer(hidraw.open_lcd),
        Lights(tracer.open_board, omen45l.HIDDEN_ZONES, omen45l.LED_GAMMA),
        ram=_ram_lights(),
        display_names=omen45l.DISPLAY_NAMES,
        looks=Looks(
            _game_artwork(args.roms),
            load_idle=lambda: _load_picture(args.logo),
            compose_idle=cover_lcd,  # the logo sits mid-picture: fill the screen with it
        ),
        fade=fade_frames,
        scheduler=glib_runtime.GLibScheduler(),
        watch_file=glib_runtime.watch_file,
    )
    # Three ways to hear that a game started or stopped, because missing it
    # means the PC never changes: the hook signals us (SIGHUP), the file is
    # watched, and it is looked at once a second regardless.
    args.subject_file.parent.mkdir(parents=True, exist_ok=True)
    args.subject_file.touch()
    read_subject = subject_reader(args.subject_file, settings)
    read_subject()
    glib_runtime.on_signal(signal.SIGHUP, read_subject)
    stop_following = glib_runtime.watch_file(str(args.subject_file), read_subject)
    stop_watching = glib_runtime.watch_file(str(args.settings), settings.reload)

    def look_again() -> bool:
        read_subject()
        settings.reload()  # picks up hand edits even if file watching fails
        return True

    glib_runtime.GLibScheduler().every(POLL_MS, look_again)
    service.start()
    going_to_sleep, woke_up = power_handlers(service, ASLEEP_MARKER)
    ASLEEP_MARKER.unlink(missing_ok=True)
    glib_runtime.on_signal(signal.SIGUSR1, going_to_sleep)
    glib_runtime.on_signal(signal.SIGUSR2, woke_up)
    log.info("omenrgb service running (Batocera)")
    glib_runtime.run_until_stopped()
    stop_following()
    stop_watching()
    service.stop()
    return 0
