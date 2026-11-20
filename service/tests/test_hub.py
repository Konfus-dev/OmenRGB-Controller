# SPDX-License-Identifier: GPL-3.0-or-later
"""The hub with several devices: per-device auto, the Default look, status."""

import json

from rgbhub.core.hub import Hub
from rgbhub.core.look import Look
from rgbhub.core.settings_schema import DEFAULT_KEYS, migrate, parse_default
from rgbhub.devices.iqunix_keyboard import IqunixKeyboard


class MemorySettings:
    def __init__(self, **values):
        from rgbhub.core.settings_schema import DEFAULTS

        self.values = {**DEFAULTS, **values}
        self.subscribers = []

    def get(self, key):
        return self.values[key]

    def set(self, key, value):
        if self.values.get(key) == value:
            return
        self.values[key] = value
        for callback in list(self.subscribers):
            callback(key)

    def subscribe(self, callback):
        self.subscribers.append(callback)

    def unsubscribe(self, callback):
        self.subscribers.remove(callback)


class FakeLights:
    def __init__(self):
        self.connected = False
        self.applied = []
        self.stored = 0

    def try_connect(self):
        self.connected = True
        return True

    def check(self):
        return self.connected

    def devices(self):
        return []

    def forget(self):
        pass

    def apply(self, enabled, brightness, modes, colours, options=None):
        self.applied.append((enabled, brightness, dict(modes), colours.get("K/All keys")))

    def store(self):
        self.stored += 1


class FakeLooks:
    def is_ignored(self, subject):
        return False

    def look_for(self, subject):
        return Look("#00FF00", b"jpeg") if subject == "game" else None

    def idle_look(self, path):
        return None


def keyboard_hub(**values):
    settings = MemorySettings(**values)
    lights = FakeLights()
    keyboard = IqunixKeyboard(lights)
    hub = Hub(settings, [keyboard], looks=FakeLooks())
    return hub, settings, lights, keyboard


def test_each_device_has_its_own_auto_switch():
    hub, settings, lights, _ = keyboard_hub(
        **{"zone-colours": {"K/All keys": "#800080"}, "focused-app": "game"}
    )
    hub.start()
    assert lights.applied[-1][3] == "#800080"  # auto is off for the keyboard
    settings.set("auto-devices", {"OMEN-PC": True})
    assert not hub.auto_for("IQUNIX-KEYBOARD")
    settings.set("auto-devices", {"OMEN-PC": True, "IQUNIX-KEYBOARD": True})
    assert hub.auto_for("IQUNIX-KEYBOARD")
    for device in hub.devices:
        device.lights.flush()
    assert lights.applied[-1][3] == "#00FF00"  # the game's colour
    hub.stop(show_default=False)


def test_per_device_brightness_and_on_off():
    hub, settings, lights, _ = keyboard_hub(
        **{"device-enabled": {"IQUNIX-KEYBOARD": False}, "device-brightness": {"OMEN-PC": 10}}
    )
    hub.start()
    assert lights.applied[-1][:2] == (False, 100)  # OMEN-PC's brightness is not ours
    hub.stop(show_default=False)


def test_save_and_load_the_default_look():
    hub, settings, _lights, _ = keyboard_hub(**{"device-brightness": {"IQUNIX-KEYBOARD": 40}})
    hub.start()
    settings.set("default-action", "save")
    assert settings.get("default-action") == ""
    saved = json.loads(settings.get("default-look"))
    assert sorted(saved) == sorted(DEFAULT_KEYS) and saved["device-brightness"] == {
        "IQUNIX-KEYBOARD": 40
    }
    settings.set("device-brightness", {"IQUNIX-KEYBOARD": 90})
    settings.set("default-action", "load")
    assert settings.get("device-brightness") == {"IQUNIX-KEYBOARD": 40}
    assert json.loads(settings.get("status"))["has-default"] is True
    hub.stop(show_default=False)


