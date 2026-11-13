# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from omenrgb import lcd_protocol as p


def test_claim_report():
    r = p.claim_report()
    assert len(r) == 64 and r[:2] == b"\x01\x41" and r[5] == 1
    assert sum(r) == 0x01 + 0x41 + 1  # everything else zero


def test_info_report():
    r = p.info_report()
    assert len(r) == 64 and r[:2] == b"\x01\x46" and sum(r) == 0x01 + 0x46


def test_brightness_report_matches_captured_head():
    r = p.brightness_report(0)
    assert len(r) == 1024
    assert r[:18].hex() == "026c00000001000001000804000000000019"
    assert p.brightness_report(57)[12] == 57


def test_brightness_is_clamped():
    assert p.brightness_report(-5)[12] == 0
    assert p.brightness_report(250)[12] == 100


def test_frame_reports_single_chunk():
    [r] = p.frame_reports(b"\xff\xd8" + b"A" * 98)
    assert len(r) == 1024 and r[:2] == b"\x02\x6e"
    assert r[4:6] == (100).to_bytes(2, "big")
    assert r[8] == 0 and r[9:11] == (100).to_bytes(2, "big")
    assert r[11:111] == b"\xff\xd8" + b"A" * 98 and r[111:] == bytes(913)


def test_frame_reports_multi_chunk_roundtrip():
    jpeg = bytes(range(256)) * 10  # 2560 bytes -> 3 chunks
    reports = p.frame_reports(jpeg)
    assert [r[8] for r in reports] == [0, 1, 2]
    assert [int.from_bytes(r[9:11], "big") for r in reports] == [1013, 1013, 534]
    assert all(r[4:6] == (2560).to_bytes(2, "big") for r in reports)
    assert b"".join(r[11 : 11 + int.from_bytes(r[9:11], "big")] for r in reports) == jpeg


def test_frame_reports_rejects_bad_sizes():
    with pytest.raises(ValueError):
        p.frame_reports(b"")
    with pytest.raises(ValueError):
        p.frame_reports(bytes(60001))


def test_claim_ack():
    ack = bytearray(64)
    ack[0:2] = b"\x01\x41"
    ack[5] = 7
    assert p.is_claim_ack(bytes(ack))
    assert not p.is_claim_ack(p.claim_report())
    assert not p.is_claim_ack(b"")


def test_parse_info():
    reply = b"\x01\x46\x00\x00\x00\x32N9,01,HS,SQ,OMEN-LCD-CAP,V3.05.024,0.1\x00\x00"
    assert "OMEN-LCD-CAP,V3.05.024" in p.parse_info(reply)
