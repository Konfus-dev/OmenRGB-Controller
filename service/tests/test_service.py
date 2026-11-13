# SPDX-License-Identifier: GPL-3.0-or-later
import json
from pathlib import Path

import pytest
from gi.repository import GLib

from omenrgb.image_prep import ImageError
from omenrgb.lights import DeviceInfo, EffectOptions
from omenrgb.service import Service


class FakeStreamer:
    def __init__(self):
        self.connected = True
        self.images = []
        self.brightness = []
        self.reconnect_ticks = 0

    def set_image(self, jpeg):
        self.images.append(jpeg)

    def set_brightness(self, percent):
        self.brightness.append(percent)

    def tick_reconnect(self):
        self.reconnect_ticks += 1

    def tick_claim(self):
        pass

    def tick_frame(self):
        pass


class FakeLights:
    def __init__(self, available=True, name="Case"):
        self.name = name
        self.available = available  # what try_connect will answer
        self.connected = False
        self.applied = []

    def try_connect(self):
        self.connected = self.available
        return self.connected

    def check(self):
        self.connected = self.connected and self.available
        return self.connected

    animating = False  # tests set this to pretend a breath is running

    def forget(self):
        pass

    def tick_animation(self, now):
        self.animation_ticks = getattr(self, "animation_ticks", 0) + 1

    def devices(self):
        if not self.connected:
            return []
        return [
            DeviceInfo(
                self.name,
                ("Static", "Off"),
                ("Logo", "Top Fan"),
                default_mode="Static",
                colourless_modes=("Off",),
            )
        ]

    def apply(self, enabled, brightness, modes, colours, options=None):
        self.applied.append((enabled, brightness, modes, colours))
        self.options = options


class FakePrepare:
    def __init__(self):
        self.calls = []
        self.error = None

    def __call__(self, path, fit):
        self.calls.append((path, fit))
        if self.error:
            raise ImageError(self.error)
        return f"jpeg of {path.name} ({fit}){getattr(self, 'version', '')}".encode()


@pytest.fixture
def parts(settings):
    settings.set_string("lcd-image", "/pics/cat.png")
    streamer, lights, prepare = FakeStreamer(), FakeLights(), FakePrepare()
    service = Service(settings, streamer, lights, prepare)
    yield service, settings, streamer, lights, prepare
    service.stop()


def status(settings):
    return json.loads(settings.get_string("status"))


def test_start_applies_everything_once(parts):
    service, settings, streamer, lights, prepare = parts
    service.start()
    assert prepare.calls == [(Path("/pics/cat.png"), "fill")]
    assert streamer.images == [b"jpeg of cat.png (fill)"]
    assert streamer.brightness == [80]
    assert lights.applied == [(True, 100, {}, {})]
    assert status(settings) == {
        "lcd": True,
        "openrgb": True,
        "ram": False,
        "error": "",
        "auto": {"app": "", "colour": ""},
        "devices": [
            {
                "name": "Case",
                "label": "Case",
                "modes": ["Static", "Off"],
                "default_mode": "Static",
                "colourless_modes": ["Off"],
                "speed_modes": [],
                "range_modes": [],
                "zones": ["Logo", "Top Fan"],
                "zone_labels": ["Logo", "Top Fan"],
            }
        ],
    }


def test_image_change_calls_prepare_and_set_image(parts):
    service, settings, streamer, _lights, prepare = parts
    service.start()
    settings.set_string("lcd-image", "/pics/dog.png")
    service.on_changed("lcd-image")
    assert prepare.calls[-1] == (Path("/pics/dog.png"), "fill")
    assert streamer.images[-1] == b"jpeg of dog.png (fill)"


def test_fit_change_reprepares_image(parts):
    service, settings, streamer, _lights, _prepare = parts
    service.start()
    settings.set_string("lcd-fit", "fit")
    service.on_changed("lcd-fit")
    assert streamer.images[-1] == b"jpeg of cat.png (fit)"


def test_bad_image_keeps_previous_and_reports(parts):
    service, settings, streamer, _lights, prepare = parts
    service.start()
    prepare.error = "boom"
    settings.set_string("lcd-image", "/pics/broken.png")
    service.on_changed("lcd-image")
    assert streamer.images == [b"jpeg of cat.png (fill)"]  # nothing new was sent
    assert status(settings)["error"] == "boom"


def test_good_image_clears_error(parts):
    service, settings, _streamer, _lights, prepare = parts
    service.start()
    prepare.error = "boom"
    service.on_changed("lcd-image")
    prepare.error = None
    service.on_changed("lcd-image")
    assert status(settings)["error"] == ""


def test_empty_image_path_clears_image(parts):
    service, settings, streamer, _lights, prepare = parts
    service.start()
    settings.set_string("lcd-image", "")
    service.on_changed("lcd-image")
    assert streamer.images[-1] is None
    assert len(prepare.calls) == 1  # prepare was not asked to open ""


