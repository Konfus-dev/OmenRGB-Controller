# SPDX-License-Identifier: GPL-3.0-or-later
from types import SimpleNamespace

from omenrgb.lights import DeviceInfo, Lights, scale_colour, zone_key


def rgb(colour):
    return (colour.red, colour.green, colour.blue)


class FakeZone:
    def __init__(self, name, fail=False):
        self.name, self.fail, self.colour = name, fail, None

    def set_color(self, colour):
        if self.fail:
            raise OSError("write failed")
        self.colour = rgb(colour)


class FakeDevice:
    def __init__(self, name, modes, zones, palettes=None, fail=False):
        """`palettes` maps a mode name to how many colours its palette has."""
        palettes = palettes or {}
        self.name = name
        # color_mode mirrors OpenRGB: 0 = the effect takes no colours.
        self.modes = [
            SimpleNamespace(
                name=m,
                colors=[None] * palettes[m] if m in palettes else None,
                color_mode=0 if m == "Off" else 1,
            )
            for m in modes
        ]
        self.zones = [FakeZone(z) for z in zones]
        self.fail = fail
        self.mode = None

    def set_mode(self, mode):
        if self.fail:
            raise OSError("write failed")
        self.mode = mode.name


def make(*devices):
    lights = Lights(lambda: SimpleNamespace(devices=list(devices)))
    assert lights.try_connect() is True
    return lights


def case():
    return FakeDevice(
        "Case",
        ["Direct", "Static", "Off", "Breathing"],
        ["Logo", "Top Fan"],
        palettes={"Breathing": 4},
    )


def test_scale_colour():
    assert scale_colour("#FF8000", 100) == (255, 128, 0)
    assert scale_colour("#FF8000", 50) == (128, 64, 0)
    assert scale_colour("#FF8000", 0) == (0, 0, 0)
    assert scale_colour("ff8000", 100) == (255, 128, 0)
    assert scale_colour("nonsense", 100) == (255, 255, 255)
    assert scale_colour("#FF8000", 500) == (255, 128, 0)
    assert scale_colour("#FF8000", -3) == (0, 0, 0)


def test_zone_key():
    assert zone_key("Case", "Top Fan") == "Case/Top Fan"


def test_devices_lists_modes_and_zones():
    lights = make(case())
    assert lights.devices() == [
        DeviceInfo(
            "Case",
            ("Direct", "Static", "Off", "Breathing"),
            ("Logo", "Top Fan"),
            default_mode="Static",
            colourless_modes=("Off",),
        )
    ]


def test_apply_uses_saved_mode_and_zone_colours():
    device = case()
    make(device).apply(True, 100, {"Case": "Direct"}, {"Case/Logo": "#00FF00"})
    assert device.mode == "Direct"
    assert device.zones[0].colour == (0, 255, 0)


def test_apply_defaults_for_unlisted_device_and_zone():
    device = case()
    make(device).apply(True, 100, {}, {})
    assert device.mode == "Static"
    assert [z.colour for z in device.zones] == [(255, 255, 255)] * 2


def test_brightness_scales_every_zone():
    device = case()
    make(device).apply(True, 50, {}, {"Case/Logo": "#FF0000"})
    assert [z.colour for z in device.zones] == [(128, 0, 0), (128, 128, 128)]


def test_unknown_mode_falls_back_to_direct():
    device = FakeDevice("Case", ["Wave", "Direct"], ["Logo"])
    make(device).apply(True, 100, {"Case": "Disco"}, {})
    assert device.mode == "Direct"


def test_unknown_mode_falls_back_to_first_mode_without_direct():
    device = FakeDevice("Case", ["Wave", "Pulse"], ["Logo"])
    make(device).apply(True, 100, {"Case": "Disco"}, {})
    assert device.mode == "Wave"


def test_effect_mode_palette_is_filled_from_zone_colours():
    device = case()
    make(device).apply(
        True, 100, {"Case": "Breathing"}, {"Case/Logo": "#FF0000", "Case/Top Fan": "#0000FF"}
    )
    assert device.mode == "Breathing"
    palette = next(m for m in device.modes if m.name == "Breathing").colors
    assert [rgb(c) for c in palette] == [(255, 0, 0), (0, 0, 255), (255, 0, 0), (0, 0, 255)]
    assert [z.colour for z in device.zones] == [None, None]  # effects ignore per-zone writes


