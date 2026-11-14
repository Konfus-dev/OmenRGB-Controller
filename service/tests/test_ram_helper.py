# SPDX-License-Identifier: GPL-3.0-or-later
"""The root helper is the only privileged code in the project, so its
checks are tested hard. It lives outside the package (it must run with the
standard library only), so it is loaded from its file."""

import importlib.util
from pathlib import Path

import pytest

HELPER = Path(__file__).resolve().parents[2] / "helper" / "omenrgb_ram_helper.py"
spec = importlib.util.spec_from_file_location("omenrgb_ram_helper", HELPER)
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)


def make_sysfs(tmp_path, *spd_addresses):
    for address in spd_addresses:
        node = tmp_path / f"0-00{address:02x}"
        node.mkdir()
        (node / "name").write_text("spd5118\n")
    return tmp_path


def test_parse_valid_request():
    assert h.parse_request("W c2 20 64") == (0xC2, 0x20, 0x64)
    assert h.parse_request("  w C6 08 53 \n") == (0xC6, 0x08, 0x53)


@pytest.mark.parametrize(
    "line",
    [
        "",
        "W",
        "W c2 20",  # too few fields
        "W c2 20 64 00",  # too many fields
        "R c2 20 64",  # only writes exist
        "W zz 20 64",  # not hex
        "W c2 20 100",  # value over one byte
        "W c2 -1 00",
        "W c1 20 64",  # odd address: not a stick
        "W a0 20 64",  # the memory's own configuration chip: never
        "W c8 20 64",  # beyond the fourth slot
        "W c2 00 00",  # register outside the lighting set
        "W c2 06 00",  # model register is read-only
        "W c2 80 00",
        "W c2 08 00",  # register 0x08 only takes begin (53) or end (44)
        "W c2 20 65",  # brightness above 100
        "W c2 0c 03",  # direction is 1 or 2
        "W c2 09 ff; rm -rf /",
    ],
)
def test_parse_rejects(line):
    with pytest.raises(ValueError):
        h.parse_request(line)


def test_every_allowed_register_is_a_lighting_register():
    # Guards against someone widening the list by accident.
    assert set(h.ALLOWED_REGISTERS) <= set(range(0x08, 0x74))
    assert 0x06 not in h.ALLOWED_REGISTERS and 0x00 not in h.ALLOWED_REGISTERS
    assert h.ALLOWED_ADDRESSES == (0xC0, 0xC2, 0xC4, 0xC6)


def test_acpi_command_matches_what_worked_on_the_hardware():
    assert h.acpi_command(0xC2, 0x20, 0x64) == (
        "\\_SB.WMID.WMAA 0x0 0x2 b53454355090002000a000000030000002064c2"
    )


def test_reply_ok():
    good = "{0x50, 0x41, 0x53, 0x53, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00}"
    assert h.reply_ok(good)
    assert not h.reply_ok("{0x50, 0x41, 0x53, 0x53, 0x06, 0x00, 0x00, 0x00}")  # BIOS said no
    assert not h.reply_ok("Error: AE_NOT_FOUND")
    assert not h.reply_ok("")


def test_stick_present_checks_the_matching_slot(tmp_path):
    sysfs = make_sysfs(tmp_path, 0x51, 0x53)
    assert h.stick_present(0xC2, sysfs) and h.stick_present(0xC6, sysfs)
    assert not h.stick_present(0xC0, sysfs) and not h.stick_present(0xC4, sysfs)


def test_stick_present_ignores_other_chips(tmp_path):
    node = tmp_path / "0-0051"
    node.mkdir()
    (node / "name").write_text("something-else\n")
    assert not h.stick_present(0xC2, tmp_path)


def run(line, tmp_path, reply="{0x50, 0x41, 0x53, 0x53, 0x00, 0x00, 0x00, 0x00}"):
    sent = []

    def call(command):
        sent.append(command)
        return reply

    answer = h.handle_line(
        line, call, make_sysfs(tmp_path, 0x51), sleep=lambda s: None, ready=lambda: True
    )
    return answer, sent


def test_ping_fails_when_the_bios_cannot_be_reached(tmp_path):
    # Without acpi_call every write would fail, so "connected" would be a lie.
    answer = h.handle_line("PING", lambda c: "", make_sysfs(tmp_path, 0x51), ready=lambda: False)
    assert answer.startswith("ERR")


def test_handle_line_ok(tmp_path):
    answer, sent = run("W c2 20 64", tmp_path)
    assert answer == "OK" and len(sent) == 1


def test_handle_line_ping_touches_nothing(tmp_path):
    answer, sent = run("PING", tmp_path)
    assert answer == "OK" and sent == []


def test_handle_line_refuses_bad_request_without_calling_bios(tmp_path):
    answer, sent = run("W a0 00 00", tmp_path)
    assert answer.startswith("ERR") and sent == []


def test_handle_line_refuses_empty_slot_without_calling_bios(tmp_path):
    answer, sent = run("W c6 20 64", tmp_path)  # only slot 1 (0x51) has a stick
    assert answer.startswith("ERR") and sent == []


def test_handle_line_reports_bios_failure(tmp_path):
    answer, _sent = run("W c2 20 64", tmp_path, reply="Error: AE_NOT_FOUND")
    assert answer.startswith("ERR")


def test_handle_line_survives_call_errors(tmp_path):
    def call(command):
        raise OSError("acpi_call not loaded")

    answer = h.handle_line("W c2 20 64", call, make_sysfs(tmp_path, 0x51), sleep=lambda s: None)
    assert answer.startswith("ERR")