def test_lcd_disabled_means_brightness_zero(parts):
    service, settings, streamer, _lights, _prepare = parts
    service.start()
    settings.set_boolean("lcd-enabled", False)
    service.on_changed("lcd-enabled")
    assert streamer.brightness[-1] == 0
    settings.set_boolean("lcd-enabled", True)
    service.on_changed("lcd-enabled")
    assert streamer.brightness[-1] == 80


def test_lcd_brightness_change(parts):
    service, settings, streamer, _lights, _prepare = parts
    service.start()
    settings.set_int("lcd-brightness", 35)
    service.on_changed("lcd-brightness")
    assert streamer.brightness[-1] == 35


def test_light_keys_trigger_apply(parts):
    service, settings, _streamer, lights, _prepare = parts
    service.start()
    settings.set_boolean("lights-enabled", False)
    settings.set_int("lights-brightness", 40)
    settings.set_value("device-modes", GLib.Variant("a{ss}", {"Case": "Off"}))
    settings.set_value("zone-colours", GLib.Variant("a{ss}", {"Case/Logo": "#FF0000"}))
    for key in ("lights-enabled", "lights-brightness", "device-modes", "zone-colours"):
        before = len(lights.applied)
        service.on_changed(key)
        service.flush_lights()
        assert len(lights.applied) == before + 1
    assert lights.applied[-1] == (False, 40, {"Case": "Off"}, {"Case/Logo": "#FF0000"})


def test_status_key_change_is_ignored(parts):
    service, _settings, streamer, lights, prepare = parts
    service.start()
    service.on_changed("status")
    assert len(lights.applied) == 1 and len(prepare.calls) == 1 and len(streamer.images) == 1


def test_lights_reconnect_reapplies(settings):
    lights = FakeLights(available=False)
    service = Service(settings, FakeStreamer(), lights, FakePrepare())
    service.start()
    assert lights.applied == [] and status(settings)["openrgb"] is False
    lights.available = True
    service.tick_slow()
    assert len(lights.applied) == 1 and status(settings)["openrgb"] is True
    service.tick_slow()
    assert len(lights.applied) == 1  # already connected: nothing to redo
    service.stop()


def test_openrgb_down_does_not_touch_lcd(settings):
    streamer = FakeStreamer()
    service = Service(settings, streamer, FakeLights(available=False), FakePrepare())
    service.start()
    service.tick_slow()
    assert streamer.reconnect_ticks >= 1
    assert status(settings)["lcd"] is True and status(settings)["openrgb"] is False
    service.stop()


def test_status_written_only_when_changed(parts):
    service, settings, _streamer, _lights, _prepare = parts
    service.start()
    writes = []
    settings.connect("changed::status", lambda *_: writes.append(1))
    service.publish_status()
    service.publish_status()
    context = GLib.MainContext.default()
    while context.iteration(False):
        pass
    assert writes == []  # start() already published this exact status


def test_openrgb_going_away_is_noticed_and_recovered(parts):
    service, settings, _streamer, lights, _prepare = parts
    service.start()
    lights.available = False  # OpenRGB stops
    service.tick_slow()
    assert status(settings)["openrgb"] is False and status(settings)["devices"] == []
    lights.available = True  # OpenRGB comes back
    service.tick_slow()
    assert status(settings)["openrgb"] is True
    assert len(lights.applied) == 2  # lights were set again after the restart


@pytest.fixture
def with_ram(settings):
    ram = FakeLights(name="RAM")
    service = Service(settings, FakeStreamer(), FakeLights(), FakePrepare(), ram=ram)
    yield service, settings, ram
    service.stop()


def test_ram_is_listed_after_the_case_lights(with_ram):
    service, settings, _ram = with_ram
    service.start()
    assert status(settings)["ram"] is True
    assert [d["name"] for d in status(settings)["devices"]] == ["Case", "RAM"]


def test_ram_gets_the_same_settings_as_the_case_lights(with_ram):
    service, settings, ram = with_ram
    service.start()
    assert ram.applied == [(True, 100, {}, {})]
    settings.set_int("lights-brightness", 30)
    service.on_changed("lights-brightness")
    service.flush_lights()
    assert ram.applied[-1] == (True, 30, {}, {})


def test_ram_changes_are_batched(with_ram):
    # Dragging a slider fires many changes; the RAM is slow to update, so
    # only the final value is sent.
    service, settings, ram = with_ram
    service.start()
    for value in (90, 80, 70):
        settings.set_int("lights-brightness", value)
        service.on_changed("lights-brightness")
    assert len(ram.applied) == 1  # nothing sent yet
    service.flush_lights()
    assert len(ram.applied) == 2 and ram.applied[-1][1] == 70


