# SPDX-License-Identifier: GPL-3.0-or-later
import io

from PIL import Image

from rgbhub.core.look import Look, Looks, dominant_colour, icon_on_black


def icon(background, blob=None, size=64):
    image = Image.new("RGBA", (size, size), background)
    if blob:
        colour, box = blob
        image.paste(Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), colour), box[:2])
    return image


def test_solid_colour_icon():
    assert dominant_colour(icon((255, 0, 0, 255))) == "#FF0000"


def test_white_icon_with_a_blue_logo_is_blue():
    image = icon((255, 255, 255, 255), ((0, 0, 255, 255), (20, 20, 44, 44)))
    assert dominant_colour(image) == "#0000FF"


def test_the_biggest_colourful_area_wins():
    image = icon((0, 160, 0, 255), ((255, 0, 0, 255), (0, 0, 16, 16)))
    red, green, blue = (int(dominant_colour(image)[i : i + 2], 16) for i in (1, 3, 5))
    assert green > red and green > blue


def test_dull_colours_are_made_vivid_for_leds():
    # A muted teal should come out as a clear, bright teal.
    red, green, blue = (
        int(dominant_colour(icon((60, 110, 110, 255)))[i : i + 2], 16) for i in (1, 3, 5)
    )
    assert max(green, blue) == 255 and red < 100


def test_grey_black_white_and_transparent_icons_have_no_colour():
    assert dominant_colour(icon((128, 128, 128, 255))) is None
    assert dominant_colour(icon((0, 0, 0, 255))) is None
    assert dominant_colour(icon((255, 255, 255, 255))) is None
    assert dominant_colour(icon((255, 0, 0, 0))) is None  # fully transparent red


def test_icon_on_black_is_a_centred_lcd_jpeg():
    data = icon_on_black(icon((255, 0, 0, 255), size=256))
    image = Image.open(io.BytesIO(data))
    assert image.format == "JPEG" and image.size == (480, 480)
    centre, corner = image.getpixel((240, 240)), image.getpixel((10, 10))
    assert centre[0] > 200 and centre[1] < 60 and max(corner) < 30


def test_transparent_parts_of_an_icon_show_as_black():
    data = icon_on_black(icon((0, 0, 0, 0), ((0, 255, 0, 255), (96, 96, 160, 160)), size=256))
    image = Image.open(io.BytesIO(data))
    assert image.getpixel((240, 240))[1] > 200 and max(image.getpixel((150, 150))) < 30


def test_looks_are_built_once_per_app_and_remembered():
    loads = []

    def load(app_id):
        loads.append(app_id)
        return icon((255, 0, 0, 255), size=256)

    looks = Looks(load)
    first = looks.look_for("firefox.desktop")
    assert isinstance(first, Look) and first.colour == "#FF0000" and first.jpeg[:2] == b"\xff\xd8"
    assert looks.look_for("firefox.desktop") is first and loads == ["firefox.desktop"]


def test_unknown_app_or_no_app_has_no_look():
    looks = Looks(lambda app_id: None)
    assert looks.look_for("mystery.desktop") is None
    assert looks.look_for("") is None


def test_a_broken_icon_does_not_raise():
    def load(app_id):
        raise RuntimeError("icon theme exploded")

    assert Looks(load).look_for("x.desktop") is None


def test_only_so_many_looks_are_remembered():
    looks = Looks(lambda app_id: icon((255, 0, 0, 255), size=64), remember=2)
    a = looks.look_for("a")
    looks.look_for("b")
    looks.look_for("c")  # pushes "a" out
    assert looks.look_for("a") is not a


def test_ignored_apps_have_no_look():
    # While our own settings window has focus, the owner should see their
    # normal colours, not the colours of the settings app's icon.
    looks = Looks(lambda app_id: icon((255, 0, 0, 255), size=64), ignore=["settings.desktop"])
    assert looks.look_for("settings.desktop") is None
    assert looks.look_for("other.desktop") is not None


# --- the idle look: what to show when no app (or game) is in focus ---------------


def test_idle_look_comes_from_the_os_logo_and_is_remembered():
    loads = []

    def load_idle():
        loads.append(1)
        return icon((255, 80, 0, 255), size=128)

    looks = Looks(lambda app_id: None, load_idle=load_idle)
    idle = looks.idle_look()
    assert idle.colour is not None and idle.jpeg[:2] == b"\xff\xd8"
    assert looks.idle_look() is idle and loads == [1]


