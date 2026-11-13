# omen-control: design

Date: 2026-11-13. Status: approved by the owner 2026-11-13.

## Intent

The owner has an HP OMEN 45L (GT22-3xxx, board 8D2C) running Ubuntu 26.04
(GNOME, Wayland). HP's lighting and LCD software is Windows-only. They want
to put their own images on the CPU-cooler LCD and to change the fan, case and
RAM lighting, all from one place, without booting Windows.

Standing instruction from the owner: **use existing tooling as much as
possible.** Write new code only where nothing existing does the job.

## Goals

1. Show a custom image, chosen by the owner, on the cooler LCD, and keep it
   there while they are logged in.
2. Set colour, effect and brightness for the front fans / case lights and the
   RAM sticks.
3. One place for both: an entry in the GNOME top-right system menu (next to
   Wi-Fi, Bluetooth, etc.), installed as a GNOME Shell extension.
4. Settings come back by themselves after reboot and login.
5. Easy for the owner to modify later: small readable modules, standard
   GNOME and Python conventions, no clever abstractions.

## Non-goals (v1)

- The RGB ring on the pump head. No way to control it is known.
- Animated GIFs or video on the LCD, live system stats on the LCD.
- Fan speed / pump speed / performance profiles.
- Other OMEN models, other distros, other desktops than GNOME, packaging
  (.deb, Flatpak), publishing on extensions.gnome.org.
- Lighting at the login screen or before login.

## Anti-goals

- Sending anything to the LCD beyond the four documented operations below.
  No firmware, no guessed commands.
- Writing to the RAM sticks' SMBus with our own code. Only OpenRGB does that.
- Copying code from `Xedecimal/omen-led-lcd` (it has no licence). Protocol
  facts only.
- Needing root to run day to day. Root is for one-time setup only.

## Hardware facts (read from the machine)

| Part | Device | How it is reached |
|---|---|---|
| Fans / case lights | USB HID `103c:84fd` "HP TracerLED" | OpenRGB's HP Omen 30L driver is registered for this ID. Unconfirmed on the 45L. |
| RAM | 2 DDR5 sticks, SMBus `i2c-5` (I801), SPD at 0x51 / 0x53 | OpenRGB DDR5 drivers. Brand unknown until read as root. |
| LCD | USB HID `03f0:7397` "LCD-PUMP", two HID interfaces | Vendor interface (usage page 0xFF1A). The other is a mouse-class interface; never write to it. |

The LCD's vendor interface report descriptor, read from the machine, matches
the public protocol notes: report ID 1 is 63 bytes in and out; report ID 2 is
511 bytes in, 1023 bytes out.

### LCD protocol (from public notes, provisional until the spike confirms)

Source: https://github.com/BanditF/foxglove/issues/470, summarising
`PROTOCOL_NOTES.md` in https://github.com/Xedecimal/omen-led-lcd. The builder
should read `PROTOCOL_NOTES.md` for exact byte layouts.

- `0x41` claim handshake (byte 5 = 1; reply has byte 5 = 7). HP's software
  re-sends it about once a second.
- `0x46` info; returns ASCII `OMEN-LCD-CAP,V3.05.019`.
- `0x6c` brightness in byte 12; strongly nonlinear; 0 is black.
- `0x6e` display frame: a 480x480 JPEG sent over report ID 2, 10-byte header
  per transfer, up to 1013 payload bytes per chunk.
- The screen stores nothing. Without a steady stream of frames it falls back
  to the OMEN logo. The last image survives a warm reboot, not a cold boot.

## Approaches

- **A. Our control panel, OpenRGB as a hidden engine for lights (chosen).**
  One window. Reuses OpenRGB for fans and RAM. Cost: lights depend on
  OpenRGB supporting this hardware.
- **B. OpenRGB's window for lights plus a separate LCD tool.** Least work,
  but two places to go; rejected because the owner asked for one place.
- **C. All our own code.** Redoes working fan/RAM control and puts our code
  on the RAM SMBus; rejected as risky and against "use existing tooling".
- **D. LCD add-on inside OpenRGB.** One app, but C++ tied to OpenRGB's
  internals; rejected as fragile.

The owner picked A.

## Design

Three pieces, all running as the logged-in user. Target: GNOME Shell 50
(installed: 50.1).

```
 +-------------------------+                         +---------------------+
 | GNOME Shell extension   |       GSettings         |  omen-control       |
 |  - system-menu entry    | ----------------------> |  service (systemd   |
 |  - settings window      |   (one shared schema)   |  user unit, Python) |
 +-------------------------+                         +----+-----------+----+
                                                          |           |
                                                          v           v
      OpenRGB server (systemd user unit, no window) <-- lights     LCD over
      -> TracerLED, RAM                                            /dev/hidraw
```