def test_disabled_uses_off_mode_when_there_is_one():
    device = case()
    make(device).apply(False, 100, {"Case": "Breathing"}, {})
    assert device.mode == "Off"


def test_disabled_sets_black_without_off_mode():
    device = FakeDevice("Case", ["Direct", "Static"], ["Logo"])
    make(device).apply(False, 100, {}, {"Case/Logo": "#FF0000"})
    assert device.zones[0].colour == (0, 0, 0)


def test_device_without_modes_is_skipped():
    empty, good = FakeDevice("Empty", [], ["Z"]), case()
    make(empty, good).apply(True, 100, {}, {})
    assert good.mode == "Static"


def test_one_failing_device_does_not_stop_others():
    bad, good = FakeDevice("Bad", ["Static"], ["Z"], fail=True), case()
    lights = make(bad, good)
    lights.apply(True, 100, {}, {})
    assert good.mode == "Static"
    assert lights.connected is False  # so the service reconnects and retries


def test_connect_failure_is_quiet():
    def refuse():
        raise ConnectionRefusedError("OpenRGB is not running")

    lights = Lights(refuse)
    assert lights.try_connect() is False
    assert lights.connected is False
    assert lights.devices() == []
    lights.apply(True, 100, {}, {})  # must not raise


def test_no_devices_yet_counts_as_not_connected():
    # OpenRGB opens its port a few seconds before it has finished detecting.
    lights = Lights(lambda: SimpleNamespace(devices=[]))
    assert lights.try_connect() is False


def test_reconnect_after_error():
    bad, good = FakeDevice("Case", ["Static"], ["Z"], fail=True), case()
    clients = [SimpleNamespace(devices=[bad]), SimpleNamespace(devices=[good])]
    lights = Lights(lambda: clients.pop(0))
    lights.try_connect()
    lights.apply(True, 100, {}, {})
    assert lights.connected is False
    assert lights.try_connect() is True
    lights.apply(True, 100, {}, {})
    assert good.mode == "Static"


def test_check_keeps_healthy_connection():
    client = SimpleNamespace(devices=[case()], update=lambda: None)
    lights = Lights(lambda: client)
    lights.try_connect()
    assert lights.check() is True and lights.connected is True


def test_check_detects_dead_connection():
    def dead():
        raise ConnectionResetError("OpenRGB went away")

    lights = Lights(lambda: SimpleNamespace(devices=[case()], update=dead))
    lights.try_connect()
    assert lights.check() is False and lights.connected is False


def test_check_when_never_connected():
    assert Lights(lambda: SimpleNamespace(devices=[])).check() is False


def hidden_setup():
    device = FakeDevice(
        "Case", ["Static", "Breathing"], ["Logo", "Dead", "Fan"], palettes={"Breathing": 4}
    )
    lights = Lights(lambda: SimpleNamespace(devices=[device]), hidden_zones={"Case/Dead"})
    lights.try_connect()
    return lights, device


def test_hidden_zones_are_not_listed():
    lights, _device = hidden_setup()
    assert lights.devices()[0].zones == ("Logo", "Fan")


def test_hidden_zones_are_not_written():
    lights, device = hidden_setup()
    lights.apply(True, 100, {}, {"Case/Dead": "#FF0000"})
    assert [z.colour for z in device.zones] == [(255, 255, 255), None, (255, 255, 255)]


def test_hidden_zones_stay_out_of_effect_palettes():
    lights, device = hidden_setup()
    lights.apply(True, 100, {"Case": "Breathing"}, {"Case/Logo": "#FF0000", "Case/Fan": "#0000FF"})
    palette = next(m for m in device.modes if m.name == "Breathing").colors
    assert [rgb(c) for c in palette] == [(255, 0, 0), (0, 0, 255), (255, 0, 0), (0, 0, 255)]
