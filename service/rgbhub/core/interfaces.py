# SPDX-License-Identifier: GPL-3.0-or-later
"""The seams between the core and everything system-specific.

The core (this folder) decides what the LCD and lights should show. It never
imports an operating system's or a hardware library's modules; it is handed
objects that fit the shapes described here. A platform (../platforms) picks
the implementations for one system. To support a new system, write that one
file; nothing in the core changes.

Shapes described elsewhere, next to the code that uses them:
- a lighting client (core/lights.py): `devices`, `update()`, `disconnect()`;
  each device has `name`, `modes`, `zones`, `set_mode()`, `set_colors()`.
  OpenRGB's client fits as it is; backends/tracer.py is a second one.
- an LCD port (core/lcd_streamer.py): `write(bytes)` and `close()`.
- a RAM sender (core/ram.py): a function taking a list of register writes.
"""

from collections.abc import Callable
from typing import Any, Protocol

# watch_file(path, on_change) starts watching one file and returns a
# function that stops watching.
WatchFile = Callable[[str, Callable[[], None]], Callable[[], None]]


class Settings(Protocol):
    """Where the front end's choices live, and where status is reported.

    Keys and their defaults are listed in settings_schema.py. Values are
    plain Python: bool, int, str, list of str, or dict.
    """

    def get(self, key: str) -> Any: ...

    def set(self, key: str, value: Any) -> None: ...

    def subscribe(self, callback: Callable[[str], None]) -> None:
        """Call `callback(key)` whenever a setting changes, whoever changed it."""

    def unsubscribe(self, callback: Callable[[str], None]) -> None: ...


class Scheduler(Protocol):
    """Runs timers on the program's event loop."""

    def every(self, interval_ms: int, callback: Callable[[], bool]) -> Any:
        """Call `callback` every `interval_ms` until it returns False.
        Returns a handle for cancel()."""

    def cancel(self, handle: Any) -> None: ...


class ManualScheduler:
    """A Scheduler that never fires by itself. Used in tests, where each
    tick is called directly, and as the default when none is given."""

    def __init__(self):
        self.timers: dict[int, tuple[int, Callable[[], bool]]] = {}
        self._next = 1

    def every(self, interval_ms: int, callback: Callable[[], bool]) -> int:
        handle, self._next = self._next, self._next + 1
        self.timers[handle] = (interval_ms, callback)
        return handle

    def cancel(self, handle: int) -> None:
        self.timers.pop(handle, None)
