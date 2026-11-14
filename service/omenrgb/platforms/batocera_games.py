# SPDX-License-Identifier: GPL-3.0-or-later
"""Finding a game's artwork on Batocera.

Batocera keeps each system's games in /userdata/roms/<system>/ with a
gamelist.xml that names the artwork scraped for every game.
"""

import logging
import xml.etree.ElementTree as ElementTree
from pathlib import Path

log = logging.getLogger(__name__)

ROMS = Path("/userdata/roms")

# Which artwork to use, best first: the game's logo reads well on a small
# round screen, then the box, then a screenshot.
ARTWORK_TAGS = ("marquee", "thumbnail", "image")


def find_artwork(rom: str, roms: Path = ROMS) -> Path | None:
    """The artwork file for the game at `rom`, or None if there is none."""
    try:
        relative = Path(rom).relative_to(roms)
    except ValueError:
        return None
    if len(relative.parts) < 2:
        return None
    system = roms / relative.parts[0]
    try:
        games = ElementTree.parse(system / "gamelist.xml").getroot().iter("game")
        for game in games:
            listed = game.findtext("path", "")
            if listed and (system / listed).resolve() == Path(rom).resolve():
                for tag in ARTWORK_TAGS:
                    picture = game.findtext(tag, "")
                    if picture and (system / picture).is_file():
                        return system / picture
                return None
    except (OSError, ElementTree.ParseError) as error:
        log.debug("No artwork for %s: %s", rom, error)
    return None
