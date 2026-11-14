# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto mode: what the lights and LCD should look like for a given app.

For the app that currently has focus, this finds its icon, picks the icon's
most striking colour for the lights, and builds an LCD picture of the icon.
"""

import colorsys
import logging
import math
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from PIL import Image

from .constants import LCD_SIZE
from .image_prep import encode_for_lcd

log = logging.getLogger(__name__)

ICON_SIZE_ON_LCD = 300  # the screen is round, so the icon stays well inside it
ICON_RENDER_SIZE = 512  # how large icons are drawn before being scaled down

# --- choosing a colour (change these to taste) ---------------------------------
SAMPLE_SIZE = 48  # about this many pixels per side are looked at
HUE_BINS = 24  # colours are grouped into this many slices of the colour wheel
MIN_SATURATION = 0.25  # paler than this counts as grey/white
MIN_VALUE = 0.20  # darker than this counts as black
MIN_COLOURFUL_SHARE = 0.01  # ignore icons with only a speck of colour
LED_SATURATION = 0.75  # the chosen colour is made at least this vivid


@dataclass(frozen=True)
class Look:
    """How one app should be shown."""

    colour: str | None  # '#RRGGBB' for the lights; None if the icon has no real colour
    jpeg: bytes  # the app's icon, ready for the LCD


def dominant_colour(icon: Image.Image) -> str | None:
    """The most striking colour of an icon as '#RRGGBB', vivid enough for
    LEDs, or None if the icon is essentially grey, black, white or empty."""
    # Look at a grid of about SAMPLE_SIZE x SAMPLE_SIZE real pixels. (Shrinking
    # the icon instead would invent in-between colours along every edge.)
    rgba = icon.convert("RGBA")
    raw, width, height = rgba.tobytes(), rgba.width, rgba.height
    step_x, step_y = max(1, width // SAMPLE_SIZE), max(1, height // SAMPLE_SIZE)
    pixels = (
        raw[offset : offset + 4]
        for y in range(0, height, step_y)
        for offset in range(y * width * 4, (y + 1) * width * 4, step_x * 4)
    )
    weight = [0.0] * HUE_BINS  # how much of each slice of the colour wheel
    hue_x, hue_y, saturation = [0.0] * HUE_BINS, [0.0] * HUE_BINS, [0.0] * HUE_BINS
    opaque = colourful = 0
    for red, green, blue, alpha in pixels:
        if alpha < 128:
            continue
        opaque += 1
        h, s, v = colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)
        if s < MIN_SATURATION or v < MIN_VALUE:
            continue
        colourful += 1
        slot = int(h * HUE_BINS) % HUE_BINS
        strength = s * v  # vivid, bright pixels count for more
        weight[slot] += strength
        hue_x[slot] += math.cos(2 * math.pi * h) * strength
        hue_y[slot] += math.sin(2 * math.pi * h) * strength
        saturation[slot] += s * strength
    if not opaque or colourful < max(1, opaque * MIN_COLOURFUL_SHARE):
        return None
    best = max(range(HUE_BINS), key=weight.__getitem__)
    hue = (math.atan2(hue_y[best], hue_x[best]) / (2 * math.pi)) % 1.0
    vivid = max(saturation[best] / weight[best], LED_SATURATION)
    red, green, blue = (round(255 * part) for part in colorsys.hsv_to_rgb(hue, vivid, 1.0))
    return f"#{red:02X}{green:02X}{blue:02X}"


def icon_on_black(icon: Image.Image) -> bytes:
    """An LCD-ready JPEG of the icon, centred on black."""
    icon = icon.convert("RGBA")
    scale = ICON_SIZE_ON_LCD / max(icon.size)
    size = (max(1, round(icon.width * scale)), max(1, round(icon.height * scale)))
    icon = icon.resize(size, Image.LANCZOS)
    screen = Image.new("RGB", (LCD_SIZE, LCD_SIZE), (0, 0, 0))
    screen.paste(icon, ((LCD_SIZE - size[0]) // 2, (LCD_SIZE - size[1]) // 2), icon)
    return encode_for_lcd(screen)


class AppLooks:
    """Works out, and remembers, the Look of each app."""

    def __init__(
        self,
        load_icon: Callable[[str], Image.Image | None],
        remember: int = 64,
        ignore: Iterable[str] = (),
        load_idle: Callable[[], Image.Image | None] | None = None,
    ):
        """`load_icon(app_id)` returns the app's icon, or None if it has none.
        Apps listed in `ignore` never get a look. `load_idle()` returns the
        picture for when nothing is in focus (normally the OS logo)."""
        self._load_icon = load_icon
        self._ignore = frozenset(ignore)
        self._load_idle = load_idle
        self._idle: dict[str, Look | None] = {}  # idle looks by picture path ('' = OS logo)
        self._remember = remember
        self._known: OrderedDict[str, Look | None] = OrderedDict()

    def is_ignored(self, app_id: str) -> bool:
        """True for apps during which the owner's normal settings stay on."""
        return app_id in self._ignore

    def idle_look(self, image_path: str = "") -> Look | None:
        """The Look for when nothing is in focus: `image_path` if given,
        otherwise the OS logo. None if there is no usable picture."""
        if image_path not in self._idle:
            try:
                if image_path:
                    with Image.open(image_path) as opened:
                        picture = opened.convert("RGBA")
                else:
                    picture = self._load_idle() if self._load_idle else None
                look = Look(dominant_colour(picture), icon_on_black(picture)) if picture else None
            except Exception as error:
                log.warning("No idle look (%s): %s", image_path or "OS logo", error)
                look = None
            self._idle[image_path] = look
        return self._idle[image_path]

    def look_for(self, app_id: str) -> Look | None:
        """The Look for an app id such as 'firefox.desktop'; None if unknown."""
        if not app_id or app_id in self._ignore:
            return None
        if app_id in self._known:
            self._known.move_to_end(app_id)
            return self._known[app_id]
        try:
            icon = self._load_icon(app_id)
            look = Look(dominant_colour(icon), icon_on_black(icon)) if icon else None
        except Exception as error:  # icon themes and image files can fail in many ways
            log.warning("No look for %s: %s", app_id, error)
            look = None
        self._known[app_id] = look
        while len(self._known) > self._remember:
            self._known.popitem(last=False)
        return look


