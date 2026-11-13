# OmenRGB-Linux-Controller Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A GNOME top-right menu entry and settings window that put custom images on the OMEN 45L cooler LCD and control fan, case and RAM lighting.

**Architecture:** A Python service (systemd user unit) is the only thing that touches hardware: it streams a JPEG to the LCD over `/dev/hidraw` and sets lights through a headless OpenRGB server. A thin GNOME Shell extension reads and writes one shared GSettings schema; the service watches it and writes back a status key.

**Tech Stack:** Python 3.14 (stdlib, `python3-gi`, Pillow, `openrgb-python` 0.3.8 from PyPI, pytest, ruff), GNOME Shell 50 extension in plain JavaScript (ESM, no build step), GSettings, systemd user units, OpenRGB 0.9 from Ubuntu's `openrgb` package.

**Spec:** `docs/superpowers/specs/2026-11-13-omen-control-design.md`

## Global Constraints

- Target machine only: OMEN 45L GT22-3xxx, board 8D2C, Ubuntu 26.04, GNOME Shell 50, Wayland.
- The LCD receives only operations `0x41`, `0x46`, `0x6c`, `0x6e`. No other operation codes, ever.
- Never write to the LCD's mouse-class HID interface. Pick the node whose report descriptor starts `06 1a ff`.
- Never talk to the RAM SMBus from our code. Lights go through OpenRGB only.
- No code copied from `Xedecimal/omen-led-lcd`. Protocol facts only.
- Nothing runs as root day to day. `sudo` appears only in `install/setup.sh`, which the owner runs.
- The extension never touches hardware and never blocks: no synchronous I/O in `extension.js`.
- One job per file. Hardware numbers live only in `service/omenrgb/constants.py`.
- Licence GPL-3.0-or-later; every source file starts with `SPDX-License-Identifier: GPL-3.0-or-later`.
- Work on `dev`. Commit after each task. Commit messages end with the Co-Authored-By line used so far.
- Deviation from the spec: no ESLint (it would add a Node toolchain). JavaScript is checked by loading it in GNOME Shell and reading the journal.

## Review Focus

1. Image file missing, unreadable or corrupt (deleted after being chosen, a text file renamed `.png`): the service keeps showing the previous image and logs one line. Test in Task 3 and Task 7.
2. Image whose JPEG is too big for the transfer (detailed 480x480 photo): quality is lowered until it fits `MAX_JPEG_BYTES`. Test in Task 3.
3. Unusual images (transparent PNG, CMYK JPEG, EXIF-rotated phone photo, animated GIF, 1x1 pixel, 8000x200 panorama): all produce a valid 480x480 RGB JPEG. Test in Task 3.
4. LCD disappears or re-enumerates under a new hidraw number (suspend/resume, USB hiccup): the service reconnects by itself within 5 s. Test in Task 5.
5. OpenRGB not running when the service starts, or it exits later: the LCD half keeps working and lights reconnect by themselves. Test in Task 6 and Task 7.

## File Structure

```
install/setup.sh                 one-time, sudo, owner runs: packages, udev rule, i2c-dev
install/70-omen-lcd.rules        udev rule for 03f0:7397
install/install-user.sh          no sudo: venv, systemd user units, extension link, schema compile
systemd/openrgb-server.service   headless OpenRGB SDK server on 127.0.0.1
systemd/omenrgb.service          our service
service/pyproject.toml
service/omenrgb/constants.py     USB IDs, sizes, offsets, intervals
service/omenrgb/lcd_protocol.py  pure functions: bytes in, bytes out
service/omenrgb/image_prep.py    any image file -> 480x480 JPEG bytes
service/omenrgb/lcd_device.py    find and open the hidraw node, read/write
service/omenrgb/lcd_streamer.py  keeps the image on screen; reconnects
service/omenrgb/lights.py        OpenRGB wrapper
service/omenrgb/settings.py      loads the GSettings schema
service/omenrgb/service.py       wires settings -> streamer and lights; GLib main loop
service/omenrgb/__main__.py      calls service.main()
service/tests/test_*.py          one per module above
extension/omenrgb@konfus-dev.github.io/metadata.json
extension/omenrgb@konfus-dev.github.io/extension.js     top-right menu entry
extension/omenrgb@konfus-dev.github.io/prefs.js         settings window
extension/omenrgb@konfus-dev.github.io/schemas/org.gnome.shell.extensions.omenrgb.gschema.xml
spike/                            throwaway scripts, deleted at the end of Task 1
```

---

### Task 0: One-time setup script

