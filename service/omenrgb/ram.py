# SPDX-License-Identifier: GPL-3.0-or-later
"""RAM stick lighting (Kingston Fury DDR5, as fitted by HP).

The sticks are never spoken to directly: on the OMEN 45L that jams the
RAM's control bus until the next reboot. Every command goes to the small
root helper (helper/omenrgb_ram_helper.py), which asks the BIOS to deliver
it, the way HP's own software does.

Register meanings come from HP's OMEN Gaming Hub and from OpenRGB's
KingstonFuryDRAMController.
"""

import logging
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .lights import DEFAULT_COLOUR, DeviceInfo, EffectOptions, scale_colour, zone_key

log = logging.getLogger(__name__)

HELPER_SOCKET = "/run/omenrgb-ram.sock"
# The helper normally answers within a tenth of a second. Waiting longer
# would hold up the rest of the service, which has an LCD to keep fed.
HELPER_TIMEOUT_S = 1.0
RETRY_AFTER_FAILURE_S = 30  # leave a failing helper alone for this long
I2C_DEVICES = Path("/sys/bus/i2c/devices")
SPD_DRIVER_NAME = "spd5118"  # the kernel's name for a DDR5 stick's info chip

DEVICE_NAME = "RAM"
MODES = ("Static", "Rainbow", "Breathing")
DEFAULT_MODE = "Rainbow"  # what the sticks do out of the box
COLOURLESS_MODES = ("Rainbow",)  # effects that ignore the per-stick colours
SPEED_MODES = ("Rainbow", "Breathing")  # effects that move
RANGE_MODES = ("Breathing",)  # effects that pulse between two brightnesses

# --- registers -----------------------------------------------------------------
REG_TRANSFER = 0x08  # every change is wrapped in begin ... end
TRANSFER_BEGIN, TRANSFER_END = 0x53, 0x44
REG_EFFECT = 0x09
EFFECT_STATIC, EFFECT_RAINBOW, EFFECT_BREATHING = 0x00, 0x01, 0x03
REG_STICK_POSITION = 0x0B  # lets an effect flow from one stick to the next
REG_DIRECTION = 0x0C
DIRECTION_UP = 0x01
REG_DELAY = 0x0D
REG_SPEED = 0x0E
REG_BREATH_TIMES = (0x16, 0x17, 0x18, 0x19)  # dim->mid, mid->bright, bright->mid, mid->dim
REG_BREATH_HOLD_DIM = 0x1A
REG_BREATH_LEVELS = (0x1B, 0x1C, 0x1D)  # bright, mid, dim (percent)
REG_BRIGHTNESS = 0x20  # 0..100
REG_STICK_COUNT = 0x27
REG_COLOUR_COUNT = 0x30
REG_COLOUR = (0x31, 0x32, 0x33)  # red, green, blue of the first effect colour

# --- effect tuning (change these to taste) -------------------------------------
# The speed slider runs from "slowest" (0 %) to "fastest" (100 %).
RAINBOW_SPEED_SLOWEST, RAINBOW_SPEED_FASTEST = 50, 0  # register value; smaller is faster
BREATH_STEP_SLOWEST, BREATH_STEP_FASTEST = 9, 1  # length of one fade step; smaller is faster
BREATH_HOLD_DIM = 1
BREATH_MID_POINT = 0.64  # where the "mid" level sits between dimmest and brightest

Command = tuple[int, int, int]  # (stick address, register, value)


def find_sticks(i2c_devices: Path = I2C_DEVICES) -> list[int]:
    """Addresses of the sticks that are fitted, lowest slot first.

    A stick in slot N has its info chip at 0x50 + N and is addressed for
    lighting as 0xC0 + 2 * N.
    """
    sticks = []
    for slot in range(4):
        for node in i2c_devices.glob(f"*-00{0x50 + slot:02x}"):
            try:
                if (node / "name").read_text().strip() == SPD_DRIVER_NAME:
                    sticks.append(0xC0 + 2 * slot)
                    break
            except OSError:
                continue
    return sticks


def _between(slowest: int, fastest: int, percent: int) -> int:
    return round(slowest + (fastest - slowest) * _percent(percent) / 100)


def _percent(value: int) -> int:
    return max(0, min(100, value))


