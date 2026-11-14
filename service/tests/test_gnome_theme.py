# SPDX-License-Identifier: GPL-3.0-or-later
from omenrgb.platforms.gnome_theme import accent_to_hex, pick_wallpaper


def test_known_accent_names_become_colours():
    assert accent_to_hex("purple") == "#9141AC"
    assert accent_to_hex("orange") == "#ED5B00"


def test_unknown_or_missing_accent_gives_none():
    assert accent_to_hex("chartreuse") is None and accent_to_hex("") is None


def test_wallpaper_follows_the_dark_or_light_setting():
    light, dark = "file:///pics/day.jpg", "file:///pics/night.jpg"
    assert pick_wallpaper("prefer-dark", light, dark) == "/pics/night.jpg"
    assert pick_wallpaper("default", light, dark) == "/pics/day.jpg"
    assert pick_wallpaper("prefer-dark", light, "") == "/pics/day.jpg"  # no dark one set


def test_wallpaper_paths_with_spaces_and_non_files():
    assert pick_wallpaper("default", "file:///my%20pics/a%20b.png", "") == "/my pics/a b.png"
    assert pick_wallpaper("default", "", "") is None
    assert pick_wallpaper("default", "https://example.com/x.jpg", "") is None