**Files:**
- Create: `install/setup.sh`, `install/70-omen-lcd.rules`

- [ ] **Step 1: Write `install/70-omen-lcd.rules`** with exactly one rule:

```
KERNEL=="hidraw*", ATTRS{idVendor}=="03f0", ATTRS{idProduct}=="7397", TAG+="uaccess"
```

- [ ] **Step 2: Write `install/setup.sh`** (`set -euo pipefail`, refuses to run unless `$EUID` is 0, prints each action before doing it). It does only this:
  1. `apt-get install -y openrgb python3-venv python3-pip`
  2. copies `70-omen-lcd.rules` to `/etc/udev/rules.d/`
  3. writes `i2c-dev` to `/etc/modules-load.d/i2c-dev.conf` and runs `modprobe i2c-dev`
  4. `udevadm control --reload && udevadm trigger`

- [ ] **Step 3: Check it** with `bash -n install/setup.sh`. Expected: no output.

- [ ] **Step 4: Commit, then hand to the owner.** Ask the owner to read the script and run `sudo bash install/setup.sh`.

- [ ] **Step 5: Verify after the owner ran it.** Run: `for h in /sys/class/hidraw/*; do grep -q 000003F0:00007397 $h/device/uevent && test -w /dev/$(basename $h) && echo ok; done; which openrgb; ls /dev/i2c-5`. Expected: `ok` twice, a path to `openrgb`, and `/dev/i2c-5`.

### Task 1: Spike on the real hardware (throwaway)

Purpose: confirm the provisional protocol facts and learn what OpenRGB sees. Nothing from `spike/` is kept. The builder cannot see the LCD or the lights; every visual check is a question to the owner.

**Files:**
- Create (temporary): `spike/lcd_probe.py`, `spike/lights_probe.sh`
- Modify: the spec, section "Hardware facts", with results

- [ ] **Step 1: LCD read-only probe.** `spike/lcd_probe.py info` sends the 64-byte report `01 46 00…` to the vendor hidraw node and prints the reply as text. Expected: contains `OMEN-LCD-CAP`. If not, stop and return to the owner.
- [ ] **Step 2: Claim.** Send `01 41 00 00 00 01 00…` (64 bytes). Expected reply: byte[1]=`0x41`, byte[5]=`0x07`.
- [ ] **Step 3: One image.** Generate a 480x480 JPEG with the text "HELLO" (Pillow). Loop for 60 s: claim every 1 s, the frame every 500 ms, using the framing in Task 2. Ask the owner: did HELLO show, and did it stay without flicker?
- [ ] **Step 4: Find the slowest rate that holds.** Repeat with frame intervals 1000 ms, 2000 ms, 5000 ms, 30 s each. Record the slowest interval at which the owner sees no return to the logo; `FRAME_INTERVAL_MS` in Task 2 becomes half of it, capped at 500.
- [ ] **Step 5: Brightness.** Send brightness 0, 10, 50, 100 (Task 2 layout), 5 s apart, while streaming. Ask the owner what they saw. Record whether mid values differ visibly.
- [ ] **Step 6: Cost.** While streaming at the chosen rate, run `top -b -n 3 -p <pid>`. If above 5% of one core, stop and return to the owner.
- [ ] **Step 7: OpenRGB.** Run `openrgb --list-devices`. Record every device name, its zones and its mode names in the spec. Then for each device: `openrgb --device <n> --mode static --color FF0000` and ask the owner which lights turned red.
- [ ] **Step 8: OpenRGB server and Python library.** Run `openrgb --server --server-host 127.0.0.1` in the background; in a scratch venv `pip install openrgb-python==0.3.8` and run a five-line script that connects, prints `[d.name for d in client.devices]` and each device's `[m.name for m in d.modes]`, sets one device green with `set_color`. If the flag or the library call differs from this plan, record the working form in the spec and use it in Tasks 6 and 7.
- [ ] **Step 9: Gate.** Apply the spec's limits: no lights at all, or no LCD image after varying chunk size, timing and header fields → stop and return to the owner. Partial lights → continue and tell the owner what is missing.
- [ ] **Step 10: Write results into the spec, delete `spike/`, commit.**

### Task 2: Project skeleton, constants, LCD protocol

**Files:**
- Create: `service/pyproject.toml`, `service/omenrgb/__init__.py`, `service/omenrgb/constants.py`, `service/omenrgb/lcd_protocol.py`
- Test: `service/tests/test_lcd_protocol.py`

