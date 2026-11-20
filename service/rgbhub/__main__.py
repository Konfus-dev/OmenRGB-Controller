# SPDX-License-Identifier: GPL-3.0-or-later
"""Start the service for the system it is running on.

    python -m rgbhub                      (works out the platform itself)
    python -m rgbhub --platform gnome ... (or name it)

To add a system, write platforms/<name>.py with a main(argv) function and
list it in PLATFORMS below.
"""

import importlib
import sys
from pathlib import Path

PLATFORMS = ("gnome", "batocera")


def detect() -> str:
    """Batocera identifies itself with this file; everything else is
    treated as a GNOME desktop for now."""
    return "batocera" if Path("/usr/share/batocera/batocera.version").exists() else "gnome"


def main(argv: list[str]) -> int:
    name = None
    if len(argv) >= 2 and argv[0] == "--platform":
        name, argv = argv[1], argv[2:]
    name = name or detect()
    if name not in PLATFORMS:
        print(f"Unknown platform '{name}'. Known: {', '.join(PLATFORMS)}", file=sys.stderr)
        return 2
    return importlib.import_module(f"rgbhub.platforms.{name}").main(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
