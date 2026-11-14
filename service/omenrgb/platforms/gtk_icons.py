# SPDX-License-Identifier: GPL-3.0-or-later
"""Finding and drawing icons on a GTK desktop: installed apps and the OS logo."""

from PIL import Image

ICON_RENDER_SIZE = 512  # how large icons are drawn before being scaled down


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
