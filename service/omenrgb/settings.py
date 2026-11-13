# SPDX-License-Identifier: GPL-3.0-or-later
"""Opens the settings shared with the GNOME Shell extension.

The schema lives in the extension's folder (extension/.../schemas), so it is
loaded from that directory instead of the system-wide schema list.
"""

from pathlib import Path

from gi.repository import Gio

from .constants import SCHEMA_ID


def load_settings(schema_dir: Path, backend: Gio.SettingsBackend | None = None) -> Gio.Settings:
    """`backend` is only for tests; leave it out to use the real settings."""
    if not (Path(schema_dir) / "gschemas.compiled").exists():
        raise FileNotFoundError(
            f"No compiled settings schema in {schema_dir}. Run install/install-user.sh first."
        )
    source = Gio.SettingsSchemaSource.new_from_directory(
        str(schema_dir), Gio.SettingsSchemaSource.get_default(), False
    )
    schema = source.lookup(SCHEMA_ID, False)
    if schema is None:
        raise FileNotFoundError(
            f"Schema {SCHEMA_ID} not found in {schema_dir}. Run install/install-user.sh first."
        )
    return Gio.Settings.new_full(schema, backend, None)
