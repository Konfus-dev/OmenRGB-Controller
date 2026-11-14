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

OpenRGB runs any detector its config does not name, so a newer OpenRGB with
new detectors could probe the RAM after all. The config is therefore tied to
the OpenRGB program it was made with:

    ... --record-program <config folder>   remember the installed OpenRGB (the installer does this)
    ... --check-program <config folder>    fail if OpenRGB has changed since (run before each start)
"""

import copy
import json
import shutil
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
PROGRAM_STAMP = "openrgb-program"


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


def program_fingerprint(program: str | None = None) -> str:
    """Identifies the installed OpenRGB without running it (running it probes hardware)."""
    path = program or shutil.which("openrgb")
    if not path:
        return "not installed"
    info = Path(path).resolve().stat()
    return f"{info.st_size}-{info.st_mtime_ns}"


def record_program(folder: Path, program: str | None = None) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / PROGRAM_STAMP).write_text(program_fingerprint(program) + "\n")


def program_unchanged(folder: Path, program: str | None = None) -> bool:
    """True if OpenRGB is still the one the detector list was made for."""
    try:
        return (folder / PROGRAM_STAMP).read_text().strip() == program_fingerprint(program)
    except OSError:
        return False  # never recorded: the installer has not finished


if __name__ == "__main__":
    if sys.argv[1] == "--record-program":
        record_program(Path(sys.argv[2]))
    elif sys.argv[1] == "--check-program":
        if not program_unchanged(Path(sys.argv[2])):
            sys.exit(
                "OpenRGB has been updated since OMEN Lights was set up. Not starting it: a new "
                "version may probe the RAM. Run install/install-user.sh again to re-check it."
            )
    else:
        known = write_config(Path(sys.argv[1]), DETECTORS, [Path(p) for p in sys.argv[2:]])
        print(known)
