# SPDX-License-Identifier: GPL-3.0-or-later
"""What the hub needs from a device (the OMEN PC, the keyboard, ...).

A device is one product with lights. The hub (core/hub.py) owns the
settings, auto mode, sleep and wake and the status report; it tells each
device what happened through the calls below. A device reads its settings
through the hub (hub.get), never from the store directly, so that the hub
can show the Default look when it stops.

To add a device: write a class with these calls in ../devices/, and have
each platform add it to the hub's list. Nothing in the hub changes.
"""

from typing import Protocol


class Device(Protocol):
    id: str  # also the key in per-device settings, e.g. "OMEN-PC"
    label: str  # what a person reads

    def attach(self, hub) -> None:
        """Called once, before start(): the hub this device belongs to."""

    def start(self) -> None:
        """Apply the current settings and start any timers of its own."""

    def stop(self) -> None:
        """Cancel the device's timers. The hardware keeps what it shows."""

    def on_changed(self, key: str) -> None:
        """One setting changed. Ignore what is not for this device."""

    def on_look_changed(self) -> None:
        """Auto mode wants something else shown (see hub.look, hub.auto_for)."""

    def on_sleep(self) -> None:
        """The computer is about to sleep (hub.asleep is now True)."""

    def on_resume(self) -> None:
        """The computer woke; the hardware may have forgotten everything."""

    def tick_slow(self) -> None:
        """Every few seconds: reconnect what went away."""

    def show_default(self) -> None:
        """The hub is stopping and hub.get now returns the Default look:
        show it at once, wait until it has really been sent, and ask the
        hardware to keep it where it can."""

    def status(self) -> dict:
        """This device for the status report: "connected" (bool) and
        whatever else its settings page needs."""