**Interfaces — Produces:**
- `constants.py`: `LCD_HID_ID = "0003:000003F0:00007397"`, `LCD_DESCRIPTOR_PREFIX = bytes.fromhex("061aff")`, `SHORT_REPORT_LEN = 64`, `LONG_REPORT_LEN = 1024`, `LONG_REPLY_LEN = 512`, `CHUNK_DATA_LEN = 1013`, `CHUNK_DATA_OFFSET = 11`, `MAX_JPEG_BYTES = 60000`, `LCD_SIZE = 480`, `CLAIM_INTERVAL_MS = 1000`, `FRAME_INTERVAL_MS = 500` (or the Task 1 result), `RECONNECT_INTERVAL_S = 5`, `OPENRGB_HOST = "127.0.0.1"`, `OPENRGB_PORT = 6742`, `SCHEMA_ID = "org.gnome.shell.extensions.omenrgb"`, `EXTENSION_UUID = "omenrgb@konfus-dev.github.io"`.
- `lcd_protocol.py`:
  - `claim_report() -> bytes`
  - `info_report() -> bytes`
  - `brightness_report(percent: int) -> bytes`
  - `frame_reports(jpeg: bytes) -> list[bytes]` (raises `ValueError` if empty or longer than `MAX_JPEG_BYTES`)
  - `is_claim_ack(reply: bytes) -> bool`
  - `parse_info(reply: bytes) -> str`

`pyproject.toml`: package `omenrgb`, `requires-python >= 3.12`, dependencies `openrgb-python==0.3.8`, optional group `dev = ["pytest", "ruff"]`, ruff line length 100, pytest `testpaths = ["tests"]`. Pillow and PyGObject come from the system (venv uses `--system-site-packages`), so they are not listed.

- [ ] **Step 1: Create a dev venv.** `python3 -m venv --system-site-packages .venv && .venv/bin/pip install -e 'service[dev]'`. (`.venv/` is already git-ignored.)

- [ ] **Step 2: Write the failing tests**

```python
from omenrgb import lcd_protocol as p

def test_claim_report():
    r = p.claim_report()
    assert len(r) == 64 and r[:2] == b"\x01\x41" and r[5] == 1
    assert sum(r) == 0x01 + 0x41 + 1          # everything else zero

def test_info_report():
    r = p.info_report()
    assert len(r) == 64 and r[:2] == b"\x01\x46" and sum(r) == 0x01 + 0x46

def test_brightness_report_matches_captured_head():
    r = p.brightness_report(0)
    assert len(r) == 1024
    assert r[:18].hex() == "026c00000001000001000804000000000019"
    assert p.brightness_report(57)[12] == 57

def test_brightness_is_clamped():
    assert p.brightness_report(-5)[12] == 0
    assert p.brightness_report(250)[12] == 100

def test_frame_reports_single_chunk():
    [r] = p.frame_reports(b"\xff\xd8" + b"A" * 98)
    assert len(r) == 1024 and r[:2] == b"\x02\x6e"
    assert r[4:6] == (100).to_bytes(2, "big")
    assert r[8] == 0 and r[9:11] == (100).to_bytes(2, "big")
    assert r[11:111] == b"\xff\xd8" + b"A" * 98 and r[111:] == bytes(913)

def test_frame_reports_multi_chunk_roundtrip():
    jpeg = bytes(range(256)) * 10            # 2560 bytes -> 3 chunks
    reports = p.frame_reports(jpeg)
    assert [r[8] for r in reports] == [0, 1, 2]
    assert [int.from_bytes(r[9:11], "big") for r in reports] == [1013, 1013, 534]
    assert all(r[4:6] == (2560).to_bytes(2, "big") for r in reports)
    assert b"".join(r[11:11 + int.from_bytes(r[9:11], "big")] for r in reports) == jpeg

def test_frame_reports_rejects_bad_sizes():
    import pytest
    with pytest.raises(ValueError): p.frame_reports(b"")
    with pytest.raises(ValueError): p.frame_reports(bytes(60001))

def test_claim_ack_and_info():
    ack = bytearray(64); ack[0:2] = b"\x01\x41"; ack[5] = 7
    assert p.is_claim_ack(bytes(ack)) and not p.is_claim_ack(p.claim_report())
    assert not p.is_claim_ack(b"")
    assert "OMEN-LCD-CAP" in p.parse_info(b"\x01\x46\x00N9,01,HS,SQ,OMEN-LCD-CAP,V3.05.019\x00\x00")
```

