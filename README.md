# OmenRGB-Controller

Control the lighting and the CPU-cooler LCD of an **HP OMEN 45L** desktop
without HP's Windows software. It runs on a GNOME desktop (developed on
Ubuntu) and on Batocera, and is built so that other systems and front ends
can be added without touching the core.

This is an unofficial hobby project, not affiliated with HP. Use it at your
own risk.

## What it does

- **Cooler LCD:** show any image you choose on the round screen on the CPU
  cooler, and set its brightness.
- **Case lights:** set the colour of the front logo and each front fan, pick
  an effect (static, breathing, colour cycle, ...), set brightness, or turn
  them all off.
- **RAM lights:** a colour per stick, or a rainbow or breathing effect, with
  the same on/off switch and brightness as the case lights.
- **Effects you can tune:** moving effects get a speed slider, and breathing
  gets "dimmest" and "brightest" sliders, on the case as well as the RAM.
- **Auto mode:** optionally match the PC to the app you are using. The lights
  take the main colour of the focused app's icon and the LCD shows the icon.
- **Smooth and light:** colours glide and the LCD crossfades when something
  changes, everything goes dark when the PC sleeps, and the service does
  almost nothing while the lights are steady.
- **One place:** an "OMEN Lights" button next to Wi-Fi and Bluetooth that
  opens a settings window for all of it.
- Your choices come back by themselves after a reboot.

## What it does not do (yet)

- The RGB ring on the pump head, animated GIFs or live stats on the LCD,
  fan speeds.
- Other OMEN models. It may work on them, but it has only been tested on
  the machine below.

## Tested on

| | |
|---|---|
| Computer | OMEN by HP 45L Gaming Desktop GT22-3xxx (board 8D2C) |
| System | Ubuntu 26.04, GNOME Shell 50, Wayland |
| Lighting board | USB `103c:84fd` "HP TracerLED", driven through OpenRGB 0.9 (shown there as "HP Omen 30L") |
| Cooler LCD | USB `03f0:7397` "LCD-PUMP", firmware V3.05.024 |
| RAM | 2 x HP `HP60U40S8HA-16FRE` (Kingston Fury DDR5), set through the BIOS |

OpenRGB lists seven light zones for the case. On the 45L only the front logo
and the three front fans have lights, so the rest are hidden.

## Install on a GNOME desktop

```sh
git clone https://github.com/Konfus-dev/OmenRGB-Controller.git
cd OmenRGB-Controller

# 1. Once, as root: installs OpenRGB and acpi_call, gives your user access
#    to the LCD, and installs the small RAM lighting helper.
#    It is short; read it first.
sudo bash install/setup.sh

# 2. As yourself: sets up the background service and the extension.
bash install/install-user.sh

# 3. Log out and back in, then:
gnome-extensions enable omenrgb@konfus-dev.github.io
```

The "OMEN Lights" button now appears in the top-right menu. Press it to open
the settings window (or bring it to the front if it is already open).

## How it works

```
 GNOME Shell extension  --settings-->  omenrgb service  -->  cooler LCD (USB)
 (button + settings window)            (Python, runs at  -->  OpenRGB server --> case lights
                                        login)           -->  root helper --> BIOS --> RAM lights
```

The extension never touches hardware; it only saves settings. The service
watches those settings and does the work. The LCD forgets its picture unless
it is re-sent, so the service sends it twice a second (this costs almost no
CPU).

Only one piece runs as root: a small helper that passes RAM lighting
commands to the BIOS, the same way HP's own software does. It refuses
everything else. Talking to these RAM sticks directly, as other tools do,
jams their control bus on this board until the next reboot, so nothing here
does that. More detail is in [docs/design.md](docs/design.md).

## Batocera

From a Linux PC, with the Batocera drive plugged in and its `SHARE`
partition mounted:

```sh
sudo bash install/install-batocera.sh /path/to/SHARE
```

Then boot Batocera and switch **OMEN_LIGHTS** on under **Main menu > System
Settings > Services**. In the menus the LCD shows the Batocera logo; while a
game runs it shows that game's artwork, and the lights take its colour.
Settings are a small JSON file; see
[frontends/batocera/README.md](frontends/batocera/README.md).

## How the code is organised

