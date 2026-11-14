# SPDX-License-Identifier: GPL-3.0-or-later
from types import SimpleNamespace

import pytest

from omenrgb.core.lights import (
    DeviceInfo,
    EffectOptions,
    Lights,
    breath_level,
    scale_colour,
    zone_key,
)


def rgb(colour):
    return (colour.red, colour.green, colour.blue)


class FakeZone:
    def __init__(self, name, fail=False):
        self.name, self.fail, self.colour = name, fail, None
        self.leds = [object()]  # one LED per zone, as on the OMEN case

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
        # speed_min/speed_max mirror OpenRGB: only moving effects have them,
        # and speed_max is the FASTEST value even when it is the smaller number.
        self.modes = [
            SimpleNamespace(
                name=m,
                colors=[None] * palettes[m] if m in palettes else None,
                color_mode=0 if m == "Off" else 1,
                speed_min=1 if m in palettes else None,
                speed_max=3 if m in palettes else None,
                speed=2 if m in palettes else None,
            )
            for m in modes
        ]
        self.zones = [FakeZone(z) for z in zones]
        self.fail = fail
        self.mode = None
        self.frames = []  # every set_colors call, as (r, g, b) per LED

    def set_mode(self, mode):
        if self.fail:
            raise OSError("write failed")
        self.mode = mode.name

    def set_colors(self, colours, fast=False):
        if self.fail:
            raise OSError("write failed")
        frame = [rgb(c) for c in colours]
        self.frames.append(frame)
        for zone, colour in zip(self.zones, frame, strict=True):  # one LED per zone
            zone.colour = colour


def make(*devices):
    lights = Lights(lambda: SimpleNamespace(devices=list(devices)))
    assert lights.try_connect() is True
    return lights


def case():
    return FakeDevice(
        "Case",
        ["Direct", "Static", "Off", "Breathing", "Wave"],
        ["Logo", "Top Fan"],
        palettes={"Breathing": 4, "Wave": 4},
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
            ("Direct", "Static", "Off", "Breathing", "Wave"),
            ("Logo", "Top Fan"),
            default_mode="Static",
            colourless_modes=("Off",),
            speed_modes=("Breathing", "Wave"),
            range_modes=("Breathing",),
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
    assert device.mode == "Direct"  # plain colours are driven by us, in direct mode
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
        True, 100, {"Case": "Wave"}, {"Case/Logo": "#FF0000", "Case/Top Fan": "#0000FF"}
    )
    assert device.mode == "Wave"
    palette = next(m for m in device.modes if m.name == "Wave").colors
    assert [rgb(c) for c in palette] == [(255, 0, 0), (0, 0, 255), (255, 0, 0), (0, 0, 255)]
    assert [z.colour for z in device.zones] == [None, None]  # effects ignore per-zone writes


def test_disabled_uses_off_mode_on_a_device_we_cannot_drive_directly():
    device = FakeDevice("Case", ["Static", "Off", "Wave"], ["Logo"], palettes={"Wave": 4})
    make(device).apply(False, 100, {"Case": "Wave"}, {})
    assert device.mode == "Off"


def test_disabled_sets_black_without_off_mode():
    device = FakeDevice("Case", ["Direct", "Static"], ["Logo"])
    make(device).apply(False, 100, {}, {"Case/Logo": "#FF0000"})
    assert device.zones[0].colour == (0, 0, 0)


def test_device_without_modes_is_skipped():
    empty, good = FakeDevice("Empty", [], ["Z"]), case()
    make(empty, good).apply(True, 100, {}, {})
    assert good.mode == "Direct"


def test_one_failing_device_does_not_stop_others():
    bad, good = FakeDevice("Bad", ["Static"], ["Z"], fail=True), case()
    lights = make(bad, good)
    lights.apply(True, 100, {}, {})
    assert good.mode == "Direct"
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
    assert good.mode == "Direct"


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