def commands(
    mode: str,
    brightness: int,
    colours: list[tuple[int, int, int]],
    sticks: list[int],
    speed: int = 50,
    min_brightness: int = 0,
    max_brightness: int = 100,
) -> list[Command]:
    """The register writes that put `sticks` into `mode`.

    `colours` holds one (red, green, blue) per stick; missing ones are white.
    `mode` is one of MODES or "Off"; anything else means "Static".
    `speed`, `min_brightness` and `max_brightness` are percentages and only
    matter for the effects listed in SPEED_MODES and RANGE_MODES.
    """
    if not sticks:
        return []
    brightness = _percent(brightness)
    colours = [
        tuple(max(0, min(255, part)) for part in (colours[i] if i < len(colours) else (255,) * 3))
        for i in range(len(sticks))
    ]
    result: list[Command] = []

    def everywhere(register: int, value: int) -> None:
        result.extend((stick, register, value) for stick in sticks)

    def colour_per_stick() -> None:
        everywhere(REG_COLOUR_COUNT, 1)
        for part, register in enumerate(REG_COLOUR):
            result.extend((stick, register, colours[i][part]) for i, stick in enumerate(sticks))

    def stick_positions() -> None:
        # HP numbers the sticks from the far end, so effects flow across them.
        result.extend(
            (stick, REG_STICK_POSITION, len(sticks) - 1 - i) for i, stick in enumerate(sticks)
        )
        everywhere(REG_STICK_COUNT, len(sticks))

    everywhere(REG_TRANSFER, TRANSFER_BEGIN)
    if mode == "Off":
        everywhere(REG_BRIGHTNESS, 0)
    elif mode == "Rainbow":
        everywhere(REG_EFFECT, EFFECT_RAINBOW)
        stick_positions()
        everywhere(REG_DIRECTION, DIRECTION_UP)
        everywhere(REG_DELAY, 0)
        everywhere(REG_SPEED, _between(RAINBOW_SPEED_SLOWEST, RAINBOW_SPEED_FASTEST, speed))
        everywhere(REG_BRIGHTNESS, brightness)
    elif mode == "Breathing":
        everywhere(REG_EFFECT, EFFECT_BREATHING)
        stick_positions()
        everywhere(REG_DIRECTION, DIRECTION_UP)
        everywhere(REG_DELAY, 0)
        everywhere(REG_SPEED, 0)
        # Fading between dim and mid takes three steps, mid and bright one.
        step = _between(BREATH_STEP_SLOWEST, BREATH_STEP_FASTEST, speed)
        for register, value in zip(REG_BREATH_TIMES, (3 * step, step, step, 3 * step), strict=True):
            everywhere(register, value)
        everywhere(REG_BREATH_HOLD_DIM, BREATH_HOLD_DIM)
        brightest = _percent(max_brightness)
        dimmest = min(_percent(min_brightness), brightest)
        mid = round(dimmest + (brightest - dimmest) * BREATH_MID_POINT)
        for register, value in zip(REG_BREATH_LEVELS, (brightest, mid, dimmest), strict=True):
            everywhere(register, value)
        everywhere(REG_BRIGHTNESS, brightness)
        colour_per_stick()
    else:  # Static: exactly the sequence HP's software sends
        everywhere(REG_EFFECT, EFFECT_STATIC)
        everywhere(REG_BRIGHTNESS, brightness)
        colour_per_stick()
    everywhere(REG_TRANSFER, TRANSFER_END)
    return result


def send_to_helper(batch: list[Command], path: str = HELPER_SOCKET) -> None:
    """Send commands to the root helper. Raises OSError if anything fails.

    An empty batch just checks that the helper answers.
    """
    lines = [f"W {a:02x} {r:02x} {v:02x}" for a, r, v in batch] or ["PING"]
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(HELPER_TIMEOUT_S)
        connection.connect(path)
        with connection.makefile("rw", encoding="ascii") as stream:
            for line in lines:
                stream.write(line + "\n")
                stream.flush()
                reply = stream.readline().strip()
                if reply != "OK":
                    raise OSError(f"RAM helper refused '{line}': {reply or 'no answer'}")


