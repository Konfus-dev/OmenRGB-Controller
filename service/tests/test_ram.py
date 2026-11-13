# SPDX-License-Identifier: GPL-3.0-or-later
from omenrgb.lights import DeviceInfo
from omenrgb.ram import RamLights, commands, find_sticks

BEGIN, END = 0x53, 0x44
LEFT, RIGHT = 0xC2, 0xC6  # slots 1 and 3, as on the test machine


def make_sysfs(tmp_path, *spd_addresses, name="spd5118"):
    for address in spd_addresses:
        node = tmp_path / f"0-00{address:02x}"
        node.mkdir()
        (node / "name").write_text(name + "\n")
    return tmp_path


def test_find_sticks(tmp_path):
    assert find_sticks(make_sysfs(tmp_path, 0x53, 0x51)) == [LEFT, RIGHT]


def test_find_sticks_ignores_other_chips_and_missing_sysfs(tmp_path):
    assert find_sticks(make_sysfs(tmp_path, 0x51, name="eeprom")) == []
    assert find_sticks(tmp_path / "missing") == []


def test_static_is_hps_sequence_with_a_colour_per_stick():
    result = commands("Static", 80, [(255, 0, 0), (0, 0, 255)], [LEFT, RIGHT])
    assert result == [
        (LEFT, 0x08, BEGIN), (RIGHT, 0x08, BEGIN),
        (LEFT, 0x09, 0x00), (RIGHT, 0x09, 0x00),
        (LEFT, 0x20, 80), (RIGHT, 0x20, 80),
        (LEFT, 0x30, 1), (RIGHT, 0x30, 1),
        (LEFT, 0x31, 255), (RIGHT, 0x31, 0),
        (LEFT, 0x32, 0), (RIGHT, 0x32, 0),
        (LEFT, 0x33, 0), (RIGHT, 0x33, 255),
        (LEFT, 0x08, END), (RIGHT, 0x08, END),
    ]  # fmt: skip


def test_off_is_hps_sequence():
    assert commands("Off", 80, [(1, 2, 3)], [LEFT]) == [
        (LEFT, 0x08, BEGIN), (LEFT, 0x20, 0), (LEFT, 0x08, END),
    ]  # fmt: skip


def test_rainbow_sets_effect_and_brightness_and_stick_order():
    result = commands("Rainbow", 60, [], [LEFT, RIGHT])
    assert result[0] == (LEFT, 0x08, BEGIN) and result[-1] == (RIGHT, 0x08, END)
    assert (LEFT, 0x09, 0x01) in result and (RIGHT, 0x20, 60) in result
    # Each stick is told its position so the rainbow flows across them.
    assert (LEFT, 0x0B, 1) in result and (RIGHT, 0x0B, 0) in result
    assert (LEFT, 0x27, 2) in result


def test_breathing_uses_each_sticks_colour():
    result = commands("Breathing", 100, [(255, 0, 0), (0, 255, 0)], [LEFT, RIGHT])
    assert (LEFT, 0x09, 0x03) in result
    assert (LEFT, 0x31, 255) in result and (RIGHT, 0x32, 255) in result


def test_every_command_is_one_the_root_helper_accepts():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "helper" / "omenrgb_ram_helper.py"
    spec = importlib.util.spec_from_file_location("helper", path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    for mode in ("Static", "Rainbow", "Breathing", "Off"):
        for address, register, value in commands(
            mode, 100, [(1, 2, 3)] * 4, [0xC0, 0xC2, 0xC4, 0xC6]
        ):
            helper.parse_request(f"W {address:02x} {register:02x} {value:02x}")  # must not raise


def test_unknown_mode_falls_back_to_static_and_values_are_clamped():
    result = commands("Disco", 250, [(300, -5, 0)], [LEFT])
    assert (LEFT, 0x09, 0x00) in result and (LEFT, 0x20, 100) in result
    assert (LEFT, 0x31, 255) in result and (LEFT, 0x32, 0) in result


def test_missing_colours_default_to_white():
    assert (RIGHT, 0x31, 255) in commands("Static", 100, [(1, 1, 1)], [LEFT, RIGHT])


def test_no_sticks_no_commands():
    assert commands("Static", 100, [], []) == []


class Sender:
    def __init__(self):
        self.batches, self.fail = [], False

    def __call__(self, batch):
        if self.fail:
            raise OSError("helper not running")
        self.batches.append(batch)


def connected(tmp_path, *spd):
    sender = Sender()
    sysfs = make_sysfs(tmp_path, *spd)
    ram = RamLights(sender, lambda: find_sticks(sysfs))
    assert ram.try_connect() is True
    sender.batches.clear()  # drop the connection test
    return ram, sender


def test_devices_lists_ram_like_any_other_light(tmp_path):
    ram, _ = connected(tmp_path, 0x51, 0x53)
    assert ram.devices() == [
        DeviceInfo(
            "RAM",
            ("Static", "Rainbow", "Breathing"),
            ("Stick 1", "Stick 2"),
            default_mode="Rainbow",
            colourless_modes=("Rainbow",),
        )
    ]


def test_apply_reads_mode_and_per_stick_colours(tmp_path):
    ram, sender = connected(tmp_path, 0x51, 0x53)
    ram.apply(True, 40, {"RAM": "Static"}, {"RAM/Stick 2": "#00FF00"})
    assert sender.batches == [commands("Static", 40, [(255, 255, 255), (0, 255, 0)], [LEFT, RIGHT])]


def test_apply_defaults_to_rainbow(tmp_path):
    ram, sender = connected(tmp_path, 0x51)
    ram.apply(True, 100, {}, {})
    assert sender.batches == [commands("Rainbow", 100, [(255, 255, 255)], [LEFT])]


def test_apply_disabled_turns_sticks_off(tmp_path):
    ram, sender = connected(tmp_path, 0x51)
    ram.apply(False, 100, {"RAM": "Static"}, {})
    assert sender.batches == [commands("Off", 100, [], [LEFT])]


def test_identical_apply_is_not_sent_twice(tmp_path):
    ram, sender = connected(tmp_path, 0x51)
    ram.apply(True, 100, {}, {})
    ram.apply(True, 100, {}, {})
    assert len(sender.batches) == 1


def test_helper_missing_is_quiet(tmp_path):
    sender = Sender()
    sender.fail = True
    ram = RamLights(sender, lambda: [LEFT])
    assert ram.try_connect() is False and ram.connected is False and ram.devices() == []
    ram.apply(True, 100, {}, {})  # must not raise


def test_no_sticks_means_not_connected():
    assert RamLights(Sender(), lambda: []).try_connect() is False


def test_send_failure_disconnects_then_recovers_and_resends(tmp_path):
    ram, sender = connected(tmp_path, 0x51)
    sender.fail = True
    ram.apply(True, 100, {}, {})
    assert ram.connected is False and ram.check() is False
    sender.fail = False
    assert ram.try_connect() is True
    ram.apply(True, 100, {}, {})
    assert sender.batches[-1] == commands("Rainbow", 100, [(255, 255, 255)], [LEFT])
