# SPDX-License-Identifier: GPL-3.0-or-later
from rgbhub.backends import via
from rgbhub.core.lights import Lights
from rgbhub.iqunix_mg65 import MG65


class FakePort:
    def __init__(self):
        self.sent = []

    def write(self, report):
        assert len(report) == 1 + via.REPORT_LEN
        self.sent.append(report)

    def drain(self):
        pass

    def close(self):
        self.closed = True


def commands(port):
    """Each report as (command, channel, value id, data...), trailing zeros cut."""
    return [tuple(report[1:].rstrip(b"\x00")) for report in port.sent]


def keyboard():
    port = FakePort()
    client = via.ViaClient(port, MG65)
    return port, client, Lights(lambda: client)


def test_reports_match_the_bytes_the_keyboard_answered_to():
    # Read on the real MG65: 08 03 01 -> brightness, 08 03 04 -> hue, saturation.
    assert via.get_report(1)[:4] == b"\x00\x08\x03\x01"
    assert via.set_report(4, 0x22, 0xFF)[:6] == b"\x00\x07\x03\x04\x22\xff"
    assert via.save_report()[:3] == b"\x00\x09\x03"


def test_colour_becomes_hue_saturation_and_brightness():
    assert via.colour_to_hsv(255, 0, 0) == (0, 255, 255)
    assert via.colour_to_hsv(0, 0, 128) == (170, 255, 128)


def test_a_static_colour_is_sent_once_and_only_changes_after():
    port, _client, lights = keyboard()
    assert lights.try_connect()
    lights.apply(True, 100, {}, {"IQUNIX MG65/All keys": "#FF0000"})
    assert commands(port) == [
        (7, 3, 2, 6),
        (7, 3, 4, 0, 255),
        (7, 3, 1, 9),
    ]  # effect, colour, brightness
    lights.apply(True, 100, {}, {"IQUNIX MG65/All keys": "#FF0000"})
    assert len(port.sent) == 3  # nothing changed: nothing sent
    lights.apply(True, 50, {}, {"IQUNIX MG65/All keys": "#FF0000"})
    assert commands(port)[3:] == [(7, 3, 1, 5)]  # only the brightness


def test_off_darkens_and_on_comes_back():
    port, _client, lights = keyboard()
    lights.try_connect()
    lights.apply(True, 100, {}, {"IQUNIX MG65/All keys": "#00FF00"})
    lights.apply(False, 100, {}, {"IQUNIX MG65/All keys": "#00FF00"})
    assert commands(port)[-1] == (7, 3, 1)  # brightness 0
    lights.apply(True, 100, {}, {"IQUNIX MG65/All keys": "#00FF00"})
    assert commands(port)[-1] == (7, 3, 1, 9)


def test_a_moving_effect_gets_its_speed():
    port, _client, lights = keyboard()
    lights.try_connect()
    from rgbhub.core.lights import EffectOptions

    lights.apply(True, 100, {"IQUNIX MG65": "Laser"}, {}, {"IQUNIX MG65": EffectOptions(speed=100)})
    assert (7, 3, 2, 9) in commands(port) and (7, 3, 3, 4) in commands(port)


def test_forgetting_sends_everything_again_and_store_saves():
    port, client, lights = keyboard()
    lights.try_connect()
    lights.apply(True, 100, {}, {})
    sent = len(port.sent)
    lights.forget()
    lights.apply(True, 100, {}, {})
    assert len(port.sent) == sent + 3
    lights.store()
    assert commands(port)[-1] == (9, 3)


def test_the_keyboard_is_found_by_its_via_interface(tmp_path):
    sys_root, dev_root = tmp_path / "sys", tmp_path / "dev"
    for name, descriptor in (("hidraw3", "05010906"), ("hidraw4", "0660ff0961a101")):
        node = sys_root / name / "device"
        node.mkdir(parents=True)
        (node / "uevent").write_text(f"HID_ID={MG65.hid_id}\n")
        (node / "report_descriptor").write_bytes(bytes.fromhex(descriptor))
    dev_root.mkdir()
    (dev_root / "hidraw4").write_bytes(b"")
    client = via.opener(MG65, sys_root, dev_root)()
    assert client._node == dev_root / "hidraw4"
    client.disconnect()
