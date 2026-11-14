# Design

How OmenRGB-Controller is put together, and why. For installing and
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

## Layers

```
 front ends                 platforms                 core                  backends
 (what you touch)           (one per system)          (the behaviour)       (hardware)

 GNOME Shell extension --+
                         +-> gnome.py -----+
                                           +--> Service ---> LcdStreamer -> hidraw.py    -> cooler LCD
 Batocera service switch +                 |      |-------> Lights ------> openrgb.py   -> case lights
 and game hook ----------+-> batocera.py --+      |                  \---> tracer.py    -> case lights
                                                  \-------> RamLights ---> ram_bios.py  -> BIOS -> RAM
```

- **core** (`service/omenrgb/core/`) decides what the LCD and lights
  should show. It imports no operating-system or hardware-library modules.
  Everything it needs is handed to `Service`: settings, a scheduler, the
  objects that reach hardware. `interfaces.py` describes those shapes.
- **backends** (`service/omenrgb/backends/`) each reach one kind of
  hardware one way.
- **platforms** (`service/omenrgb/platforms/`) are the only place that
  knows about a particular system. Each has a `main()` that picks backends,
  a settings store, timers, and a source for "what is in focus", and builds
  the `Service` from them.
- **front ends** (`frontends/`) never touch hardware. A front end writes
  the settings listed in `core/settings_schema.py` and reads the `status`
  report; that list is the whole contract.

| | GNOME desktop | Batocera |
|---|---|---|
| Front end | Shell extension: menu button and settings window | Service switch, game hook, a JSON file |
| Settings store | GSettings (`gsettings_store.py`) | JSON file (`json_store.py`) |
| Case lights | OpenRGB server (`openrgb.py`) | own driver (`tracer.py`) |
| RAM lights | root helper over a socket | in-process (the service is root), if `acpi_call` exists |
| In focus | the focused app; its icon from the icon theme | the running game; its scraped artwork |
| Idle look (nothing in focus) | the wallpaper and its main colour (`gnome_theme.py`) | the menu theme: an LCD animation or picture it ships, else its background (`batocera_theme.py`) |
| Sleep/wake | systemd-logind | not handled |
| Timers, file watching | GLib (`glib_runtime.py`) | GLib |

A bug in a front end cannot stall hardware handling, and a new system does
not touch the core: it is one more file in `platforms/`.

On the GNOME desktop the parts run as: the extension inside GNOME Shell,
the service and an OpenRGB server as systemd user units, and the RAM helper
as a socket-activated root unit.

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
HP's software uses for a still picture. When the picture changes, a short
burst of blended frames crossfades from the old one to the new
(`core/lcd_fade.py`). Confirmed on firmware V3.05.024.

Protocol facts are from the notes in
[omen-led-lcd](https://github.com/Xedecimal/omen-led-lcd); none of its code
is used.

## Case lights

USB `103c:84fd` "HP TracerLED". OpenRGB drives it with its "HP Omen 30L"
driver. It reports seven zones; on the 45L only the front logo and the
three front fans have lights, so the other three are hidden
(`omen45l.HIDDEN_ZONES`).

OpenRGB runs with its own configuration folder in which every detector
except that one is switched off (`backends/openrgb.py`). Its normal RAM
probing is what jams the RAM bus.

Plain colours and breathing are not left to the board. The service puts it
in "Direct" mode, where it shows whatever colours it is sent, and draws them
itself (`lights._Glow`):

- A colour change glides from the old colours to the new over 0.4 s.
- Breathing is a smoothly changing brightness (`lights.breath_level`), which
  is what makes the speed, dimmest and brightest sliders work. The board's
  own breathing always fades to black and has three fixed speeds.

Frames go out about 30 times a second, but only while something is fading
or breathing; the fast timer stops itself otherwise, so a steady colour
costs nothing. The board's other effects (Wave, Color Cycle, ...) are still
its own.

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

Each write takes about 65 ms through the BIOS, so `core/ram.py` remembers what
every register holds and sends only the ones that changed, on a worker
thread, newest request wins. A colour change is then about ten writes
instead of forty, and never delays the LCD or the case lights. The RAM
cannot fade smoothly at that speed; it switches in one step.

## Auto mode

Optional. The extension writes the focused app's id (for example
`firefox.desktop`) to the `focused-app` setting, and only while auto mode is
on. Everything else happens in the service (`core/look.py`, with the icon lookup in `platforms/gtk_icons.py`): it finds the
app's icon in the current icon theme, picks the icon's most striking colour
(grouping pixels by hue, ignoring greys, then making the winner vivid enough
for LEDs), and builds an LCD picture of the icon on black. That colour
replaces every zone colour and the picture replaces the chosen LCD image
until focus moves on. Apps with no usable icon, and this project's own
settings window, fall back to the normal settings.

## Sleep

The service holds a logind "delay" lock. Just before the computer sleeps it
turns the LCD, case lights and RAM off, then releases the lock. After
waking it sends every setting again, in case the hardware lost them.

## Settings

Every setting and its default is listed in `core/settings_schema.py`. On
GNOME they are stored in GSettings (schema in
`frontends/gnome-shell/…/schemas/`, checked against that list by a test);
on Batocera in a JSON file. The front end writes; the service watches. The
`status` key goes the other way: the service writes a JSON report (what is
connected, the last image error, and the lighting devices with their
zones, effects and display names).

## Tested hardware

OMEN by HP 45L GT22-3xxx (board 8D2C), Ubuntu 26.04, GNOME Shell 50,
OpenRGB 0.9, LCD firmware V3.05.024.
