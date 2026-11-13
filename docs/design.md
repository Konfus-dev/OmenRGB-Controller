# Design

How OmenRGB-Linux-Controller is put together, and why. For installing and
using it, see the [README](../README.md).

## Goals

- Put the owner's own images on the cooler LCD and keep them there.
- Set colour, effect and brightness for the case lights and the RAM sticks.
- One place for all of it: a button in the GNOME system menu that opens a
  settings window.
- Settings return by themselves after a reboot.
- Easy to change later: one job per file, hardware numbers in one place,
  existing tools reused wherever one does the job.

## Things it deliberately does not do

- Send anything to the LCD beyond three known operations (claim,
  brightness, image frame). No firmware, no guessed commands.
- Talk to the RAM sticks over SMBus. On this board that jams the RAM's
  control bus until the next reboot (see "RAM lights" below).
- Run day-to-day code as root, apart from one small, single-purpose helper.

## Parts

```
 GNOME Shell extension        GSettings         omenrgb service (your user)
  extension.js  (button)  ------------------>    lcd_*.py  ----> cooler LCD (USB)
  prefs.js      (window)  <--- status key ---    lights.py ----> OpenRGB server --> case lights
                                                 ram.py    ----> root helper --> BIOS --> RAM lights
```

| Part | Runs as | Job |
|---|---|---|
| `extension/…/extension.js` | you, inside GNOME Shell | The menu button. Opens or focuses the settings window. |
| `extension/…/prefs.js` | you | The settings window. Reads and writes settings only. |
| `service/omenrgb/` | you (systemd user unit) | Watches the settings and drives the hardware. |
| OpenRGB server | you (systemd user unit) | Talks to the case-light board. Limited to that one detector. |
| `helper/omenrgb_ram_helper.py` | root (systemd, socket-activated) | Passes checked RAM lighting writes to the BIOS. Nothing else. |

The extension never touches hardware, so a bug in it cannot stall the
desktop. The service is the only thing that knows about devices; it reports
what it found in the `status` setting, and the window builds itself from
that report instead of hard-coding devices.

## Cooler LCD

USB `03f0:7397` "LCD-PUMP", two HID interfaces. Only the vendor interface
(report descriptor starting `06 1a ff`) is used; the other is mouse-class
and must not be written to.

| Operation | Report | Meaning |
|---|---|---|
| `0x41` | ID 1, 64 bytes, byte 5 = 1 | Claim the screen. Repeated every second. |
| `0x6c` | ID 2, 1024 bytes, byte 12 = 0–100 | Brightness. |
| `0x6e` | ID 2, 1024 bytes | One chunk of a 480x480 JPEG: total length at bytes 4–5, chunk index at 8, chunk length at 9–10, up to 1013 data bytes from 11. |

The screen stores nothing; without a stream of frames it returns to the
OMEN logo. The service re-sends the current image every 500 ms, the rate
HP's software uses for a still picture. Confirmed on firmware V3.05.024.

Protocol facts are from the notes in
[omen-led-lcd](https://github.com/Xedecimal/omen-led-lcd); none of its code
is used.

## Case lights

USB `103c:84fd` "HP TracerLED". OpenRGB drives it with its "HP Omen 30L"
driver. It reports seven zones; on the 45L only the front logo and the
three front fans have lights, so the other three are hidden
(`constants.HIDDEN_ZONES`).

OpenRGB runs with its own configuration folder in which every detector
except that one is switched off (`openrgb_config.py`). Its normal RAM
probing is what jams the RAM bus.

## RAM lights

The sticks are HP-branded Kingston Fury DDR5 (`HP60U40S8HA-16FRE`). They
speak the standard Kingston lighting registers, but on this board any
direct SMBus access to their light controllers (address `0x61`, `0x63`),
even a read, makes the whole RAM bus stop answering until reboot.

HP's OMEN Gaming Hub avoids that by asking the BIOS to deliver each
register write: WMI command `0x20009`, type `0x0A`, data
`[register, value, address]` with address `0xC0 + 2 * slot`. From Linux
that is ACPI method `\_SB.WMID.WMAA`, reached through the `acpi_call`
kernel module. Sent this way the sticks obey and the bus stays healthy.

Because only root can call ACPI methods, a root helper sits in between.
It was chosen over a custom kernel module (more to maintain) and over a
password prompt per change (unusable). The helper:

- runs from a root-owned copy in `/usr/local/lib/omenrgb/`, standard
  library only;
- listens on `/run/omenrgb-ram.sock`, which only the installing user can
  open;
- accepts one request, "write value V to register R of stick A", and
  checks A, R and V against fixed lists and that a stick is in that slot.

Register meanings come from HP's module and from OpenRGB's
`KingstonFuryDRAMController`.

## Settings

One GSettings schema, `org.gnome.shell.extensions.omenrgb`, in
`extension/…/schemas/`. The extension writes; the service watches. The
`status` key goes the other way: the service writes a JSON report (what is
connected, the last image error, and the lighting devices with their
zones, effects and display names).

## Tested hardware

OMEN by HP 45L GT22-3xxx (board 8D2C), Ubuntu 26.04, GNOME Shell 50,
OpenRGB 0.9, LCD firmware V3.05.024.