def test_stopping_shows_the_default_without_auto_and_stores_it():
    default = {"zone-colours": {"K/All keys": "#800080"}, "device-modes": {"K": "Breathing"}}
    hub, settings, lights, _ = keyboard_hub(
        **{
            "default-look": json.dumps(default),
            "auto-devices": {"IQUNIX-KEYBOARD": True},
            "focused-app": "game",
            "zone-colours": {"K/All keys": "#FFFFFF"},
        }
    )
    hub.start()
    assert lights.applied[-1][3] == "#00FF00"  # following the game
    hub.stop()
    enabled, _brightness, modes, colour = lights.applied[-1]
    assert colour == "#800080" and enabled
    assert modes == {"K": "Static"}  # a breath drawn by us would freeze mid-way
    assert lights.stored == 1
    assert settings.get("status") == "{}"
    assert settings.get("zone-colours") == {"K/All keys": "#FFFFFF"}  # the settings are untouched


def test_a_failing_device_does_not_stop_the_others():
    class Broken(IqunixKeyboard):
        id = label = "BROKEN"

        def show_default(self):
            raise RuntimeError("boom")

    settings = MemorySettings()
    good = FakeLights()
    hub = Hub(settings, [Broken(FakeLights()), IqunixKeyboard(good)])
    hub.start()
    hub.stop()
    assert good.stored == 1


def test_status_lists_every_device():
    hub, settings, _lights, _ = keyboard_hub()
    hub.start()
    report = json.loads(settings.get("status"))
    assert report["devices"] == [
        {"id": "IQUNIX-KEYBOARD", "label": "IQUNIX-KEYBOARD", "connected": True, "lights": []}
    ]
    hub.stop(show_default=False)


def test_old_settings_are_carried_over():
    new = migrate(
        {
            "lights-enabled": False,
            "lights-brightness": 60,
            "auto-enabled": True,
            "lcd-image": "/a.png",
            "status": "{...}",
            "nonsense": 1,
        }
    )
    assert new == {
        "device-enabled": {"OMEN-PC": False},
        "device-brightness": {"OMEN-PC": 60},
        "auto-devices": {"OMEN-PC": True, "IQUNIX-KEYBOARD": True},
        "lcd-image": "/a.png",
    }


def test_a_damaged_default_is_ignored():
    assert parse_default("not json") == {}
    assert parse_default('{"lcd-brightness": "loud", "lcd-fit": "fit", "x": 1}') == {
        "lcd-fit": "fit"
    }
    # Inside tables too: a number where a colour belongs would break the lights.
    assert parse_default('{"zone-colours": {"RAM/Stick 1": 255}, "device-speeds": {"RAM": 9}}') == {
        "device-speeds": {"RAM": 9}
    }


def test_batocera_moves_the_old_folder_and_settings(tmp_path):
    from rgbhub.platforms.batocera import migrate_old_config

    old, new = tmp_path / "omen-lights", tmp_path / "rgb-hub"
    (old / "pictures").mkdir(parents=True)
    (old / "settings.json").write_text(
        json.dumps({"lights-brightness": 50, "lcd-image": f"{old}/pictures/spike.png"})
    )
    migrate_old_config(old, new / "settings.json")
    assert not old.exists() and (new / "pictures").is_dir()
    data = json.loads((new / "settings.json").read_text())
    assert data["device-brightness"] == {"OMEN-PC": 50}
    assert data["lcd-image"] == f"{new}/pictures/spike.png"
    assert json.loads(data["default-look"])["lcd-image"] == f"{new}/pictures/spike.png"


def test_gnome_reads_the_old_dconf_dump():
    from rgbhub.platforms.gnome_migrate import parse_dump

    dump = "[/]\nauto-enabled=true\ndevice-speeds={'RAM': 35}\nlcd-image='/a b.jpg'\n"
    assert parse_dump(dump) == {
        "auto-enabled": True,
        "device-speeds": {"RAM": 35},
        "lcd-image": "/a b.jpg",
    }