def test_gamma_makes_dark_colours_look_right_on_leds():
    # LEDs show a "dark purple" as pale lilac unless the values are curved.
    assert scale_colour("#613583", 100, gamma=2.2) == (30, 8, 59)
    assert scale_colour("#FF0000", 100, gamma=2.2) == (255, 0, 0)  # pure colours unchanged
    assert scale_colour("#FFFFFF", 50, gamma=2.2) == (128, 128, 128)  # brightness stays linear


def test_lights_apply_gamma_to_zone_colours():
    device = case()
    lights = Lights(lambda: SimpleNamespace(devices=[device]), gamma=2.2)
    lights.try_connect()
    lights.apply(True, 100, {}, {"Case/Logo": "#613583"})
    assert device.zones[0].colour == (30, 8, 59)


def wave_speed(percent):
    device = case()
    make(device).apply(True, 100, {"Case": "Wave"}, {}, {"Case": EffectOptions(speed=percent)})
    return next(m for m in device.modes if m.name == "Wave").speed


def test_speed_percent_maps_onto_the_effects_own_range():
    assert wave_speed(0) == 1 and wave_speed(50) == 2 and wave_speed(100) == 3


def test_speed_is_clamped_and_optional():
    assert wave_speed(900) == 3 and wave_speed(-5) == 1
    device = case()
    make(device).apply(True, 100, {"Case": "Wave"}, {})  # no options given
    assert next(m for m in device.modes if m.name == "Wave").speed == 2  # 50 percent


def test_speed_follows_an_inverted_range():
    # Some OpenRGB effects count downwards: 60 is slowest, 0 fastest.
    device = case()
    mode = next(m for m in device.modes if m.name == "Wave")
    mode.speed_min, mode.speed_max = 60, 0
    make(device).apply(True, 100, {"Case": "Wave"}, {}, {"Case": EffectOptions(speed=100)})
    assert mode.speed == 0


# --- Breathing is animated by us, so that dimmest/brightest/speed all work -----


def test_breath_level_swings_between_dimmest_and_brightest():
    period = 4.5  # seconds at speed 50
    assert breath_level(0, 50, 20, 80) == pytest.approx(0.20)
    assert breath_level(period / 2, 50, 20, 80) == pytest.approx(0.80)
    assert breath_level(period, 50, 20, 80) == pytest.approx(0.20)
    assert 0.20 < breath_level(period / 4, 50, 20, 80) < 0.80


def test_breath_level_speed_and_sane_limits():
    assert breath_level(0.5, 100, 0, 100) == pytest.approx(1.0)  # fastest: 1 s per breath
    assert breath_level(4.0, 0, 0, 100) == pytest.approx(1.0)  # slowest: 8 s per breath
    assert breath_level(0, 50, 90, 40) <= 0.40  # dimmest above brightest is pulled down
    assert breath_level(0, 50, -20, 500) == 0.0


def breathing(options=None, colours=None, brightness=100):
    device = case()
    lights = make(device)
    lights.apply(True, brightness, {"Case": "Breathing"}, colours or {}, options)
    return lights, device


def test_breathing_runs_in_direct_mode_and_is_animated():
    lights, device = breathing()
    assert device.mode == "Direct" and lights.animating is True


def test_other_effects_are_not_animated():
    device = case()
    lights = make(device)
    lights.apply(True, 100, {"Case": "Static"}, {})
    assert lights.animating is False
    sent = len(device.frames)
    lights.tick_animation(1.0)
    assert len(device.frames) == sent  # nothing more to send: no cost while idle


def test_each_tick_sends_one_frame_at_the_current_level():
    options = {"Case": EffectOptions(speed=50, min_brightness=20, max_brightness=80)}
    lights, device = breathing(options, {"Case/Logo": "#FF0000", "Case/Top Fan": "#0000FF"})
    lights.tick_animation(100.0)  # first tick starts the breath at its dimmest
    lights.tick_animation(100.0 + 2.25)  # half a breath later: brightest
    assert device.frames == [
        [(51, 0, 0), (0, 0, 51)],
        [(204, 0, 0), (0, 0, 204)],
    ]


