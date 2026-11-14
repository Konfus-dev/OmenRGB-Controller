# SPDX-License-Identifier: GPL-3.0-or-later
"""Case lights through an OpenRGB server, and keeping that server tame.

Limits OpenRGB to the hardware this project uses.

Left alone, OpenRGB probes for hundreds of kinds of device at startup,
including RAM modules. On the OMEN 45L that probing makes the RAM's control
bus stop responding until the next reboot (the RAM temperature sensors go
dead). So OpenRGB gets its own configuration folder in which every hardware
detector is switched off except the ones named in DETECTORS below.

Run as:  python -m omenrgb.backends.openrgb <config folder> [other OpenRGB.json ...]
Prints how many detectors it now knows about.
"""

import copy
import json
import sys
from collections.abc import Iterable
from pathlib import Path

HOST = "127.0.0.1"
PORT = 6742
TIMEOUT_S = 2.0  # how long to wait for OpenRGB to answer a request

# The only OpenRGB hardware detectors left switched on.
# "HP Omen 30L" is OpenRGB's name for the 45L's case-light board.
DETECTORS = ("HP Omen 30L",)

CONFIG_FILE = "OpenRGB.json"


def connect():
    """Connect to the local OpenRGB server. Raises if it is not running.

    The returned client is what core/lights.py expects of a lighting client.
    """
    from openrgb import OpenRGBClient  # imported late: it is slow to load

    client = OpenRGBClient(HOST, PORT, "omenrgb")
    # The library waits up to 10 seconds for each answer by default, which
    # would freeze the service (and the LCD) if OpenRGB ever hung.
    client.comms.sock.settimeout(TIMEOUT_S)
    return client


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
    known = write_config(Path(sys.argv[1]), DETECTORS, [Path(p) for p in sys.argv[2:]])
    print(known)
