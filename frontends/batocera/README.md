# OMEN Lights on Batocera

Switch it on or off under **Main menu > System Settings > Services >
OMEN_LIGHTS**.

While it is on:

- **In the menus** the PC matches your menu theme. If the theme has an
  `omen-lights/` folder, the LCD shows what is in it (an animation or a
  picture); otherwise it shows the theme's menu background. The lights take
  that picture's main colour.
- **While a game runs** the LCD shows that game's artwork (its logo if one
  was scraped, otherwise its box or a screenshot) and the lights take the
  artwork's main colour. Games without artwork keep the menu look.

## Settings

Edit `settings.json` in this folder (from Batocera it is
`/userdata/system/configs/omen-lights/settings.json`). Changes apply as soon
as the file is saved. Only list what you want to change, for example:

```json
{
    "lights-brightness": 60,
    "lcd-brightness": 70,
    "auto-idle-image": "/userdata/system/configs/omen-lights/menu.png",
    "auto-idle-colour": "#00AA00",
    "device-modes": {"HP Omen 30L": "Breathing"}
}
```

| Setting | Meaning | Default |
|---|---|---|
| `auto-enabled` | Follow the running game | `true` |
| `auto-lights` / `auto-lcd` | Let the lights / the LCD follow | `true` |
| `auto-idle-image` | Picture shown in the menus | the theme's background |
| `auto-idle-colour` | Light colour in the menus, `"#RRGGBB"` | from the picture |
| `lights-enabled` | Case lights on or off | `true` |
| `lights-brightness`, `lcd-brightness` | Percent | `100`, `80` |
| `device-modes` | Effect per device: `Static` or `Breathing` (RAM also `Rainbow`, which ignores colours) | `Static` |
| `lcd-image` | Picture shown when auto mode is off | none |

## If something is wrong

The log is `/userdata/system/logs/omen-lights.log`; the previous run's is
kept beside it as `omen-lights.log.old`. For a detailed log, create an empty
file `/userdata/system/configs/omen-lights/debug` and switch the service off
and on. Remove it afterwards: the detailed log grows quickly.

If the program stops by itself it is started again automatically, and a
mistake in `settings.json` is reported in the log while the previous settings
stay in force.

RAM lights need a small kernel module that is built for one exact Batocera
kernel (the one for Batocera 43.1 is included). After a Batocera update the
RAM may go back to its own pattern until a new module is built; see
`frontends/batocera/modules/README.md` in the project.

When the PC sleeps, the LCD and lights are switched off, and they come back
when it wakes.

## The theme's LCD folder

A folder named `omen-lights` inside a theme's folder decides what the LCD
shows in the menus while that theme is active. Without it, the theme's menu
background is shown. The folder may hold:

- **Several JPEG files: an animation.** They must be LCD-ready (480x480, at
  most 60,000 bytes each) and are played in name order, in a loop
  (`000.jpg`, `001.jpg`, ...). A text file `frame_ms` may give the time per
  frame in milliseconds (default 100).
- **One picture** (PNG, JPEG, WebP, ...): shown as a still.
- **One animated file** (GIF, animated WebP or PNG): played as an animation.

The screen is round, so keep what matters inside the circle.