The extension never touches hardware. It only reads and writes settings; the
service is the only thing that talks to the LCD and to OpenRGB. This keeps
the code that runs inside GNOME Shell tiny, so a bug in it cannot stall the
desktop, and lets each half be changed without touching the other.

**Service.** Starts at login. Owns the LCD: claims it, sets brightness, and
keeps re-sending the current image at whatever rate the spike shows is
needed to stop the logo coming back. Applies the saved lighting through
OpenRGB once OpenRGB is up. Reacts when a setting changes. If the LCD or
OpenRGB is missing, it logs that and keeps the other half working. It also
publishes what it found (device and zone names, available effects, LCD
connected or not) so the extension can list them without hard-coding.

**System-menu entry.** For the things changed often. It follows the look of
the built-in entries (a toggle button that opens a submenu):

```
 +--------------------------------------+
 | [Wi-Fi      >] [Bluetooth   >]       |
 | [Power Mode >] [OMEN Lights >]  <- click: lights on/off; arrow: menu
 +--------------------------------------+
   OMEN Lights
   Brightness   [=======---]
   LCD          [=====-----]
   Recent images  [img] [img] [img] [img]
   Settings...
```

**Settings window.** Opened from "Settings..." or from the Extensions app.
A standard libadwaita preferences window, for the things changed rarely:

```
 [ LCD ]  [ Lights ]
 ------------------------------------------------
 LCD:   [ preview ]   [Choose image...]
        Fit: (o) Fill  ( ) Fit      Brightness [====--]
 Lights: one row per device/zone the service reports
        Front fans   [colour]  [effect v]  [brightness]
        RAM          [colour]  [effect v]  [brightness]
```

Changes apply immediately and are saved. Any image format Pillow opens is
accepted; the service crops or letterboxes it to 480x480 and re-encodes it
as JPEG.

**OpenRGB.** Installed from Ubuntu's `openrgb` package and run with its SDK
server on localhost only. The owner never needs to open its window.

**One-time setup (needs sudo, run by the owner).** Install packages; a udev
rule giving the logged-in user access to `03f0:7397`; load `i2c-dev` for RAM.
OpenRGB's package ships its own udev rules. Provide this as one script the
owner can read before running.

### Calls made by the designer (not decided by the owner)

- Extension in plain JavaScript (the only language GNOME Shell extensions
  can use), standard layout: `metadata.json`, `extension.js`, `prefs.js`,
  `schemas/`. No build step, no TypeScript, no bundler.
- Service in Python 3, using what Ubuntu already ships: `python3-gi` and
  `python3-pil` are already installed. LCD access through `python3-hid` or
  plain `/dev/hidraw` writes, builder's choice.
- OpenRGB client: the `openrgb-python` library (pip, in a venv with access
  to system packages; Ubuntu has no package for it). Its licence must be
  checked first; see the open question below.
- Extension and service share one GSettings schema: the extension writes,
  the service watches. Information flowing the other way (device list,
  status) goes through a small D-Bus interface on the session bus or a
  read-only settings key, builder's choice. These are the standard GNOME
  mechanisms; no custom sockets or file formats.
- Easy to modify: one job per file (LCD protocol, image preparation, lights,
  settings), hardware details such as USB IDs and packet sizes as named
  constants in one place, tests for the parts that need no hardware, `ruff`
  for Python and GNOME's standard ESLint config for JavaScript.
- Repo: `main` is stable, work happens on `dev`.

### Licence (decided by the owner)

GPL-3.0-or-later, so the existing `openrgb-python` library can be used
whatever its licence turns out to be among the GPL family.

## Order of work

0. **One-time setup, run by the owner.** The builder has no sudo. The
   builder writes the setup script first; the owner reads and runs it. After
   that the builder may talk to the LCD and to OpenRGB directly.
1. **Spike (throwaway).** Show one image on the LCD and find the frame rate
   needed to hold it; run OpenRGB and record which devices, zones and
   effects it finds. Update this document with the results. Limits:
   - LCD: start with the read-only info operation. If a frame does not
     show, the builder may vary chunk size, timing and header fields within
     the four documented operations. No other operation codes. If it still
     fails, stop and return to the owner.
   - Lights: if OpenRGB drives only some of the hardware, or only static
     colours, carry on with what works and tell the owner what is missing.
     If it drives none of it, stop and return to the owner with options.
   - Streaming: if holding a still image costs more than about 5% of one
     CPU core, return to the owner before building on it.