def test_case_light_changes_are_batched_too(parts):
    service, settings, _streamer, lights, _prepare = parts
    service.start()
    for value in (90, 80, 70):
        settings.set_int("lights-brightness", value)
        service.on_changed("lights-brightness")
    assert len(lights.applied) == 1
    service.flush_lights()
    assert len(lights.applied) == 2 and lights.applied[-1][1] == 70


def test_unexpected_image_failure_does_not_crash_the_service(parts):
    service, settings, streamer, _lights, prepare = parts

    def explode(path, fit):
        raise RuntimeError("something odd inside the image library")

    service._prepare = explode
    service.start()  # must not raise, or systemd would restart us forever
    assert streamer.images == [] and "something odd" in status(settings)["error"]


def test_a_failing_tick_does_not_stop_its_timer():
    def bad_tick():
        raise RuntimeError("boom")

    assert Service._repeat(bad_tick) is True


def test_ram_helper_appearing_later_is_picked_up(settings):
    ram = FakeLights(available=False, name="RAM")
    service = Service(settings, FakeStreamer(), FakeLights(), FakePrepare(), ram=ram)
    service.start()
    assert status(settings)["ram"] is False and ram.applied == []
    ram.available = True
    service.tick_slow()
    assert status(settings)["ram"] is True and len(ram.applied) == 1
    service.stop()


def test_status_uses_friendly_names_where_known(settings):
    service = Service(
        settings,
        FakeStreamer(),
        FakeLights(),
        FakePrepare(),
        display_names={"Case": "Case lights", "Case/Top Fan": "Top fan"},
    )
    service.start()
    device = status(settings)["devices"][0]
    assert device["name"] == "Case" and device["label"] == "Case lights"
    assert device["zones"] == ["Logo", "Top Fan"]  # real names stay for saving settings
    assert device["zone_labels"] == ["Logo", "Top fan"]
    service.stop()


def test_effect_options_reach_both_kinds_of_light(with_ram):
    service, settings, ram = with_ram
    service.start()
    settings.set_value("device-speeds", GLib.Variant("a{si}", {"RAM": 80}))
    settings.set_value("device-min-brightness", GLib.Variant("a{si}", {"RAM": 10}))
    settings.set_value("device-max-brightness", GLib.Variant("a{si}", {"RAM": 90}))
    for key in ("device-speeds", "device-min-brightness", "device-max-brightness"):
        service.on_changed(key)
    service.flush_lights()
    assert ram.options["RAM"] == EffectOptions(speed=80, min_brightness=10, max_brightness=90)
    assert ram.options.get("Case", EffectOptions()) == EffectOptions()  # defaults elsewhere


def test_new_setting_defaults(settings):
    for key in ("device-speeds", "device-min-brightness", "device-max-brightness"):
        assert settings.get_value(key).unpack() == {}


def test_animation_timer_only_runs_while_something_is_animated(parts):
    service, settings, _streamer, lights, _prepare = parts
    service.start()
    assert service.animation_running is False  # no 30-times-a-second wake-ups for nothing
    lights.animating = True
    service.flush_lights()
    assert service.animation_running is True
    lights.animating = False
    service.flush_lights()
    assert service.animation_running is False


def test_stopping_clears_the_status_so_the_window_knows(parts):
    service, settings, _streamer, _lights, _prepare = parts
    service.start()
    assert status(settings) != {}
    service.stop()
    assert settings.get_string("status") == "{}"


def test_edited_image_file_is_reloaded(parts):
    service, _settings, streamer, _lights, prepare = parts
    service.start()
    prepare.version = " v2"  # the file now has different contents
    service.on_image_file_changed()  # what the file monitor calls
    assert len(prepare.calls) == 2
    assert streamer.images == [b"jpeg of cat.png (fill)", b"jpeg of cat.png (fill) v2"]


def test_an_unchanged_picture_is_not_sent_to_the_lcd_again(parts):
    service, _settings, streamer, _lights, _prepare = parts
    service.start()
    service.on_image_file_changed()  # touched, but the picture is the same
    assert len(streamer.images) == 1


def test_resume_from_sleep_sends_everything_again(with_ram):
    service, _settings, ram = with_ram
    service.start()
    ram.forgotten = False
    ram.forget = lambda: setattr(ram, "forgotten", True)
    before = len(ram.applied)
    service.on_resume()
    assert ram.forgotten is True and len(ram.applied) == before + 1


# --- auto mode ----------------------------------------------------------------


class FakeLooks:
    """Stands in for AppLooks: firefox is orange, everything else unknown."""

    def look_for(self, app_id):
        from omenrgb.app_look import Look

        return Look("#FF8800", b"firefox icon jpeg") if app_id == "firefox.desktop" else None