The code is meant to be easy to change and to extend to other systems.

| Folder | What lives there |
|---|---|
| `service/omenrgb/core/` | What the LCD and lights should do. No operating-system or hardware-library imports. `interfaces.py` describes what it needs handed in; `settings_schema.py` lists every setting. |
| `service/omenrgb/backends/` | One file per way of reaching hardware: `hidraw.py` (LCD), `openrgb.py` and `tracer.py` (case lights), `ram_bios.py` (RAM). |
| `service/omenrgb/platforms/` | One file per system (`gnome.py`, `batocera.py`) that picks backends and wires in that system's settings storage, timers and "what is in focus". |
| `service/omenrgb/omen45l.py` | Facts about this PC model: zone names, hidden zones, colour correction. |
| `frontends/` | What you see and touch: the GNOME Shell extension, the Batocera service switch and game hook. A front end only reads and writes settings. |
| `helper/` | The small root helper for RAM lights on desktop Linux. |
| `install/`, `systemd/` | Set-up scripts and service definitions. |

**Adding a system** (another distro, Windows): write
`platforms/<name>.py` with a `main()` that builds a `Service` from the
pieces that fit, and list it in `service/omenrgb/__main__.py`. Reuse the
backends that work there; add one where they do not.

**Adding a front end**: anything that can write the settings in
`core/settings_schema.py` and read the `status` report is a front end.

On the GNOME desktop:
after editing the service: `systemctl --user restart omenrgb`;
after editing the settings list: `bash install/install-user.sh`;
after editing the root helper: `sudo bash install/setup.sh`;
after editing the extension's menu button: log out and back in.

To run the tests:

```sh
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e 'service[dev]'
.venv/bin/pytest service && .venv/bin/ruff check service
```

## Troubleshooting

```sh
journalctl --user -u omenrgb -e           # what the service is doing
journalctl --user -u openrgb-server -e    # what OpenRGB is doing
journalctl -u omenrgb-ram -e              # what the RAM helper is doing
systemctl --user status omenrgb openrgb-server

# What the service currently sees:
gsettings --schemadir ~/.local/share/gnome-shell/extensions/omenrgb@konfus-dev.github.io/schemas \
    get org.gnome.shell.extensions.omenrgb status
```

- **LCD shows the OMEN logo:** no image chosen, or the service is not
  running.
- **Lights do not react:** OpenRGB is not running. If it has just been
  restarted it can need a minute or two before it accepts connections.
- **RAM lights do not react:** check `systemctl status omenrgb-ram.socket`
  and that `lsmod | grep acpi_call` shows the module.
- **Auto mode does nothing:** it needs the extension, so log out and back in
  after installing. Apps without an icon in your icon theme keep the normal
  look, as does this project's own settings window.
- **After a full power-off** the LCD shows the logo, and the RAM its
  rainbow, until you log in.
- **RAM temperature sensors stopped reporting:** something talked to the RAM
  directly (for example a full OpenRGB scan). Reboot to recover.

## Uninstall

```sh
gnome-extensions disable omenrgb@konfus-dev.github.io
systemctl --user disable --now omenrgb openrgb-server
rm ~/.config/systemd/user/omenrgb.service ~/.config/systemd/user/openrgb-server.service
rm ~/.local/share/gnome-shell/extensions/omenrgb@konfus-dev.github.io
rm -r ~/.local/share/omenrgb
rm -r ~/.config/omenrgb
sudo systemctl disable --now omenrgb-ram.socket omenrgb-ram.service
sudo rm /etc/systemd/system/omenrgb-ram.socket /etc/systemd/system/omenrgb-ram.service
sudo rm -r /usr/local/lib/omenrgb
sudo rm /etc/udev/rules.d/70-omen-lcd.rules /etc/modules-load.d/i2c-dev.conf \
        /etc/modules-load.d/omenrgb-acpi-call.conf
```

## Credits

- The LCD protocol was worked out by the author of
  [omen-led-lcd](https://github.com/Xedecimal/omen-led-lcd). This project
  uses the facts in its protocol notes and none of its code.
- [OpenRGB](https://openrgb.org) drives the case lights, and its Kingston
  Fury driver documents the RAM lighting registers.

## Licence

GPL-3.0-or-later. See `LICENSE`.