- [ ] **Step 3: Run** `.venv/bin/pytest service -q`. Expected: fails on import.
- [ ] **Step 4: Implement** `constants.py` and `lcd_protocol.py`. `parse_info` keeps printable ASCII only.
- [ ] **Step 5: Run** `.venv/bin/pytest service -q && .venv/bin/ruff check service`. Expected: all pass, no findings.
- [ ] **Step 6: Commit.**

### Task 3: Image preparation

**Files:**
- Create: `service/omenrgb/image_prep.py`
- Test: `service/tests/test_image_prep.py`

**Interfaces — Produces:**
- `class ImageError(Exception)`
- `prepare_jpeg(path: Path, fit: str) -> bytes`; `fit` is `"fill"` (centre crop) or `"fit"` (letterbox on black); any other value behaves as `"fill"`. Raises `ImageError` for a missing, unreadable or non-image file.

Approach: Pillow. `ImageOps.exif_transpose`, first frame only, alpha composited onto black, convert to RGB, `ImageOps.fit` or `ImageOps.pad` to 480x480, save baseline JPEG starting at quality 90 and stepping down by 10 to a floor of 30 until the result is at most `MAX_JPEG_BYTES`.

- [ ] **Step 1: Write the failing tests** (each builds its input with Pillow in `tmp_path`, calls `prepare_jpeg`, reopens the result):

```python
def check(data):                       # helper used by every test
    im = Image.open(io.BytesIO(data))
    assert im.format == "JPEG" and im.size == (480, 480) and im.mode == "RGB"
    assert len(data) <= 60000
    return im

test_plain_png_fill            # 1000x500 red PNG, fit="fill" -> check(); centre pixel is red
test_fit_letterboxes           # 1000x500 red PNG, fit="fit" -> pixel (240,10) is black, (240,240) is red
test_transparent_png_on_black  # RGBA fully transparent -> centre pixel is black
test_cmyk_jpeg                 # mode "CMYK" JPEG -> check()
test_exif_rotation             # 200x100 with EXIF orientation 6, left half white, fit="fit" -> top half white
test_animated_gif_first_frame  # 2-frame GIF, red then blue -> centre pixel is red
test_tiny_and_panorama         # 1x1 and 8000x200 -> check() for both, both fit values
test_noisy_image_is_shrunk     # 480x480 of os.urandom pixels -> check() (forces the quality loop)
test_unknown_fit_means_fill    # fit="banana" equals output of fit="fill"
test_missing_file              # raises ImageError
test_not_an_image              # text file named x.png raises ImageError
```

Colour assertions allow ±12 per channel for JPEG loss.

- [ ] **Step 2: Run** `.venv/bin/pytest service/tests/test_image_prep.py -q`. Expected: fails on import.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the tests and ruff. Expected: pass.
- [ ] **Step 5: Commit.**

### Task 4: LCD device access

**Files:**
- Create: `service/omenrgb/lcd_device.py`
- Test: `service/tests/test_lcd_device.py`

**Interfaces:**
- Consumes: `LCD_HID_ID`, `LCD_DESCRIPTOR_PREFIX`, `LONG_REPLY_LEN` from `constants`.
- Produces:
  - `find_hidraw(sys_root: Path = Path("/sys/class/hidraw"), dev_root: Path = Path("/dev")) -> Path | None`
  - `class LcdDevice` with `__init__(self, path: Path)` (opens read/write, non-blocking), `write(self, report: bytes) -> None`, `request(self, report: bytes, timeout_s: float = 0.5) -> bytes | None` (drain pending input, write, wait with `select`, return one reply or `None`), `close(self) -> None`. I/O failures raise `OSError`.

- [ ] **Step 1: Write the failing tests.** Build a fake sysfs in `tmp_path`: `hidraw2/device/uevent` with `HID_ID=0003:000003F0:00007397` and `report_descriptor` starting `05 01` (the mouse interface); `hidraw8` with the same ID and descriptor starting `06 1a ff`; `hidraw4` with `HID_ID=0003:0000103C:000084FD`.

```python
def test_finds_vendor_interface_not_mouse():   assert find_hidraw(sys, dev) == dev / "hidraw8"
def test_none_when_absent():                   # remove hidraw8 -> None
def test_ignores_unreadable_entries():         # an entry with no uevent file does not raise
def test_write_and_request_over_pipe_pair():   # LcdDevice on a socketpair-backed fd: write() delivers bytes;
                                               # request() returns the peer's reply; returns None on silence
```

For the last test give `LcdDevice` a classmethod `from_fd(fd: int) -> LcdDevice` so tests can pass one end of `socket.socketpair()`.

