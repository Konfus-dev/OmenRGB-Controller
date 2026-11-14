# OMEN Lights on Batocera

Switch it on or off under **Main menu > System Settings > Services >
OMEN_LIGHTS**.

While it is on:

- **In the menus** the cooler LCD shows the Batocera logo and the case
  lights take its colour.
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
| `auto-idle-image` | Picture shown in the menus | Batocera logo |
| `auto-idle-colour` | Light colour in the menus, `"#RRGGBB"` | from the picture |
| `lights-enabled` | Case lights on or off | `true` |
| `lights-brightness`, `lcd-brightness` | Percent | `100`, `80` |
| `device-modes` | Effect per device: `Static` or `Breathing` | `Static` |
| `lcd-image` | Picture shown when auto mode is off | none |

## If something is wrong

The log is `/userdata/system/logs/omen-lights.log`.

RAM lights only work if the `acpi_call` kernel module is available for
Batocera's kernel; without it the RAM keeps its own pattern.