def test_overall_brightness_scales_the_breath():
    lights, device = breathing({"Case": EffectOptions(min_brightness=100)}, brightness=50)
    lights.tick_animation(1.0)
    assert device.frames == [[(128, 128, 128), (128, 128, 128)]]


def test_hidden_zones_stay_dark_while_breathing():
    device = FakeDevice("Case", ["Direct", "Breathing"], ["Logo", "Dead", "Fan"])
    lights = Lights(lambda: SimpleNamespace(devices=[device]), hidden_zones={"Case/Dead"})
    lights.try_connect()
    lights.apply(True, 100, {"Case": "Breathing"}, {}, {"Case": EffectOptions(min_brightness=100)})
    lights.tick_animation(1.0)
    assert device.frames == [[(255, 255, 255), (0, 0, 0), (255, 255, 255)]]


def test_switching_away_or_off_stops_the_animation():
    lights, device = breathing()
    lights.apply(True, 100, {"Case": "Static"}, {})
    assert lights.animating is False
    lights, device = breathing()
    lights.apply(False, 100, {"Case": "Breathing"}, {})
    lights.finish_transitions()
    assert lights.animating is False
    assert [z.colour for z in device.zones] == [(0, 0, 0)] * 2


def test_changing_a_slider_does_not_restart_the_breath():
    lights, device = breathing()
    lights.tick_animation(100.0)
    lights.apply(True, 100, {"Case": "Breathing"}, {}, {"Case": EffectOptions(max_brightness=60)})
    lights.tick_animation(100.0 + 2.25)  # still half a breath after the original start
    assert device.frames[-1] == [(153, 153, 153)] * 2


def test_device_without_direct_mode_keeps_the_boards_own_breathing():
    device = FakeDevice("Case", ["Static", "Breathing"], ["Logo"], palettes={"Breathing": 4})
    lights = make(device)
    lights.apply(True, 100, {"Case": "Breathing"}, {})
    assert device.mode == "Breathing" and lights.animating is False
    assert lights.devices()[0].range_modes == ()


def test_animation_error_disconnects_quietly():
    lights, device = breathing()
    device.fail = True
    lights.tick_animation(1.0)  # must not raise
    assert lights.connected is False and lights.animating is False


def test_a_slow_failing_openrgb_is_left_alone_for_a_while():
    # A half-dead server can make each connection attempt hang for seconds;
    # retrying every few seconds would starve the LCD.
    now = [1000.0]
    attempts = []

    def hanging_connect():
        attempts.append(now[0])
        now[0] += 10  # the attempt itself took ten seconds
        raise TimeoutError("no answer")

    lights = Lights(hanging_connect, clock=lambda: now[0])
    assert lights.try_connect() is False
    now[0] += 5
    assert lights.try_connect() is False
    assert len(attempts) == 1  # the second call did not even try
    now[0] += 120
    lights.try_connect()
    assert len(attempts) == 2


def test_a_quick_refusal_is_retried_straight_away():
    now = [1000.0]
    attempts = []

    def refuse():
        attempts.append(1)
        raise ConnectionRefusedError("not running")

    lights = Lights(refuse, clock=lambda: now[0])
    lights.try_connect()
    lights.try_connect()
    assert len(attempts) == 2


def test_forget_restarts_a_breath_from_scratch():
    # After sleep the board may have left direct mode; say so again.
    lights, device = breathing()
    device.mode = None
    lights.forget()
    lights.apply(True, 100, {"Case": "Breathing"}, {})
    assert device.mode == "Direct" and lights.animating is True


# --- colour changes glide instead of jumping --------------------------------------


def near(colour, expected):
    """Equal within one step: fades land on halves, which round either way."""
    return all(abs(a - b) <= 1 for a, b in zip(colour, expected, strict=True))


def static(colours):
    device = case()
    lights = make(device)
    lights.apply(True, 100, {"Case": "Static"}, colours)
    return lights, device


