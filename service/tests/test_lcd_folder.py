# SPDX-License-Identifier: GPL-3.0-or-later
import io

from PIL import Image

from rgbhub.core.lcd_folder import load_lcd_folder
from rgbhub.core.look import Animation, cover_lcd

JPEG = b"\xff\xd8" + b"x" * 100


def test_missing_or_empty_folder_means_no_override(tmp_path):
    assert load_lcd_folder(tmp_path / "missing", cover_lcd) is None
    assert load_lcd_folder(tmp_path, cover_lcd) is None
    (tmp_path / "notes.txt").write_text("hello")
    assert load_lcd_folder(tmp_path, cover_lcd) is None


def test_several_jpeg_frames_are_an_animation_played_in_name_order(tmp_path):
    for name in ("002.jpg", "000.jpg", "001.jpg"):
        (tmp_path / name).write_bytes(JPEG + name.encode())
    (tmp_path / "frame_ms").write_text("80\n")
    animation = load_lcd_folder(tmp_path, cover_lcd)
    assert isinstance(animation, Animation) and animation.frame_ms == 80
    assert [f[-7:] for f in animation.frames] == [b"000.jpg", b"001.jpg", b"002.jpg"]


def test_frame_time_defaults_and_unusable_frames_are_skipped(tmp_path):
    (tmp_path / "000.jpg").write_bytes(JPEG)
    (tmp_path / "001.jpg").write_bytes(JPEG + b"b")
    (tmp_path / "002.jpg").write_bytes(b"not a jpeg")
    (tmp_path / "003.jpg").write_bytes(b"\xff\xd8" + bytes(70000))  # too big for the LCD
    (tmp_path / "frame_ms").write_text("fast")
    animation = load_lcd_folder(tmp_path, cover_lcd)
    assert animation.frames == (JPEG, JPEG + b"b") and animation.frame_ms == 100


def test_one_picture_is_a_still(tmp_path):
    Image.new("RGB", (800, 600), (0, 0, 255)).save(tmp_path / "anything.png")
    still = load_lcd_folder(tmp_path, cover_lcd)
    assert isinstance(still, Image.Image) and still.size == (800, 600)


def test_one_animated_file_is_played_too(tmp_path):
    frames = [Image.new("RGB", (120, 120), c) for c in ((255, 0, 0), (0, 255, 0), (0, 0, 255))]
    frames[0].save(tmp_path / "spin.gif", save_all=True, append_images=frames[1:], duration=70)
    animation = load_lcd_folder(tmp_path, cover_lcd)
    assert isinstance(animation, Animation) and len(animation.frames) == 3
    assert animation.frame_ms == 70
    first = Image.open(io.BytesIO(animation.frames[0]))
    assert first.size == (480, 480) and first.getpixel((240, 240))[0] > 200  # made LCD-ready


def test_a_broken_picture_means_no_override(tmp_path):
    (tmp_path / "broken.png").write_text("not a picture")
    assert load_lcd_folder(tmp_path, cover_lcd) is None
