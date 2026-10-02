"""Single QSettings identity for the app (WI-3.1).

Historically the app wrote to three different registry identities:
``Codex\\Image Triage`` (the default, from ``QCoreApplication``'s
organisation/application name), a stray ``ImageTriage\\ImageTriage`` used
only by ``ui/shortcuts.py``, and whatever a bare ``QSettings()`` call picked
up. All settings now live under one key, ``HKCU\\Software\\Image Triage``
(no doubled ``Image Triage\\Image Triage`` segment), read through
:func:`user_settings`. ``QCoreApplication``'s organisation name was left as
"Codex" for a while after this landed, since it also drives
``QStandardPaths`` (the thumbnail cache, ``decisions.sqlite3``, and more) and
moving those needed a real data migration, not just a rename - WI-3.6 added
that migration (see ``app_data_migration.py``), so ``main.py`` now sets the
organisation name to "Image Triage" too.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QObject, QSettings

_REGISTRY_KEY = r"HKEY_CURRENT_USER\Software\Image Triage"
_FALLBACK_ORG = "Image Triage"
_FALLBACK_APP = "Image Triage"

_LEGACY_IDENTITIES: tuple[tuple[str, str], ...] = (
    ("Codex", "Image Triage"),
    ("ImageTriage", "ImageTriage"),
)

_MIGRATION_MARKER_KEY = "_settings_migrated_from_legacy"


def user_settings(parent: QObject | None = None) -> QSettings:
    """The single, current settings store for the app."""
    if sys.platform == "win32":
        return QSettings(_REGISTRY_KEY, QSettings.Format.NativeFormat, parent)
    return QSettings(_FALLBACK_ORG, _FALLBACK_APP, parent)


def legacy_settings_sources() -> list[QSettings]:
    """Read-only handles to identities settings used to live under."""
    return [QSettings(organization, application) for organization, application in _LEGACY_IDENTITIES]


def migrate_legacy_settings_once() -> None:
    """Copy any settings still only in a legacy identity into the current one.

    Safe to call on every startup: a marker key makes it a no-op after the
    first run, and a key already present in the current store is never
    overwritten (so settings changed since migration are not clobbered).
    """
    target = user_settings()
    if target.value(_MIGRATION_MARKER_KEY, False, bool):
        return
    for legacy in legacy_settings_sources():
        for key in legacy.allKeys():
            if key == _MIGRATION_MARKER_KEY:
                continue
            if not target.contains(key):
                target.setValue(key, legacy.value(key))
    target.setValue(_MIGRATION_MARKER_KEY, True)
    target.sync()
