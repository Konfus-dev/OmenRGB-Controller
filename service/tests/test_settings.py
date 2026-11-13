# SPDX-License-Identifier: GPL-3.0-or-later
import pytest

from omenrgb.settings import load_settings


def test_defaults(settings):
    assert settings.get_boolean("lights-enabled") is True
    assert settings.get_int("lights-brightness") == 100
    assert settings.get_boolean("lcd-enabled") is True
    assert settings.get_int("lcd-brightness") == 80
    assert settings.get_string("lcd-image") == ""
    assert settings.get_string("lcd-fit") == "fill"
    assert settings.get_strv("lcd-recent-images") == []
    assert settings.get_string("status") == "{}"
    assert settings.get_value("device-modes").unpack() == {}
    assert settings.get_value("zone-colours").unpack() == {}


def test_missing_compiled_schema(tmp_path):
    with pytest.raises(FileNotFoundError, match="install-user.sh"):
        load_settings(tmp_path)
