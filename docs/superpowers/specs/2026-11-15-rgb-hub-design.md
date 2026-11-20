# RGB-Hub design

2026-11-15. Turns OmenRGB-Controller into **RGB-Hub**: one program and one
settings window for every lit device on the PC, with the IQUNIX keyboard as
the second device.

## Why

The owner has a PC (HP OMEN 45L: cooler LCD, case lights, RAM) whose lights
already follow what they are doing, and an IQUNIX keyboard that does not.
They want the keyboard to get the same controls and the same "follow what I
am doing" behaviour, and they want one place for all of it instead of a tool
per device. Adding a device later should be one more module, not a rewrite.

They also want a nice-looking **Default** look (theirs: everything purple,
Spike from Cowboy Bebop on the LCD) put back whenever the hub stops, so that
booting into something else (Windows on the dual boot) does not show
whatever game or app was last on screen.

## Goals

- One project, **RGB-Hub**, replacing OmenRGB-Controller, on both systems it
  runs on today: Ubuntu (GNOME) and Batocera.
- A small **device** interface in the core. Devices for now:
  - **OMEN-PC**: cooler LCD + case lights + RAM (today's code, wrapped).
  - **IQUNIX-KEYBOARD**: IQUNIX MG65 (new).
- The keyboard gets every control the OMEN lights have: on/off, brightness,
  effect, colour, speed, breathing min/max, and auto (follow the app/game).
- Settings window (GNOME): tabs **Auto**, **OMEN-PC**, **IQUNIX-KEYBOARD**.
  - **Auto** tab: one master switch that turns auto on for every device, and
    a switch per device. OMEN-PC keeps its two sub-switches (lights follow /
    LCD shows the app). Idle picture, idle colour and "Now following" move
    here as they are.
  - **One tab per device** with that device's controls. OMEN-PC's tab holds
    today's LCD page plus today's case-light and RAM groups.
  - A device that is not plugged in still has its tab, saying "Not
    connected".
- Batocera gets the same: the JSON settings, the service switch, and the
  EmuBox menu (Game Settings block and Guide quick menu) grouped per device,
  with the same master and per-device auto switches.
- **Default look on stop.** When the hub stops cleanly (app/service stopped,
  log-out, shutdown, reboot, Batocera service switched off or Batocera
  shutting down), every device is set to the Default before the program
  exits. Where a device can store a look itself, it is stored there, so it
  survives the hub being gone.

## Non-goals

- Xbox controller. The owner's is a Microsoft one over Bluetooth: its Xbox
  button is white only, and Linux cannot set it over Bluetooth. The device
  list must make a later controller device (via the Xbox wireless adapter)
  one more module, nothing more.
- Per-key keyboard colours. The keyboard's standard lighting commands set
  one colour for the whole board.
- Windows support (the design already keeps that possible).
- A global "all lights off" switch. Not asked for; each device has its own.

## Anti-goals

- Anything that probes the RAM's SMBus (addresses 0x61/0x63). RAM stays on
  the BIOS route through the root helper. OpenRGB stays limited to the "HP
  Omen 30L" detector. This rule from the OMEN work is unchanged.
- Sending the keyboard anything beyond the standard VIA commands listed
  below. No firmware, no guessed vendor commands, no keymap writes.
- Losing the owner's current settings in the rename.
- Wearing out the keyboard's memory: "save" is sent only at stop, never
  while drawing frames.

## Hardware facts found

**IQUNIX MG65**, USB `320f:5055`, four HID interfaces. The one with usage
page `0xFF60`, usage `0x61` (32-byte reports) is the VIA raw-HID interface.
It answered on 2026-11-15:

- `id_get_protocol_version` (0x01) -> `0x000B` (VIA protocol 11).
- `id_custom_get_value` (0x08) on channel 3 (`qmk_rgb_matrix`) gives real
  values: brightness (value id 1) = 9, effect (2) = 7, speed (3) = 0,
  colour (4) = hue 0x22, saturation 0xFF. Channels 1, 2 and 5 return zeros
  (not used).
- Writes use `id_custom_set_value` (0x07) with the same channel and value
  ids. They change the live look only. `id_custom_save` (0x09, channel 3)
  stores it in the keyboard; this is what makes the Default survive.
- Colour is hue + saturation; brightness is the matrix's brightness. The
  value range (0-255 in QMK) and the names of the effect numbers are not
  confirmed yet: brightness 9 is suspiciously low. Confirmed with the owner
  watching the keyboard during building.
- The hidraw nodes are root-only. Ubuntu needs a udev rule (`uaccess`) for
  `320f:5055`, as the LCD already has. Batocera runs as root.

**Case lights and RAM storing a look**: unknown. To find out during
building, with the owner watching: whether the TracerLED board keeps its
own Static colour (as opposed to "Direct", which the service draws) after
the hub stops, across a reboot and across a power-off. For RAM, only
registers already known from HP's module may be used; if none stores the
look, the RAM keeps the last colour sent for as long as it has power.

**Cooler LCD** stores nothing; without frames it shows the OMEN logo. So
after the hub stops the LCD shows the OMEN logo, whatever the Default says.
The Default's picture is what it shows first when the hub starts.

## Approaches considered

1. **A device hub with one module per device (chosen).** The core gets a
   device interface; OMEN-PC wraps today's LcdStreamer / Lights / RamLights
   unchanged; IQUNIX-KEYBOARD is a new module with its own small VIA driver
   over hidraw. One code path on both systems; settings grouped per device.
   Biggest refactor, but it is the shape asked for, and a third device is
   one module.
2. **The keyboard as one more light in today's lights code, window
   regrouped.** Least change, but the "hub" would only be a look; the
   keyboard either goes through OpenRGB (which does not list this model) or
   is bolted onto the case-light code. Rejected.
3. **A separate program per device, the window talking to each.** More
   background services, double the Batocera install work, the master auto
   switch has to coordinate several programs. Rejected.

## How it is built (decided)

### Names

| Today | RGB-Hub |
|---|---|
| repo folder `~/Source/OmenRGB-Controller` | `~/Source/RGB-Hub` |
| Python package `omenrgb` | `rgbhub` |
| GNOME extension `omenrgb@konfus-dev.github.io` | `rgbhub@konfus-dev.github.io` |
| `omenrgb.service`, `omenrgb-ram.socket/.service` | `rgbhub.service`, `rgbhub-ram.socket/.service` |
| `~/.config/omenrgb/` | `~/.config/rgbhub/` |
| Batocera service `omen_lights` (OMEN_LIGHTS) | `rgb_hub` (RGB_HUB) |
| `/userdata/system/configs/omen-lights/` | `/userdata/system/configs/rgb-hub/` |
| EmuBox component `omen-lights`, `startmenu_omen.py`, menu block "OMEN LIGHTING" | `rgb-hub`, `startmenu_rgbhub.py`, "RGB HUB" |

The installers remove the old units/extension/service and carry the old
settings, config folders (LCD override folder, pictures, OpenRGB config,
the recorded OpenRGB program) over once. Device ids are the owner's own
names: `OMEN-PC`, `IQUNIX-KEYBOARD`.

The GitHub repository is renamed by the owner (GitHub keeps a redirect);
this session has no push access.

### Layers

Same four layers as `docs/design.md`; the change is in the core:

```
 platforms (gnome.py, batocera.py) -- build --> Hub([OmenPc(...), IqunixKeyboard(...)])
 Hub: settings, auto (what is in focus -> a Look), timers, status, default-on-stop
 each Device: start / stop / apply(settings for me, look or None) / show_default / status
```

- `core/device.py`: the device interface (a Protocol, like
  `interfaces.py`). Every device has an id, a label, `connected`, its
  controls described for the front end, a way to be told its settings and
  the current auto Look, a slow tick (reconnect), and `show_default()`.
- `core/hub.py` (from today's `Service`): owns the settings store, the
  Looks, the focus subject, sleep/wake, status. Hands each device its part.
- `devices/omen_pc.py`: today's LCD, lights and RAM code behind the device
  interface. Behaviour unchanged.
- `devices/iqunix_keyboard.py` + `backends/via.py`: the keyboard. It reuses
  the lights code's shape (a lighting client with one device, one zone
  "All keys") so fades and the service-drawn Breathing (`lights._Glow`)
  work the same as on the case lights. `via.py` sends only the commands
  listed above.
- Platforms pick backends per system as today. The keyboard backend is the
  same on both (plain hidraw, standard library only).

### Settings

Lights settings that are already per lighting device (`device-modes`,
`zone-colours`, `device-speeds`, `device-min-brightness`,
`device-max-brightness`) stay as they are; the keyboard is one more entry.
What was global becomes per device:

| Today | RGB-Hub |
|---|---|
| `lights-enabled`, `lights-brightness` | `device-enabled`, `device-brightness`: dicts by device id (OMEN-PC's covers case + RAM as today) |
| `auto-enabled` | `auto-devices`: dict device id -> bool. The master switch is not stored: it shows on when every device is on, and flipping it sets them all. |
| `auto-lights`, `auto-lcd` | kept, as OMEN-PC's sub-switches |
| — | `default-look`: the saved Default (below) |

A one-time migration on first start maps old keys to new ones
(`auto-enabled: true` -> every device on, etc.). Batocera keeps its
"auto on by default" default.

### Default look

- **What it is**: a saved snapshot of every device's own (non-auto)
  settings: on/off, brightness, effect, colours, speed, min/max, and for
  OMEN-PC the LCD picture and fit. Stored in `default-look`.
- **How it is set**: a "Save current look as Default" button on the Auto
  tab (and an EmuBox menu row), which copies each device's current own
  settings. A "Show Default now" button applies it, so the owner can check
  it.
- **When it is used**: on a clean stop the hub shows the Default on every
  device and waits until it is delivered (RAM writes take ~65 ms each)
  before exiting. Then, per device, it stores the look where the device
  can: the keyboard via `id_custom_save`; the case board by being left in
  its own Static mode with the Default colours if testing shows it keeps
  that; the RAM only if a known register stores it.
- Until a Default is saved, today's built-in look is the Default.
- Stop paths that must trigger it: SIGTERM from systemd (log-out, shutdown,
  reboot, `systemctl --user stop`), the Batocera service's `stop`, Batocera
  shutdown/reboot. Ordering: on Ubuntu the hub must stop before the
  OpenRGB server and the root RAM helper (unit `Before=`/`After=` set
  accordingly). Sleep keeps today's behaviour (lights off), not the
  Default.
- A crash does not run it (nothing can); the restarted hub sets things
  right again.

### Status (the front-end contract)

`status` becomes a list of devices, each: id, label, connected, which
controls it has (LCD, light groups with today's `_describe` fields), and
its auto state. The GNOME window and EmuBox's menu build their tabs/blocks
from it, so they do not hard-code devices. `core/settings_schema.py` stays
the one list of keys; the GSettings schema test keeps checking it.

### Front ends

- **GNOME**: rename the extension; tabs as in Goals. The system-menu button
  becomes "RGB Hub" with the master auto switch.
- **EmuBox (Batocera)**, repo `~/Source/Batocera/EmuBox`: the component is
  renamed and its menu (`startmenu_omen.py` and the Guide menu rows in
  `components/console/files/lib/guide.py`) is grouped per device, with the
  master auto, per-device auto and Save Default rows. It is offered when
  either device is present (add `320f:5055` to `usb_devices`). Its docs
  (`docs/details.md`, `docs/extending.md`) and `setups/omen-desktop.json`
  are updated.
- `frontends/batocera/README.md` lists the new settings.

### Testing

- Unit tests as today (pytest, ruff): the device interface, the hub with
  fake devices, the migration, the VIA packet builder against the bytes
  above, default-on-stop ordering.
- With the owner watching: keyboard brightness range and effect names,
  colour and Breathing on the keyboard, Default on stop on Ubuntu (log out,
  reboot into Windows) and Batocera, the case board keeping its colour.
  Extension changes need a re-login (Wayland).

## Left to the builder

- Exact class and file names inside the layers above, and how far
  `service.py` is split.
- Effect names for the keyboard once the numbers are matched on the
  hardware (unknown numbers shown as "Effect N").
- The GNOME window's exact rows and wording, following today's style.
- How the installers detect and migrate the old install.
- Commit order. Work on `dev`; `main` only once both systems are tested.

## Open questions (builder check, 2026-11-15; to settle with the owner on resume)

1. **EmuBox menu: built from status, or a fixed device list?** On Batocera
   `status` is memory-only (`json_store.VOLATILE`), and ES reads
   `es_features_emubox.cfg` only at start. Likely answer: fixed list
   (OMEN-PC + IQUNIX-KEYBOARD, "Not connected" when absent).
2. **Default with a hub-drawn effect (Breathing, keyboard colours)**: what
   does each device keep after the hub exits: its own native effect,
   Static, or is Breathing not allowed in the Default?
3. **"Show Default now"**: is it a temporary preview or does it copy the
   Default into the current settings? Does every start show the Default
   first, or does the LCD just show the OMEN logo until the hub runs?
4. **MacWinBuntu**: `steps/hardware.sh`, `capture.sh`,
   `lib/dconf_filter.py`, `settings/dconf-omenrgb.ini`, `settings/dconf.ini`
   use the old names, repo and dconf path. Should the rename include it?
   (Memory says desktop customizations belong there, so probably yes.)
5. **Keyboard writes and its memory**: test before any animation that
   `id_custom_set_value` does not save on this firmware (write, unplug,
   check that the old look comes back). If it does save, the keyboard only
   gets its own effects, with no hub-drawn fades.
6. **EmuBox before the push**: `component.json` fetches
   `github.com/Konfus-dev/OmenRGB-Controller` (sibling
   `OmenRGB-Controller`). Is a local checkout only, until the owner pushes,
   acceptable? What is the new GitHub repo name?

## Decisions on the open questions (2026-11-19)

The owner asked to go ahead with implementing, so these were settled by the
builder (Claude) and are open to change:

1. **EmuBox menu: fixed device list** (OMEN-PC, IQUNIX-KEYBOARD), not built
   from status.
2. **Breathing in the Default**: the case board's Breathing is drawn by the
   hub, so on stop those lights are left in Static with the Default colours.
   The RAM and the keyboard run their own effects, which they keep.
3. **"Load"** makes the Default the current settings (it does not switch
   auto off). Start-up shows the normal settings / auto look straight away;
   the LCD shows the OMEN logo until the hub runs.
4. **MacWinBuntu updated** (repo name, dconf path, extension id).
5. **No hub-drawn animation on the keyboard**, whatever the firmware does
   with sets: only changed values are sent, on change. Whether a set
   survives an unplug is still to be seen on the hardware.
6. **GitHub name `RGB-Hub`**; until the owner renames the GitHub repo and
   pushes, EmuBox installs from the local checkout.

Also decided: the first Default is the look the owner had when the old
settings were carried over; the master auto switch is not stored.