- [ ] **Step 2: Run.** Expected: fails on import.
- [ ] **Step 3: Implement** with `os.open`, `os.write`, `os.read`, `select.select`.
- [ ] **Step 4: Run** tests and ruff. Expected: pass.
- [ ] **Step 5: Hardware check.** `.venv/bin/python -c "from omenrgb import lcd_device as d, lcd_protocol as p; x=d.LcdDevice(d.find_hidraw()); print(p.parse_info(x.request(p.info_report())))"`. Expected: a line containing `OMEN-LCD-CAP`.
- [ ] **Step 6: Commit.**

### Task 5: LCD streamer

**Files:**
- Create: `service/omenrgb/lcd_streamer.py`
- Test: `service/tests/test_lcd_streamer.py`

**Interfaces:**
- Consumes: `lcd_protocol` functions; an object with `write(bytes)`, `close()`.
- Produces: `class LcdStreamer`:
  - `__init__(self, open_device: Callable[[], object | None])` — returns a device or `None` if not found
  - `connected: bool` (property)
  - `set_image(self, jpeg: bytes | None) -> None`
  - `set_brightness(self, percent: int) -> None`
  - `tick_claim(self) -> None` — called every `CLAIM_INTERVAL_MS`
  - `tick_frame(self) -> None` — called every `FRAME_INTERVAL_MS`
  - `tick_reconnect(self) -> None` — called every `RECONNECT_INTERVAL_S`

Behaviour: on connect (first or after loss) send claim, then brightness, then frames. `tick_frame` sends nothing when there is no image. A brightness change is sent once, on the next `tick_claim`. Any `OSError` from the device closes it, sets `connected` to `False`, and never propagates. `tick_reconnect` does nothing while connected.

- [ ] **Step 1: Write the failing tests** with a `FakeDevice` that records writes and can be told to raise:

```python
test_connect_sends_claim_then_brightness      # first two writes: claim_report(), brightness_report(80)
test_tick_frame_sends_all_chunks_in_order     # set_image(3-chunk jpeg) -> next tick writes frame_reports(jpeg)
test_no_image_sends_no_frames
test_brightness_change_sent_once              # set_brightness(40); two tick_claim -> one brightness_report(40)
test_write_error_disconnects_without_raising  # device raises OSError -> connected is False, close() called
test_reconnect_resumes_image_and_brightness   # after loss, tick_reconnect with a new FakeDevice ->
                                              # claim, brightness_report(current), then frames on next tick
test_open_returning_none_stays_disconnected   # every tick is a no-op, nothing raises
```

- [ ] **Step 2: Run.** Expected: fails on import.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** tests and ruff. Expected: pass.
- [ ] **Step 5: Commit.**

### Task 6: Settings schema and lights

**Files:**
- Create: `extension/omenrgb@konfus-dev.github.io/schemas/org.gnome.shell.extensions.omenrgb.gschema.xml`, `service/omenrgb/settings.py`, `service/omenrgb/lights.py`
- Test: `service/tests/test_settings.py`, `service/tests/test_lights.py`

**Interfaces — Produces:**

Schema id `org.gnome.shell.extensions.omenrgb`, path `/org/gnome/shell/extensions/omenrgb/`:

| Key | Type | Default | Meaning |
|---|---|---|---|
| `lights-enabled` | `b` | `true` | all lights on or off |
| `lights-brightness` | `i`, range 0–100 | `100` | scales every colour |
| `device-lights` | `a{s(ss)}` | `{}` | OpenRGB device name → (mode name, colour `#RRGGBB`) |
| `lcd-enabled` | `b` | `true` | off means screen black |
| `lcd-brightness` | `i`, range 0–100 | `80` | |
| `lcd-image` | `s` | `''` | absolute path; empty means leave the OMEN logo |
| `lcd-fit` | `s`, choices `fill`, `fit` | `'fill'` | |
| `lcd-recent-images` | `as` | `[]` | newest first, at most 8 |
| `status` | `s` | `'{}'` | written by the service only; JSON |

`status` JSON shape: `{"lcd": bool, "openrgb": bool, "devices": [{"name": str, "modes": [str]}], "error": str}`.