def test_first_colours_appear_at_once():
    lights, device = static({"Case/Logo": "#FF0000"})
    assert device.zones[0].colour == (255, 0, 0) and lights.animating is False


def test_a_colour_change_fades_over_a_moment():
    lights, device = static({"Case/Logo": "#FF0000"})
    lights.apply(True, 100, {"Case": "Static"}, {"Case/Logo": "#0000FF"})
    assert lights.animating is True
    lights.tick_animation(50.0)  # the fade starts on its first frame
    assert device.zones[0].colour == (255, 0, 0)
    lights.tick_animation(50.2)  # half-way through a 0.4 s fade
    assert near(device.zones[0].colour, (128, 0, 128))
    lights.tick_animation(50.5)
    assert device.zones[0].colour == (0, 0, 255)
    assert lights.animating is False  # done: the fast timer can stop


def test_a_new_change_mid_fade_carries_on_from_where_it_is():
    lights, device = static({"Case/Logo": "#FF0000"})
    lights.apply(True, 100, {"Case": "Static"}, {"Case/Logo": "#0000FF"})
    lights.tick_animation(50.0)
    lights.tick_animation(50.2)  # now at (128, 0, 128)
    lights.apply(True, 100, {"Case": "Static"}, {"Case/Logo": "#000000"})
    lights.tick_animation(60.0)
    assert near(device.zones[0].colour, (128, 0, 128))  # no jump back to red or on to blue
    lights.tick_animation(60.5)
    assert device.zones[0].colour == (0, 0, 0)


def test_turning_the_lights_off_fades_to_black():
    lights, device = static({})
    lights.apply(False, 100, {"Case": "Static"}, {})
    assert lights.animating is True and device.mode == "Direct"
    lights.tick_animation(1.0)
    lights.tick_animation(1.5)
    assert [z.colour for z in device.zones] == [(0, 0, 0)] * 2


def test_finish_transitions_jumps_to_the_end():
    # Used just before sleep, when there is no time for a fade.
    lights, device = static({"Case/Logo": "#FF0000"})
    lights.apply(True, 100, {"Case": "Static"}, {"Case/Logo": "#0000FF"})
    lights.finish_transitions()
    assert device.zones[0].colour == (0, 0, 255) and lights.animating is False


def test_an_unchanged_apply_sends_nothing():
    lights, device = static({"Case/Logo": "#FF0000"})
    sent = len(device.frames)
    lights.apply(True, 100, {"Case": "Static"}, {"Case/Logo": "#FF0000"})
    assert len(device.frames) == sent and lights.animating is False


def test_breathing_colour_change_also_fades():
    lights, device = breathing(
        {"Case": EffectOptions(min_brightness=100)}, {"Case/Logo": "#FF0000"}
    )
    lights.tick_animation(10.0)
    lights.apply(
        True, 100, {"Case": "Breathing"}, {"Case/Logo": "#0000FF"},
        {"Case": EffectOptions(min_brightness=100)},
    )  # fmt: skip
    lights.tick_animation(20.0)
    lights.tick_animation(20.2)
    assert near(device.zones[0].colour, (128, 0, 128))


# --- keep-alive: a board that resets itself gets its colours back ---------------


def test_refresh_sends_the_current_colours_again():
    lights, device = static({"Case/Logo": "#FF0000"})
    sent = len(device.frames)
    lights.refresh()
    assert len(device.frames) == sent + 1 and device.frames[-1] == device.frames[-2]


def test_refresh_leaves_running_animations_alone():
    lights, device = breathing()
    lights.refresh()
    assert device.frames == []  # the animation is already sending frames


def test_refresh_without_anything_to_show_or_after_a_failure():
    lights = Lights(lambda: SimpleNamespace(devices=[]))
    lights.refresh()  # not connected: nothing happens
    lights, device = static({})
    device.fail = True
    lights.refresh()  # must not raise
    assert lights.connected is False