def test_idle_look_can_be_a_picture_the_owner_chose(tmp_path):
    path = tmp_path / "mine.png"
    icon((0, 0, 255, 255), size=128).save(path)
    looks = Looks(lambda app_id: None, load_idle=lambda: icon((255, 0, 0, 255)))
    assert looks.idle_look(str(path)).colour == "#0000FF"
    assert looks.idle_look().colour == "#FF0000"  # the default is still available


def test_no_idle_look_without_a_logo_or_when_it_fails(tmp_path):
    assert Looks(lambda app_id: None).idle_look() is None

    def broken():
        raise RuntimeError("no logo")

    assert Looks(lambda app_id: None, load_idle=broken).idle_look() is None
    assert Looks(lambda app_id: None).idle_look(str(tmp_path / "missing.png")) is None


def test_is_ignored():
    looks = Looks(lambda app_id: None, ignore=["settings.desktop"])
    assert looks.is_ignored("settings.desktop") and not looks.is_ignored("other.desktop")


def test_cover_lcd_fills_the_whole_screen():
    from rgbhub.core.look import cover_lcd

    wide = Image.new("RGB", (1920, 1080), (0, 0, 200))
    image = Image.open(io.BytesIO(cover_lcd(wide)))
    assert image.size == (480, 480) and image.getpixel((5, 5))[2] > 150  # no black border


def test_looks_can_be_told_how_to_lay_pictures_out():
    looks = Looks(
        lambda subject: icon((255, 0, 0, 255), size=64),
        load_idle=lambda: icon((0, 255, 0, 255), size=64),
        compose=lambda image: b"art",
        compose_idle=lambda image: b"idle",
    )
    assert looks.look_for("game").jpeg == b"art" and looks.idle_look().jpeg == b"idle"


def test_forget_idle_rebuilds_the_idle_look():
    pictures = [icon((255, 0, 0, 255), size=64), icon((0, 0, 255, 255), size=64)]
    looks = Looks(lambda subject: None, load_idle=lambda: pictures.pop(0))
    assert looks.idle_look().colour == "#FF0000"
    looks.forget_idle()
    assert looks.idle_look().colour == "#0000FF"


# --- animated idle pictures ---------------------------------------------------------


def lcd_jpeg(colour):
    buffer = io.BytesIO()
    Image.new("RGB", (480, 480), colour).save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


def test_an_animation_becomes_an_animated_look():
    from rgbhub.core.look import Animation

    frames = (lcd_jpeg((0, 200, 0)), lcd_jpeg((0, 255, 0)))
    looks = Looks(lambda subject: None, load_idle=lambda: Animation(frames, 100))
    idle = looks.idle_look()
    assert idle.frames == frames and idle.frame_ms == 100
    assert idle.jpeg == frames[0]  # what is shown first, and faded to
    red, green, blue = (int(idle.colour[i : i + 2], 16) for i in (1, 3, 5))
    assert green == 255 and red < 80 and blue < 80


def test_a_still_look_has_no_frames():
    looks = Looks(lambda subject: None, load_idle=lambda: icon((255, 0, 0, 255), size=64))
    assert looks.idle_look().frames == ()


def test_an_empty_animation_gives_no_look():
    from rgbhub.core.look import Animation

    assert Looks(lambda s: None, load_idle=lambda: Animation((), 100)).idle_look() is None


def test_a_remembered_look_is_made_again_when_its_source_changes():
    """Another icon theme (or new artwork for a game) means other icons and colours: a
    look made before the change must not be shown for as long as the service runs."""
    theme = ["purple"]
    colours = {"purple": (120, 60, 255, 255), "green": (60, 255, 150, 255)}
    calls = []

    def load(app_id):
        calls.append(app_id)
        return icon(colours[theme[0]], size=64)

    looks = Looks(load, version=lambda app_id: theme[0])
    first = looks.look_for("terminal.desktop")
    assert looks.look_for("terminal.desktop") is first and len(calls) == 1  # remembered
    theme[0] = "green"
    second = looks.look_for("terminal.desktop")
    assert len(calls) == 2 and second.colour != first.colour
    assert looks.look_for("terminal.desktop") is second and len(calls) == 2


def test_a_version_that_cannot_be_asked_does_not_stop_the_look():
    def version(app_id):
        raise OSError("settings not there")

    looks = Looks(lambda app_id: icon((255, 0, 0, 255), size=64), version=version)
    assert looks.look_for("x.desktop")
