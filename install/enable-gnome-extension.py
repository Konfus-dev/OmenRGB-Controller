#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Put a GNOME Shell extension on GNOME's list of enabled extensions.

    python3 install/enable-gnome-extension.py <uuid> [--remove <old uuid>]...

`gnome-extensions enable` only works once GNOME Shell has loaded the
extension (after a log-in), so after a first install, or a rename to a new
id, the extension stayed off. GNOME reads org.gnome.shell enabled-extensions
at log-in, so this writes the id there itself: exactly once, with any old
ids taken off. It also takes the id off disabled-extensions, which wins over
the enabled list.

Standard library only; it talks to GSettings through the gsettings tool
($GSETTINGS if set, for tests).
"""

import ast
import os
import re
import subprocess
import sys

SCHEMA = "org.gnome.shell"
UUID = re.compile(r"^[A-Za-z0-9_.@-]+$")


def gsettings(*args: str) -> str:
    program = os.environ.get("GSETTINGS", "gsettings")
    return subprocess.run(
        [program, *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def parse_list(text: str) -> list[str]:
    """A GVariant string list as gsettings prints it: ['a', 'b'] or @as []."""
    text = text.removeprefix("@as").strip()
    value = ast.literal_eval(text)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"not a list of strings: {text!r}")
    return value


def format_list(items: list[str]) -> str:
    return "[" + ", ".join(f"'{item}'" for item in items) + "]"


def updated(current: list[str], uuid: str, remove: list[str]) -> list[str]:
    """`current` with `uuid` exactly once (where it was, else at the end)
    and none of `remove`."""
    result: list[str] = []
    for item in current:
        if item in remove or item in result:
            continue
        result.append(item)
    if uuid not in result:
        result.append(uuid)
    return result


def set_list(key: str, items: list[str]) -> None:
    gsettings("set", SCHEMA, key, format_list(items))


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    uuid, rest = argv[0], argv[1:]
    remove = []
    while rest:
        if rest[0] != "--remove" or len(rest) < 2:
            print(f"Unexpected arguments: {rest}", file=sys.stderr)
            return 2
        remove.append(rest[1])
        rest = rest[2:]
    for name in (uuid, *remove):
        if not UUID.match(name):
            print(f"Not an extension id: {name!r}", file=sys.stderr)
            return 2
    remove = [name for name in remove if name != uuid]

    try:
        enabled = parse_list(gsettings("get", SCHEMA, "enabled-extensions"))
        wanted = updated(enabled, uuid, remove)
        if wanted != enabled:
            set_list("enabled-extensions", wanted)

        disabled = parse_list(gsettings("get", SCHEMA, "disabled-extensions"))
        if uuid in disabled:
            set_list("disabled-extensions", [item for item in disabled if item != uuid])

        if gsettings("get", SCHEMA, "disable-user-extensions") == "true":
            print(
                "WARNING: GNOME has all user extensions switched off (the Extensions app's "
                "main switch). Switch them on for the RGB Hub button and auto mode.",
                file=sys.stderr,
            )
        if parse_list(gsettings("get", SCHEMA, "enabled-extensions")).count(uuid) != 1:
            raise ValueError("the list did not take the change")
    except (OSError, subprocess.CalledProcessError, ValueError, SyntaxError) as error:
        print(
            f"WARNING: could not switch the GNOME extension on ({error}).\n"
            f"         After logging in, run: gnome-extensions enable {uuid}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
