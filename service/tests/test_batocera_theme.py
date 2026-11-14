# SPDX-License-Identifier: GPL-3.0-or-later
from omenrgb.platforms.batocera_theme import active_theme, theme_background

ES_SETTINGS = """<?xml version="1.0"?>
<config>
\t<bool name="ShowHelp" value="true" />
\t<string name="ThemeSet" value="classic-dash" />
</config>
"""

THEME = """<theme>
  <view name="system, basic, detailed">
    <image name="shell"><path>./art/shell.png</path></image>
  </view>
  <view name="basic">
    <image name="background"><path>${themePath}/art/bg_list.jpg</path></image>
  </view>
  <view name="system">
    <image name="background" extra="static">
      <path>${themePath}/art/bg_system.jpg</path>
    </image>
  </view>
</theme>
"""


def make(tmp_path, theme_xml=THEME, files=("art/bg_system.jpg", "art/bg_list.jpg")):
    tmp_path.mkdir(parents=True, exist_ok=True)
    settings = tmp_path / "es_settings.cfg"
    settings.write_text(ES_SETTINGS)
    theme = tmp_path / "themes" / "classic-dash"
    (theme / "art").mkdir(parents=True)
    (theme / "theme.xml").write_text(theme_xml)
    for name in files:
        (theme / name).write_bytes(b"x")
    return settings, [tmp_path / "themes"]


def test_active_theme_folder(tmp_path):
    settings, themes = make(tmp_path)
    assert active_theme(settings, themes) == tmp_path / "themes" / "classic-dash"


def test_uses_the_system_views_background(tmp_path):
    settings, themes = make(tmp_path)
    expected = tmp_path / "themes" / "classic-dash" / "art" / "bg_system.jpg"
    assert theme_background(settings, themes) == expected


def test_relative_paths_and_any_background_as_second_choice(tmp_path):
    xml = (
        '<theme><view name="detailed"><image name="background">'
        "<path>./art/bg_list.jpg</path></image></view></theme>"
    )
    settings, themes = make(tmp_path, xml)
    assert theme_background(settings, themes).name == "bg_list.jpg"


def test_follows_included_files(tmp_path):
    xml = "<theme><include>./inc/views.xml</include></theme>"
    settings, themes = make(tmp_path, xml)
    inc = tmp_path / "themes" / "classic-dash" / "inc"
    inc.mkdir()
    (inc / "views.xml").write_text(
        '<theme><view name="system"><image name="background">'
        "<path>./../art/bg_system.jpg</path></image></view></theme>"
    )
    assert theme_background(settings, themes).name == "bg_system.jpg"


def test_no_usable_background_means_none(tmp_path):
    settings, themes = make(tmp_path, files=())  # picture files missing
    assert theme_background(settings, themes) is None
    settings, themes2 = make(tmp_path / "b", "<theme><view name='system'/></theme>")
    assert theme_background(settings, themes2) is None
    settings, themes3 = make(tmp_path / "c", "<theme><view")  # broken XML
    assert theme_background(settings, themes3) is None
    assert theme_background(tmp_path / "missing.cfg", themes) is None
    settings, _ = make(tmp_path / "d")
    assert theme_background(settings, [tmp_path / "nowhere"]) is None


def test_backgrounds_that_depend_on_theme_variables_are_skipped(tmp_path):
    xml = (
        '<theme><view name="system"><image name="background">'
        "<path>./art/${system.theme}.jpg</path></image></view>"
        '<view name="basic"><image name="background">'
        "<path>./art/bg_list.jpg</path></image></view></theme>"
    )
    settings, themes = make(tmp_path, xml)
    assert theme_background(settings, themes).name == "bg_list.jpg"


# --- a theme can ship its own picture or animation for the LCD ----------------------

JPEG = b"\xff\xd8" + b"x" * 100


def test_lcd_animation_from_a_folder_of_frames(tmp_path):
    from omenrgb.platforms.batocera_theme import lcd_animation

    frames = tmp_path / "omen-lights"
    frames.mkdir()
    for name in ("002.jpg", "000.jpg", "001.jpg"):
        (frames / name).write_bytes(JPEG + name.encode())
    (frames / "frame_ms").write_text("80\n")
    animation = lcd_animation(tmp_path)
    assert [f[-7:] for f in animation.frames] == [b"000.jpg", b"001.jpg", b"002.jpg"]  # in order
    assert animation.frame_ms == 80


def test_lcd_animation_defaults_and_skips_unusable_frames(tmp_path):
    from omenrgb.platforms.batocera_theme import lcd_animation

    frames = tmp_path / "omen-lights"
    frames.mkdir()
    (frames / "000.jpg").write_bytes(JPEG)
    (frames / "001.jpg").write_bytes(b"not a jpeg")
    (frames / "002.jpg").write_bytes(b"\xff\xd8" + bytes(70000))  # too big for the LCD
    (frames / "notes.txt").write_text("hello")
    (frames / "frame_ms").write_text("fast")
    animation = lcd_animation(tmp_path)
    assert animation.frames == (JPEG,) and animation.frame_ms == 100


def test_no_lcd_animation_without_frames(tmp_path):
    from omenrgb.platforms.batocera_theme import lcd_animation

    assert lcd_animation(tmp_path) is None
    (tmp_path / "omen-lights").mkdir()
    assert lcd_animation(tmp_path) is None


def test_lcd_still_picture_shipped_by_a_theme(tmp_path):
    from omenrgb.platforms.batocera_theme import lcd_still

    assert lcd_still(tmp_path) is None
    (tmp_path / "omen-lights.png").write_bytes(b"png")
    assert lcd_still(tmp_path) == tmp_path / "omen-lights.png"