- `settings.py`: `load_settings(schema_dir: Path) -> Gio.Settings` (uses `Gio.SettingsSchemaSource.new_from_directory`; raises `FileNotFoundError` with the path and the hint "run install/install-user.sh" if `gschemas.compiled` is missing).
- `lights.py`:
  - `DEFAULT_MODE = "Static"`, `DEFAULT_COLOUR = "#FFFFFF"`, `FALLBACK_MODE = "Direct"`
  - `scale_colour(hex_colour: str, brightness: int) -> tuple[int, int, int]` (bad input → white scaled)
  - `@dataclass(frozen=True) class DeviceInfo: name: str; modes: tuple[str, ...]`
  - `class Lights`: `__init__(self, connect: Callable[[], object])` where `connect` returns an `openrgb.OpenRGBClient`-like object or raises; `connected: bool`; `try_connect(self) -> bool`; `devices(self) -> list[DeviceInfo]`; `apply(self, enabled: bool, brightness: int, per_device: dict[str, tuple[str, str]]) -> None`.

`apply` behaviour, per device: `enabled` false → black. Otherwise use the device's entry or the defaults; if the mode name is not one of the device's modes use `FALLBACK_MODE`, and if that is absent too, the device's first mode; set the mode, then the scaled colour. Any exception from the client marks `connected` false and does not propagate; one failing device does not stop the others.

- [ ] **Step 1: Write the schema**, then `glib-compile-schemas --strict --dry-run extension/omenrgb@konfus-dev.github.io/schemas`. Expected: no output.
- [ ] **Step 2: Write the failing tests.**

```python
# test_settings.py  (compiles the schema into tmp_path, uses the GSettings memory backend)
test_defaults                      # lights-enabled True, lcd-brightness 80, lcd-fit "fill", status "{}"
test_missing_compiled_schema       # FileNotFoundError mentioning install-user.sh

# test_lights.py  (FakeClient with FakeDevice objects recording set_mode / set_color)
def test_scale_colour():
    assert scale_colour("#FF8000", 100) == (255, 128, 0)
    assert scale_colour("#FF8000", 50) == (128, 64, 0)
    assert scale_colour("#FF8000", 0) == (0, 0, 0)
    assert scale_colour("nonsense", 100) == (255, 255, 255)
test_apply_uses_per_device_entry           # {"Fans": ("Breathing", "#00FF00")} -> set_mode("Breathing"), green
test_apply_defaults_for_unlisted_device    # Static, white
test_unknown_mode_falls_back_to_direct     # then to first mode when Direct is absent
test_disabled_sets_black
test_one_failing_device_does_not_stop_others
test_connect_failure_is_quiet              # connect raises ConnectionRefusedError -> try_connect() False
test_client_error_marks_disconnected       # then try_connect() succeeds again with a working connect
```

- [ ] **Step 3: Run.** Expected: fails on import.
- [ ] **Step 4: Implement.** In the real `connect` (built in Task 7) use `OpenRGBClient(OPENRGB_HOST, OPENRGB_PORT, "omenrgb")`, `device.set_mode(name)` and `device.set_color(RGBColor(r, g, b))`, or the forms recorded in Task 1 Step 8.
- [ ] **Step 5: Run** tests and ruff. Expected: pass.
- [ ] **Step 6: Commit.**

### Task 7: Service, systemd units, user install

**Files:**
- Create: `service/omenrgb/service.py`, `service/omenrgb/__main__.py`, `systemd/openrgb-server.service`, `systemd/omenrgb.service`, `install/install-user.sh`
- Test: `service/tests/test_service.py`

**Interfaces:**
- Consumes: everything from Tasks 2–6.
- Produces: `class Service` with `__init__(self, settings: Gio.Settings, streamer: LcdStreamer, lights: Lights, prepare: Callable[[Path, str], bytes] = prepare_jpeg)`, `start(self) -> None` (connect signals, apply everything once, add GLib timers), `on_changed(self, key: str) -> None`, `publish_status(self) -> None`; and `main(argv: list[str] | None = None) -> int` with one option `--schema-dir` (default `~/.local/share/gnome-shell/extensions/<EXTENSION_UUID>/schemas`).

Behaviour:
- `lcd-image` or `lcd-fit` changed → `prepare`; on `ImageError` keep the current image and put the message in `status.error`; on success clear `error`. Empty path → `set_image(None)`.
- `lcd-enabled` false → brightness 0; true → `lcd-brightness`.
- Any `lights-*` or `device-lights` change → `lights.apply(...)`.
- Every `RECONNECT_INTERVAL_S`: `streamer.tick_reconnect()`; if lights are disconnected, `try_connect()` and re-apply on success; then `publish_status()`.
- `publish_status` writes `status` only when the JSON text differs from the stored value.
- Logging through `logging` to stderr (journald picks it up), one line per event, no per-frame logging.