def load_os_logo() -> Image.Image | None:
    """The operating system's logo, as named in /etc/os-release."""
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    # Exactly the icon the OS names; "fallback" lookups pick unrelated icons.
    name = GLib.get_os_info("LOGO") or "distributor-logo"
    return _draw_themed_icon(Gio.ThemedIcon.new(name))


def load_app_icon(app_id: str) -> Image.Image | None:
    """Find an installed app's icon in the current icon theme and draw it."""
    import gi  # imported here: GTK is slow to load and only auto mode needs it

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio

    try:
        gi.require_version("GioUnix", "2.0")
        from gi.repository import GioUnix as DesktopFiles  # newer GLib
    except (ValueError, ImportError):
        DesktopFiles = Gio  # older GLib keeps DesktopAppInfo here
    try:
        app = DesktopFiles.DesktopAppInfo.new(app_id)
    except TypeError:  # PyGObject's way of saying "no such desktop file"
        return None
    if app is None or app.get_icon() is None:
        return None

    return _draw_themed_icon(app.get_icon())


def _draw_themed_icon(icon) -> Image.Image | None:
    """Look a Gio icon up in the current icon theme and draw it."""
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import Gdk, Gtk

    Gtk.init_check()
    display = Gdk.Display.get_default()
    theme = Gtk.IconTheme.get_for_display(display) if display else Gtk.IconTheme()
    found = theme.lookup_by_gicon(
        icon, ICON_RENDER_SIZE, 1, Gtk.TextDirection.NONE, Gtk.IconLookupFlags(0)
    )
    file = found.get_file() if found else None
    if file is None or file.get_path() is None:
        return None
    return draw_image_file(file.get_path())


def draw_image_file(path: str) -> Image.Image:
    """Any picture file GdkPixbuf can read (SVG included) as a PIL image."""
    import gi

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(path, ICON_RENDER_SIZE, ICON_RENDER_SIZE)
    mode = "RGBA" if pixbuf.get_has_alpha() else "RGB"
    return Image.frombuffer(
        mode,
        (pixbuf.get_width(), pixbuf.get_height()),
        pixbuf.get_pixels(),
        "raw",
        mode,
        pixbuf.get_rowstride(),
        1,  # rows run top to bottom
    ).copy()
