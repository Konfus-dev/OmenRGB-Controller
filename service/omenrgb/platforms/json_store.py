# SPDX-License-Identifier: GPL-3.0-or-later
"""Settings kept in a plain JSON file.

For systems without a settings service of their own (Batocera now; a good
fit for Windows later). The file holds only what differs from the defaults,
so it stays short and easy to edit by hand:

    {
        "lights-brightness": 60,
        "auto-idle-colour": "#00AA00"
    }

Fits core.interfaces.Settings.
"""

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..core.settings_schema import DEFAULTS

log = logging.getLogger(__name__)

# These change all the time and are not the owner's choices, so they live in
# memory only and never touch the disk.
VOLATILE = ("status", "focused-app")


class JsonSettings:
    def __init__(self, path: Path, defaults: dict[str, Any] | None = None):
        """`defaults` overrides the standard defaults for this platform."""
        self._path = Path(path)
        self._defaults = {**DEFAULTS, **(defaults or {})}
        self._saved: dict[str, Any] = self._read()  # what the file says
        self._volatile: dict[str, Any] = {}
        self._subscribers: list[Callable[[str], None]] = []

    def get(self, key: str) -> Any:
        if key in self._volatile:
            return self._volatile[key]
        return self._saved.get(key, self._defaults[key])

    def set(self, key: str, value: Any) -> None:
        if value == self.get(key):
            return
        if key in VOLATILE:
            self._volatile[key] = value
        else:
            self._saved[key] = value
            self._write()
        self._tell(key)

    def subscribe(self, callback: Callable[[str], None]) -> None:
        self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[str], None]) -> None:
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    def reload(self) -> None:
        """Read the file again (after someone edited it) and report changes."""
        before = {key: self.get(key) for key in self._defaults}
        self._saved = self._read()
        for key in self._defaults:
            if self.get(key) != before[key]:
                self._tell(key)

    def _tell(self, key: str) -> None:
        for callback in list(self._subscribers):
            callback(key)

    def _read(self) -> dict[str, Any]:
        """The file's contents, keeping only known keys with sensible values."""
        try:
            data = json.loads(self._path.read_text())
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as error:
            log.warning("Ignoring %s: %s", self._path, error)
            return {}
        if not isinstance(data, dict):
            log.warning("Ignoring %s: it should contain one { ... } object", self._path)
            return {}
        good = {}
        for key, value in data.items():
            if key in VOLATILE or key not in self._defaults:
                continue
            if type(value) is not type(self._defaults[key]):
                log.warning("Ignoring '%s' in %s: wrong kind of value", key, self._path)
                continue
            good[key] = value
        return good

    def _write(self) -> None:
        changed = {k: v for k, v in self._saved.items() if v != self._defaults[k]}
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(".tmp")
            temporary.write_text(json.dumps(changed, indent=4, sort_keys=True) + "\n")
            os.replace(temporary, self._path)  # never leaves a half-written file
        except OSError as error:
            log.warning("Could not save %s: %s", self._path, error)
