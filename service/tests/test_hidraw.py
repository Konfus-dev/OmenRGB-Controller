# SPDX-License-Identifier: GPL-3.0-or-later
import socket

import pytest

from rgbhub.backends.hidraw import LcdDevice, find_hidraw

LCD_ID = "HID_ID=0003:000003F0:00007397\nHID_NAME=LCD-PUMP\n"


def make_node(sys_root, name, uevent, descriptor_hex):
    device = sys_root / name / "device"
    device.mkdir(parents=True)
    (device / "uevent").write_text(uevent)
    (device / "report_descriptor").write_bytes(bytes.fromhex(descriptor_hex))


@pytest.fixture
def roots(tmp_path):
    sys_root, dev_root = tmp_path / "sys", tmp_path / "dev"
    sys_root.mkdir()
    dev_root.mkdir()
    make_node(sys_root, "hidraw2", LCD_ID, "05010902a101")  # mouse-class interface
    make_node(sys_root, "hidraw8", LCD_ID, "061aff0993a1")  # vendor interface
    make_node(sys_root, "hidraw4", "HID_ID=0003:0000103C:000084FD\n", "05010900a101")
    return sys_root, dev_root


def test_finds_vendor_interface_not_mouse(roots):
    sys_root, dev_root = roots
    assert find_hidraw(sys_root, dev_root) == dev_root / "hidraw8"


def test_none_when_absent(roots):
    sys_root, dev_root = roots
    (sys_root / "hidraw8" / "device" / "report_descriptor").write_bytes(b"\x05\x01")
    assert find_hidraw(sys_root, dev_root) is None


def test_none_when_sysfs_missing(tmp_path):
    assert find_hidraw(tmp_path / "nothing", tmp_path) is None


def test_ignores_unreadable_entries(roots):
    sys_root, dev_root = roots
    (sys_root / "hidraw0" / "device").mkdir(parents=True)  # no uevent file at all
    assert find_hidraw(sys_root, dev_root) == dev_root / "hidraw8"


@pytest.fixture
def pair():
    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    device = LcdDevice.from_fd(ours.detach())
    yield device, theirs
    device.close()
    theirs.close()


def test_write_delivers_bytes(pair):
    device, peer = pair
    device.write(b"\x01\x46" + bytes(62))
    assert peer.recv(2048) == b"\x01\x46" + bytes(62)


def test_write_after_peer_closed_raises_oserror(pair):
    device, peer = pair
    peer.close()
    with pytest.raises(OSError):
        device.write(b"x")


def test_close_twice_is_fine(pair):
    device, _peer = pair
    device.close()
    device.close()
