# SPDX-License-Identifier: GPL-3.0-or-later
"""Reading the look of a GNOME desktop: its wallpaper and accent colour.

Used as the idle look, so that with nothing in focus the PC matches the
desktop's theme.
"""

from collections.abc import Callable
from urllib.parse import unquote, urlparse

from gi.repository import Gio

# GNOME's named accent colours (as drawn by libadwaita).
ACCENT_COLOURS = {
    "blue": "#3584E4",
    "teal": "#2190A4",
    "green": "#3A944A",
    "yellow": "#C88800",
    "orange": "#ED5B00",
    "red": "#E62D42",
    "pink": "#D56199",
    "purple": "#9141AC",
    "slate": "#6F8396",
}

INTERFACE = "org.gnome.desktop.interface"
BACKGROUND = "org.gnome.desktop.background"


def accent_to_hex(name: str) -> str | None:
    return ACCENT_COLOURS.get(name)


def pick_wallpaper(colour_scheme: str, uri: str, uri_dark: str) -> str | None:
    """The wallpaper file in use, given GNOME's settings; None if it is not
    a local file."""
    chosen = uri_dark if colour_scheme == "prefer-dark" and uri_dark else uri
    parsed = urlparse(chosen)
    if parsed.scheme != "file" or not parsed.path:
        return None
    return unquote(parsed.path)


class DesktopTheme:
    """The current wallpaper and accent colour, and a way to hear changes."""

    def __init__(self):
        self._interface = Gio.Settings.new(INTERFACE)
        self._background = Gio.Settings.new(BACKGROUND)

    def accent_colour(self) -> str | None:
        if "accent-color" not in self._interface.props.settings_schema.list_keys():
            return None  # older GNOME has no accent colour
        return accent_to_hex(self._interface.get_string("accent-color"))

    def wallpaper(self) -> str | None:
        return pick_wallpaper(
            self._interface.get_string("color-scheme"),
            self._background.get_string("picture-uri"),
            self._background.get_string("picture-uri-dark"),
        )

    def on_change(self, callback: Callable[[], None]) -> None:
        """Call `callback` when the wallpaper, accent or dark/light choice changes."""
        for key in ("accent-color", "color-scheme"):
            self._interface.connect(f"changed::{key}", lambda *_: callback())
        for key in ("picture-uri", "picture-uri-dark"):
            self._background.connect(f"changed::{key}", lambda *_: callback())