class RamLights:
    """The RAM sticks, presented like any other lighting device.

    Every write goes through the BIOS and takes about 65 ms, so two things
    keep it quick: only registers whose value has changed are sent, and with
    background=True the sending happens on a worker thread, where it cannot
    hold up the LCD or the case lights. If requests arrive faster than they
    can be sent, the newest one wins.
    """

    def __init__(
        self,
        send: Callable[[list[Command]], None] = send_to_helper,
        find: Callable[[], list[int]] = find_sticks,
        gamma: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        background: bool = False,
    ):
        self._send = send
        self._find = find
        self._gamma = gamma  # see lights.scale_colour
        self._clock = clock
        self._retry_at = 0.0  # do not contact the helper before this time
        self._sticks: list[int] = []

        # What we believe each stick's registers hold: {stick: {register: value}}.
        self._registers: dict[int, dict[int, int]] = {}
        self._epoch = 0  # bumped whenever that belief is thrown away

        # Background sending. The lock guards everything below it and the
        # two fields above; the worker thread is started on first use.
        self._background = background
        self._lock = threading.Lock()
        self._wanted: list[Command] | None = None  # newest request not yet sent
        self._wake = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._failed = False  # set by the worker, handled by check()
        self._worker: threading.Thread | None = None

    @property
    def connected(self) -> bool:
        return bool(self._sticks)

    def try_connect(self) -> bool:
        """True once there are sticks and the root helper answers."""
        if self._sticks:
            return True
        if self._clock() < self._retry_at:
            return False
        sticks = self._find()
        if not sticks:
            return False
        try:
            self._send([])
        except OSError as error:
            log.debug("RAM helper not available: %s", error)
            self._back_off()
            return False
        self._sticks = sticks
        self.forget()
        log.info("RAM lights connected, %d stick(s)", len(sticks))
        return True

    def _back_off(self) -> None:
        self._retry_at = self._clock() + RETRY_AFTER_FAILURE_S

    def forget(self) -> None:
        """Send everything again on the next apply, even if nothing changed.

        Used after sleep, when the sticks may have lost their settings.
        """
        with self._lock:
            self._registers = {}
            self._epoch += 1

    def check(self) -> bool:
        """False once a send has failed; the caller then reconnects later."""
        if self._failed:
            self._failed = False
            self._disconnect()
        return self.connected

    def _disconnect(self) -> None:
        self._sticks = []  # reconnect and resend later
        self.forget()
        self._back_off()

    def wait_idle(self, timeout_s: float) -> bool:
        """Wait until nothing is waiting to be sent (used before sleep)."""
        return self._idle.wait(timeout_s)

    def devices(self) -> list[DeviceInfo]:
        if not self._sticks:
            return []
        zones = tuple(f"Stick {i + 1}" for i in range(len(self._sticks)))
        return [
            DeviceInfo(
                DEVICE_NAME,
                MODES,
                zones,
                default_mode=DEFAULT_MODE,
                colourless_modes=COLOURLESS_MODES,
                speed_modes=SPEED_MODES,
                range_modes=RANGE_MODES,
            )
        ]

    def apply(
        self,
        enabled: bool,
        brightness: int,
        modes: dict[str, str],
        colours: dict[str, str],
        options: dict[str, EffectOptions] | None = None,
    ) -> None:
        """Same call as Lights.apply, so the service treats both alike."""
        if not self._sticks:
            return
        mode = modes.get(DEVICE_NAME, DEFAULT_MODE) if enabled else "Off"
        per_stick = [
            scale_colour(
                colours.get(zone_key(DEVICE_NAME, f"Stick {i + 1}"), DEFAULT_COLOUR),
                100,
                self._gamma,
            )
            for i in range(len(self._sticks))
        ]
        extra = (options or {}).get(DEVICE_NAME, EffectOptions())
        wanted = commands(
            mode,
            brightness,
            [] if mode == "Off" else per_stick,
            self._sticks,
            speed=extra.speed,
            min_brightness=extra.min_brightness,
            max_brightness=extra.max_brightness,
        )
        if not self._background:
            if not self._send_changes(wanted):
                self._disconnect()
            return
        with self._lock:
            self._wanted = wanted
            self._idle.clear()
        if self._worker is None:
            self._worker = threading.Thread(target=self._work, name="ram-lights", daemon=True)
            self._worker.start()
        self._wake.set()

    def _work(self) -> None:
        """Worker thread: send the newest request, then wait for the next."""
        while True:
            self._wake.wait()
            self._wake.clear()
            while True:
                with self._lock:
                    wanted, self._wanted = self._wanted, None
                    if wanted is None:
                        self._idle.set()
                        break
                if not self._send_changes(wanted):
                    self._failed = True  # check() will disconnect and back off

    def _send_changes(self, wanted: list[Command]) -> bool:
        """Send the writes in `wanted` that differ from what the sticks hold.

        Returns False if sending failed.
        """
        with self._lock:
            epoch = self._epoch
            changes = [
                (stick, register, value)
                for stick, register, value in wanted
                if register != REG_TRANSFER
                and self._registers.get(stick, {}).get(register) != value
            ]
        if not changes:
            return True  # nothing changed; do not bother the BIOS
        begin = [(stick, REG_TRANSFER, TRANSFER_BEGIN) for stick in self._sticks]
        end = [(stick, REG_TRANSFER, TRANSFER_END) for stick in self._sticks]
        try:
            self._send(begin + changes + end)
        except OSError as error:
            log.warning("Could not set RAM lights: %s", error)
            return False
        with self._lock:
            if epoch == self._epoch:  # nobody called forget() meanwhile
                for stick, register, value in changes:
                    self._registers.setdefault(stick, {})[register] = value
        return True