@pytest.fixture
def auto(settings):
    settings.set_string("lcd-image", "/pics/cat.png")
    settings.set_value("zone-colours", GLib.Variant("a{ss}", {"Case/Logo": "#0000FF"}))
    streamer, lights = FakeStreamer(), FakeLights()
    service = Service(settings, streamer, lights, FakePrepare(), looks=FakeLooks())
    service.start()
    yield service, settings, streamer, lights
    service.stop()


def focus(service, settings, app_id):
    settings.set_string("focused-app", app_id)
    service.on_changed("focused-app")
    service.flush_lights()


def test_auto_mode_is_off_by_default(auto):
    service, settings, streamer, lights = auto
    focus(service, settings, "firefox.desktop")
    assert streamer.images[-1] == b"jpeg of cat.png (fill)"
    assert lights.applied[-1][3] == {"Case/Logo": "#0000FF"}


def test_auto_mode_follows_the_focused_app(auto):
    service, settings, streamer, lights = auto
    settings.set_boolean("auto-enabled", True)
    service.on_changed("auto-enabled")
    focus(service, settings, "firefox.desktop")
    assert streamer.images[-1] == b"firefox icon jpeg"
    colours = lights.applied[-1][3]
    assert colours.get("Case/Logo") == "#FF8800" and colours.get("Anything/Else") == "#FF8800"
    assert status(settings)["auto"] == {"app": "firefox.desktop", "colour": "#FF8800"}


def test_unknown_app_falls_back_to_the_normal_settings(auto):
    service, settings, streamer, lights = auto
    settings.set_boolean("auto-enabled", True)
    focus(service, settings, "firefox.desktop")
    focus(service, settings, "mystery.desktop")
    assert streamer.images[-1] == b"jpeg of cat.png (fill)"
    assert lights.applied[-1][3] == {"Case/Logo": "#0000FF"}
    assert status(settings)["auto"] == {"app": "", "colour": ""}


def test_lights_and_lcd_can_follow_separately(auto):
    service, settings, streamer, lights = auto
    settings.set_boolean("auto-enabled", True)
    settings.set_boolean("auto-lcd", False)
    focus(service, settings, "firefox.desktop")
    assert streamer.images[-1] == b"jpeg of cat.png (fill)"
    assert lights.applied[-1][3].get("Case/Logo") == "#FF8800"
    settings.set_boolean("auto-lcd", True)
    settings.set_boolean("auto-lights", False)
    service.on_changed("auto-lcd")
    service.on_changed("auto-lights")
    service.flush_lights()
    assert streamer.images[-1] == b"firefox icon jpeg"
    assert lights.applied[-1][3] == {"Case/Logo": "#0000FF"}


def test_switching_auto_off_restores_the_normal_look(auto):
    service, settings, streamer, lights = auto
    settings.set_boolean("auto-enabled", True)
    focus(service, settings, "firefox.desktop")
    settings.set_boolean("auto-enabled", False)
    service.on_changed("auto-enabled")
    service.flush_lights()
    assert streamer.images[-1] == b"jpeg of cat.png (fill)"
    assert lights.applied[-1][3] == {"Case/Logo": "#0000FF"}


def test_switching_apps_does_not_reload_the_picture_from_disk(auto):
    service, settings, _streamer, _lights = auto
    settings.set_boolean("auto-enabled", True)
    service.on_changed("auto-enabled")
    prepare = service._prepare
    before = len(prepare.calls)
    for app in ("mystery.desktop", "firefox.desktop", "other.desktop"):
        focus(service, settings, app)
    assert len(prepare.calls) == before  # decoding a big photo on every alt-tab would stutter


def test_case_lights_are_not_resent_when_the_auto_colour_is_unchanged(auto):
    service, settings, _streamer, lights = auto
    settings.set_boolean("auto-enabled", True)
    focus(service, settings, "mystery.desktop")
    settings.set_string("focused-app", "another-unknown.desktop")  # still the normal look
    service.on_changed("focused-app")
    assert service.lights_update_pending is False
    settings.set_string("focused-app", "firefox.desktop")  # now the colour changes
    service.on_changed("focused-app")
    assert service.lights_update_pending is True


def test_a_broken_picture_is_reported_even_while_an_app_icon_is_shown(auto):
    service, settings, _streamer, _lights = auto
    settings.set_boolean("auto-enabled", True)
    focus(service, settings, "firefox.desktop")
    service._prepare.error = "boom"
    service.on_image_file_changed()
    assert status(settings)["error"] == "boom"


def test_resume_also_makes_the_case_lights_start_over(parts):
    service, _settings, _streamer, lights, _prepare = parts
    service.start()
    lights.forgotten = False
    lights.forget = lambda: setattr(lights, "forgotten", True)
    service.on_resume()
    assert lights.forgotten is True
