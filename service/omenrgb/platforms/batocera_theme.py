# SPDX-License-Identifier: GPL-3.0-or-later
"""Reading the look of Batocera's menu from its active theme.

What goes on the LCD while no game is running, best first:

1. An animation the theme ships for this purpose: a folder `omen-lights/`
   in the theme, holding LCD-ready frames (480x480 JPEGs named so that they
   sort in order) and optionally a file `frame_ms` with the time per frame.
2. A still picture the theme ships: `omen-lights.png` (or .jpg, .webp).
3. The picture behind the menu's system list.

So any theme works, and a theme author can decide exactly what is shown.
"""

import logging
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from ..core.constants import MAX_JPEG_BYTES
from ..core.look import Animation

log = logging.getLogger(__name__)

ES_SETTINGS = Path("/userdata/system/configs/emulationstation/es_settings.cfg")
THEME_FOLDERS = (Path("/userdata/themes"), Path("/usr/share/emulationstation/themes"))
MAX_INCLUDE_DEPTH = 4  # themes split over several files include one another

LCD_NAME = "omen-lights"  # what a theme calls the LCD picture or animation it ships
STILL_TYPES = (".png", ".jpg", ".jpeg", ".webp")
DEFAULT_FRAME_MS = 100
MAX_FRAMES = 600  # a minute at ten frames a second


def lcd_animation(theme: Path) -> Animation | None:
    """The LCD animation a theme ships, or None."""
    folder = theme / LCD_NAME
    try:
        files = sorted(p for p in folder.iterdir() if p.suffix.lower() in (".jpg", ".jpeg"))
    except OSError:
        return None
    frames = []
    for file in files[:MAX_FRAMES]:
        try:
            data = file.read_bytes()
        except OSError:
            continue
        if data.startswith(b"\xff\xd8") and len(data) <= MAX_JPEG_BYTES:
            frames.append(data)
    if not frames:
        return None
    try:
        frame_ms = int((folder / "frame_ms").read_text().strip())
    except (OSError, ValueError):
        frame_ms = DEFAULT_FRAME_MS
    return Animation(tuple(frames), frame_ms)


def lcd_still(theme: Path) -> Path | None:
    """The still LCD picture a theme ships, or None."""
    for suffix in STILL_TYPES:
        if (theme / f"{LCD_NAME}{suffix}").is_file():
            return theme / f"{LCD_NAME}{suffix}"
    return None


def active_theme(es_settings: Path = ES_SETTINGS, theme_folders=THEME_FOLDERS) -> Path | None:
    """The folder of the theme chosen in Batocera's menu, or None."""
    try:
        root = ElementTree.parse(es_settings).getroot()
    except (OSError, ElementTree.ParseError):
        return None
    name = next((e.get("value") for e in root.iter("string") if e.get("name") == "ThemeSet"), None)
    if not name:
        return None
    for folder in theme_folders:
        if (Path(folder) / name / "theme.xml").is_file():
            return Path(folder) / name
    return None


def theme_background(es_settings: Path = ES_SETTINGS, theme_folders=THEME_FOLDERS) -> Path | None:
    """The active theme's menu background picture, or None.

    The background of the "system" view (the list of consoles) is preferred;
    failing that, any view's background.
    """
    theme = active_theme(es_settings, theme_folders)
    if theme is None:
        return None
    found = _backgrounds(theme / "theme.xml", theme, MAX_INCLUDE_DEPTH)
    for wanted_system_view in (True, False):
        for is_system_view, picture in found:
            if is_system_view == wanted_system_view:
                return picture
    return None


def _backgrounds(xml_file: Path, theme: Path, depth: int) -> list[tuple[bool, Path]]:
    """(is it the system view?, picture) for every usable background image."""
    try:
        root = ElementTree.parse(xml_file).getroot()
    except (OSError, ElementTree.ParseError) as error:
        log.debug("Cannot read theme file %s: %s", xml_file, error)
        return []
    found = []
    for view in root.iter("view"):
        views = [name.strip() for name in view.get("name", "").split(",")]
        for image in view.iter("image"):
            if image.get("name") != "background":
                continue
            picture = _resolve(image.findtext("path", ""), xml_file.parent, theme)
            if picture:
                found.append(("system" in views, picture))
    if depth > 0:
        for include in root.iter("include"):
            included = _resolve(include.text or "", xml_file.parent, theme)
            if included:
                found.extend(_backgrounds(included, theme, depth - 1))
    return found


def _resolve(text: str, folder: Path, theme: Path) -> Path | None:
    """A path written in a theme file as a real, existing file, or None."""
    text = text.strip().replace("${themePath}", str(theme))
    if not text or "${" in text:  # depends on something only the menu knows
        return None
    path = Path(text)
    if not path.is_absolute():
        path = folder / path
    return path if path.is_file() else None