`systemd/openrgb-server.service`: `ExecStart=/usr/bin/openrgb --server --server-host 127.0.0.1` (or the Task 1 form), `Restart=on-failure`, `WantedBy=default.target`.
`systemd/omenrgb.service`: `After=openrgb-server.service`, `Wants=openrgb-server.service`, `ExecStart=%h/.local/share/omenrgb/venv/bin/python -m omenrgb`, `Restart=on-failure`, `RestartSec=5`, `WantedBy=default.target`.

`install/install-user.sh` (refuses to run as root; safe to re-run):
  1. `python3 -m venv --system-site-packages ~/.local/share/omenrgb/venv` and `pip install -e <repo>/service` (editable, so editing the repo and restarting the service is enough)
  2. symlink `extension/omenrgb@konfus-dev.github.io` into `~/.local/share/gnome-shell/extensions/`
  3. `glib-compile-schemas` on the extension's `schemas/`
  4. copy both units to `~/.config/systemd/user/`, `systemctl --user daemon-reload`, `enable --now` both
  5. print: "Log out and back in once, then run: gnome-extensions enable omenrgb@konfus-dev.github.io"

- [ ] **Step 1: Write the failing tests** (memory-backend settings from Task 6's test helper, fake streamer and fake lights recording calls, fake `prepare`):

```python
test_start_applies_everything_once          # prepare called with (path, "fill"); lights.apply called once
test_image_change_calls_prepare_and_set_image
test_bad_image_keeps_previous_and_reports   # prepare raises ImageError("boom") -> set_image not called again,
                                            # json.loads(status)["error"] == "boom"
test_good_image_clears_error
test_empty_image_path_clears_image          # set_image(None)
test_lcd_disabled_means_brightness_zero     # and re-enabling restores lcd-brightness
test_light_keys_trigger_apply               # each of lights-enabled, lights-brightness, device-lights
test_lights_reconnect_reapplies             # lights.connected False then try_connect True -> apply called
test_openrgb_down_does_not_touch_lcd        # try_connect False -> streamer still ticked, status.openrgb False
test_status_written_only_when_changed       # two publish_status calls -> one "changed::status" signal
```

- [ ] **Step 2: Run.** Expected: fails on import.
- [ ] **Step 3: Implement** `service.py`, `__main__.py`, the two units, `install-user.sh`.
- [ ] **Step 4: Run** `.venv/bin/pytest service -q && .venv/bin/ruff check service && bash -n install/install-user.sh`. Expected: pass.
- [ ] **Step 5: Install and check on the hardware.** Run `bash install/install-user.sh`, then `systemctl --user is-active openrgb-server omenrgb`. Expected: `active` twice. Define `G="gsettings --schemadir ~/.local/share/gnome-shell/extensions/omenrgb@konfus-dev.github.io/schemas"` and run:
  - `$G set org.gnome.shell.extensions.omenrgb lcd-image /usr/share/backgrounds/<any file>` → ask the owner: is it on the LCD?
  - `$G set … lights-brightness 30`, then `lights-enabled false` → ask the owner what the lights did.
  - `$G get … status` → shows `"lcd": true`, `"openrgb": true` and the device list.
  - `systemctl --user stop openrgb-server`; wait 10 s → status shows `"openrgb": false`, LCD image still showing; start it again → lights come back within 10 s.
- [ ] **Step 6: Commit.**

### Task 8: Extension settings window

**Files:**
- Create: `extension/omenrgb@konfus-dev.github.io/metadata.json`, `extension/omenrgb@konfus-dev.github.io/prefs.js`, and a minimal `extension.js` (empty `enable`/`disable`) so the extension loads

**Interfaces:**
- Consumes: the schema keys and `status` JSON from Task 6.
- Produces: `metadata.json` with `uuid` `omenrgb@konfus-dev.github.io`, `name` `OMEN Lights`, `shell-version` `["50"]`, `settings-schema` `org.gnome.shell.extensions.omenrgb`, `url` of the repo.

`prefs.js`: default export extends `ExtensionPreferences`, implements `fillPreferencesWindow(window)` with `this.getSettings()`. Two `Adw.PreferencesPage`s:
- **LCD**: `Adw.SwitchRow` ↔ `lcd-enabled`; a 240 px `Gtk.Picture` preview of `lcd-image`; "Choose image…" button opening `Gtk.FileDialog` with an images filter; `Adw.ComboRow` Fill/Fit ↔ `lcd-fit`; `Gtk.Scale` 0–100 ↔ `lcd-brightness`; an `Adw.Banner` showing `status.error` when not empty, and "LCD not found" when `status.lcd` is false.
- **Lights**: `Adw.SwitchRow` ↔ `lights-enabled`; `Gtk.Scale` ↔ `lights-brightness`; one `Adw.ExpanderRow` per entry in `status.devices` holding an `Adw.ComboRow` of that device's modes and a `Gtk.ColorDialogButton`, both writing `device-lights`; rebuilt when `changed::status` fires; an `Adw.StatusPage` "OpenRGB is not running" when `status.openrgb` is false.

Choosing an image sets `lcd-image` and puts the path at the front of `lcd-recent-images`, removing duplicates and trimming to 8. Put that list logic in one small function `pushRecent(list, path)` at the top of the file. Simple keys use `settings.bind`.

- [ ] **Step 1: Write the three files.**
- [ ] **Step 2: Owner logs out and back in**, then `gnome-extensions enable omenrgb@konfus-dev.github.io`.
- [ ] **Step 3: Verify.** `gnome-extensions info omenrgb@konfus-dev.github.io` shows `State: ACTIVE`. `gnome-extensions prefs omenrgb@konfus-dev.github.io` opens the window. `journalctl --user -b -g omenrgb -p warning` shows no JavaScript errors.
- [ ] **Step 4: Owner checklist.** Choose a PNG → it appears on the LCD and in the preview. Switch Fill/Fit → LCD changes. Choose a text file renamed `.png` → banner shows an error, LCD keeps the previous image. Each device row lists its modes; changing colour changes that device's lights. Choose the same image twice → `gsettings … get lcd-recent-images` lists it once.
- [ ] **Step 5: Commit.**

### Task 9: Top-right menu entry

**Files:**
- Modify: `extension/omenrgb@konfus-dev.github.io/extension.js`

**Interfaces:**
- Consumes: schema keys; `Extension`, `QuickMenuToggle`, `QuickSlider`, `SystemIndicator` from GNOME Shell 50 (`resource:///org/gnome/shell/ui/quickSettings.js`), `PopupMenu`.

Structure, one class each, all registered with `GObject.registerClass`:
- `OmenToggle extends QuickMenuToggle`: title "OMEN Lights", icon `keyboard-brightness-symbolic`, `checked` bound to `lights-enabled`. Its menu holds: header "OMEN Lights"; a slider item "Lights" ↔ `lights-brightness`; a slider item "LCD" ↔ `lcd-brightness`; a section "Recent images" with up to 4 `PopupImageMenuItem`s (file's basename, `Gio.FileIcon` thumbnail) that set `lcd-image` when activated, rebuilt on `changed::lcd-recent-images`; a separator; "Settings…" calling `extension.openPreferences()`.
- `OmenIndicator extends SystemIndicator`: creates the toggle, pushes it to `quickSettingsItems`.
- Default export `extends Extension`: `enable()` creates the indicator and calls `Main.panel.statusArea.quickSettings.addExternalIndicator(indicator)`; `disable()` destroys every item and the indicator and sets every field to `null`. Every `settings.connect` is paired with a disconnect in `destroy`.

Slider values are 0–1 in the widget and 0–100 in settings; convert in one helper `bindSlider(settings, key, slider)` that also guards against feedback loops.

- [ ] **Step 1: Write `extension.js`.**
- [ ] **Step 2: Reload.** Owner logs out and back in (Wayland cannot reload an extension in place).
- [ ] **Step 3: Verify.** `journalctl --user -b -g omenrgb -p warning` shows no errors. `gnome-extensions disable` then `enable` leaves exactly one "OMEN Lights" button and logs no errors.
- [ ] **Step 4: Owner checklist.** The button sits with Wi-Fi and Bluetooth. Clicking it turns all lights off and on. Both sliders act within a second. A recent image click changes the LCD. "Settings…" opens the window from Task 8. With `systemctl --user stop omenrgb` the menu still opens and nothing freezes.
- [ ] **Step 5: Commit.**

### Task 10: README and release to main

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Rewrite `README.md`**: what it does, supported hardware (from the spec, including what Task 1 found missing), install (the two scripts, the log-out step, the enable command), how to change things (file map from this plan's File Structure, "edit, then `systemctl --user restart omenrgb`"), troubleshooting (`journalctl --user -u omenrgb`, the `status` key), uninstall steps, credit for the protocol notes, licence.
- [ ] **Step 2: Final check.** `.venv/bin/pytest service -q && .venv/bin/ruff check service`. Expected: pass. Reboot (cold: power off and on); after login the image and lights return without touching anything.
- [ ] **Step 3: Commit, merge `dev` into `main` with `--no-ff`, push both** (needs GitHub access, see the pending item with the owner).
