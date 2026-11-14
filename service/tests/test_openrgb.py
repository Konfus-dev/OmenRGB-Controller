# SPDX-License-Identifier: GPL-3.0-or-later
import json

from omenrgb.backends.openrgb import restrict_detectors, write_config


def test_only_wanted_detectors_stay_on():
    config = {
        "Detectors": {"detectors": {"HP Omen 30L": True, "Kingston Fury DDR5 DRAM": True}},
        "Other": {"keep": "me"},
    }
    result = restrict_detectors(config, ["HP Omen 30L"])
    assert result["Detectors"]["detectors"] == {
        "HP Omen 30L": True,
        "Kingston Fury DDR5 DRAM": False,
    }
    assert result["Other"] == {"keep": "me"}


def test_wanted_detector_is_added_when_openrgb_has_not_listed_it_yet():
    result = restrict_detectors({}, ["HP Omen 30L"])
    assert result["Detectors"]["detectors"] == {"HP Omen 30L": True}


def test_input_is_not_modified():
    config = {"Detectors": {"detectors": {"X": True}}}
    restrict_detectors(config, [])
    assert config["Detectors"]["detectors"] == {"X": True}


def test_write_config_creates_and_updates_file(tmp_path):
    folder = tmp_path / "openrgb"
    write_config(folder, ["HP Omen 30L"])
    path = folder / "OpenRGB.json"
    assert json.loads(path.read_text())["Detectors"]["detectors"] == {"HP Omen 30L": True}

    # OpenRGB later adds every detector it knows, switched on; we switch them off again.
    path.write_text(json.dumps({"Detectors": {"detectors": {"HP Omen 30L": True, "ENE": True}}}))
    write_config(folder, ["HP Omen 30L"])
    assert json.loads(path.read_text())["Detectors"]["detectors"] == {
        "HP Omen 30L": True,
        "ENE": False,
    }


def test_write_config_survives_a_corrupt_file(tmp_path):
    (tmp_path / "OpenRGB.json").write_text("{ not json")
    write_config(tmp_path, ["HP Omen 30L"])
    assert json.loads((tmp_path / "OpenRGB.json").read_text())["Detectors"]["detectors"] == {
        "HP Omen 30L": True
    }


def test_seed_file_supplies_detector_names_to_switch_off(tmp_path):
    # Detectors OpenRGB has not listed in our folder yet would run once at
    # its next start. Names learned from another OpenRGB config prevent that.
    seed = tmp_path / "global.json"
    seed.write_text(json.dumps({"Detectors": {"detectors": {"ENE": True, "HP Omen 30L": True}}}))
    folder = tmp_path / "ours"
    count = write_config(folder, ["HP Omen 30L"], seeds=[seed, tmp_path / "missing.json"])
    assert json.loads((folder / "OpenRGB.json").read_text())["Detectors"]["detectors"] == {
        "HP Omen 30L": True,
        "ENE": False,
    }
    assert count == 2  # how many detectors are now known