2. Service: LCD half, then lights half. Usable from the command line with
   `gsettings` at this point.
3. Extension: settings window first, then the system-menu entry.
4. Setup script, README, start-at-login.

## Open risks

- OpenRGB's 30L driver may not work, or may only partly work, on the 45L.
- RAM brand may be one OpenRGB does not support.
- Holding a still image may need a constant stream; CPU and USB cost unknown.
- HP may change the LCD protocol in a firmware update (current: V3.05.019).
- GNOME Shell extensions need a small update for most new GNOME releases
  (every six months). Keeping the extension thin limits that cost.

## Left to the builder

Module layout, settings key names, test approach (the protocol encoder and
image preparation should be testable without hardware), error messages, and
the exact streaming loop.

## Hardware test results (2026-11-13)

**LCD.** Works. Firmware reports `N9,01,HS,SQ,OMEN-LCD-CAP,V3.05.024,0.1`,
build `Jun 18 2025` (newer than the public notes' V3.05.019; same protocol).
Claim is acknowledged with byte[5]=7. Labelled 480x480 JPEG test screens
were seen by the owner. Replies arrive about 0.2 s late and queued one
behind the request, so the service never depends on them. Frames are sent
every 500 ms, the rate HP's own software uses for a still image. Streaming
cost: below 1% of one CPU core.

**Case lights.** Work through OpenRGB. It reports one device, "HP Omen 30L",
modes `Direct, Static, Off, Breathing, Color Cycle, Blinking, Wave, Radial`,
and seven one-LED zones: `Omen Logo, Light Bar, Front Fan, CPU Cooler, Front
Bottom Fan, Front Middle Fan, Front Top Fan`. Setting the whole device green
turned the front logo and all three front fans green. On the 45L, `Front
Fan` and `CPU Cooler` are reported by others to be unconnected. `Direct` and
`Static` take one colour per zone; the effect modes take a short palette and
a speed of 1 to 3.

Consequence for the design: lighting settings are per zone (colour) and per
device (mode), not per device only.

**RAM lights. Not controllable yet.** The sticks are an HP OEM kit made by
Kingston (`HP60U40S8HA-16FRE`, two sticks). OpenRGB's Kingston Fury DDR5
driver gets no answer from them at its expected addresses. They keep doing
their own thing (one stick amber, one colour-cycling). Left out of v1.

**OpenRGB server.** `openrgb --server --server-host 127.0.0.1
--noautoconnect` works. Device detection finishes a few seconds after the
port opens, so a client must retry until devices appear. `openrgb-python`
0.3.8 works on Python 3.14: `OpenRGBClient(host, port, name)`,
`device.set_mode(...)`, `zone.set_color(RGBColor(r, g, b))`.

## RAM lights (added 2026-11-13, approach approved by the owner)

**Finding.** Talking to the RAM sticks' light controllers directly over SMBus
(as OpenRGB does) jams the bus on this board until the next reboot; even a
single read does it. HP's own OMEN Gaming Hub never does that. It sends the
standard Kingston Fury register writes through the BIOS: WMI command
`0x20009`, type `0x0A`, data `[register, value, stick address]`, where the
address is `0xC0 + 2 * slot`. Sent that way from Linux (ACPI method
`\_SB.WMID.WMAA`), both sticks obeyed and the bus stayed healthy.

**Anti-goal, reworded.** Nothing in this project ever talks to the RAM over
SMBus. RAM lighting goes through the BIOS only.

**Approaches.** (A) a small root helper service using the packaged
`acpi_call` module, (B) our own kernel module, (C) a password prompt per
change. The owner picked A: no kernel code to maintain, existing package.

**Design.**
- `helper/omenrgb_ram_helper.py`: runs as root from a root-owned copy,
  standard library only, started by systemd on demand through a socket that
  only the installing user can open. It accepts one request, "write value V
  to lighting register R of stick A", checks A, R and V against a fixed
  list, checks that a stick is really in that slot, and passes it to the
  BIOS. It can do nothing else.
- `service/omenrgb/ram.py`: turns "mode, brightness, colour per stick" into
  the register writes HP uses, and sends them to the helper. It presents the
  RAM as one more lighting device ("RAM", zones "Stick 1", "Stick 2", ...),
  so the settings window needs no change.
- v1 effects: Static (one colour per stick), Rainbow, Breathing, and off.
- Register meanings come from HP's module and from OpenRGB's
  `KingstonFuryDRAMController` (GPL-2.0-or-later).
