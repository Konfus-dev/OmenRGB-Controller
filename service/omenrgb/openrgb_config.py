# SPDX-License-Identifier: GPL-3.0-or-later
"""Limits OpenRGB to the hardware this project uses.

Left alone, OpenRGB probes for hundreds of kinds of device at startup,
including RAM modules. On the OMEN 45L that probing makes the RAM's control
bus stop responding until the next reboot (the RAM temperature sensors go
dead). So OpenRGB gets its own configuration folder in which every hardware
detector is switched off except the ones named in constants.OPENRGB_DETECTORS.

Run as:  python -m omenrgb.openrgb_config <config folder> [other OpenRGB.json ...]
Prints how many detectors it now knows about.
"""

import copy
import json
import sys
from collections.abc import Iterable
from pathlib import Path

from .constants import OPENRGB_DETECTORS

CONFIG_FILE = "OpenRGB.json"


def restrict_detectors(config: dict, keep: Iterable[str]) -> dict:
    """A copy of an OpenRGB config with only the `keep` detectors enabled."""
    keep = set(keep)
    result = copy.deepcopy(config)
    detectors = result.setdefault("Detectors", {}).setdefault("detectors", {})
    for name in set(detectors) | keep:
        detectors[name] = name in keep
    return result


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def write_config(folder: Path, keep: Iterable[str], seeds: Iterable[Path] = ()) -> int:
    """Create or update OpenRGB.json in `folder`.

    OpenRGB runs any detector that is not listed in its config, so the more
    names we know, the better. `seeds` are other OpenRGB config files to
    learn detector names from. Returns how many detectors are now listed.
    """
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / CONFIG_FILE
    config = _read(path)
    detectors = config.setdefault("Detectors", {}).setdefault("detectors", {})
    for seed in seeds:
        for name in _read(seed).get("Detectors", {}).get("detectors", {}):
            detectors.setdefault(name, False)
    config = restrict_detectors(config, keep)
    path.write_text(json.dumps(config, indent=4, sort_keys=True))
    return len(config["Detectors"]["detectors"])


if __name__ == "__main__":
    known = write_config(Path(sys.argv[1]), OPENRGB_DETECTORS, [Path(p) for p in sys.argv[2:]])
    print(known)
