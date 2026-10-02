from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QStandardPaths


def app_data_root() -> Path:
    """The shared data root for the catalog, library, and AI-training stores.

    ``IMAGE_TRIAGE_APPDATA`` is a test-only escape hatch (set by several
    tests to sandbox this into a temp dir) and is checked first, unchanged
    from before. Otherwise this now goes through ``QStandardPaths`` instead
    of hand-rolling ``%APPDATA%\\ImageTriage`` from the environment (WI-3.6):
    that hand-rolled path was a fourth, independent identity, unrelated to
    QCoreApplication's organisation name and to the registry identity WI-3.1
    unified - and it bypassed this repo's test sandboxing for
    ``QStandardPaths`` (see ``tests/conftest.py``) whenever the
    ``IMAGE_TRIAGE_APPDATA`` override wasn't explicitly set. Existing data at
    the old hand-rolled location is migrated forward once by
    ``app_data_migration.migrate_legacy_app_data_once`` (see ``main.py``);
    the old location itself is left untouched.
    """
    override = os.environ.get("IMAGE_TRIAGE_APPDATA", "").strip()
    if override:
        root = Path(override) / "ImageTriage"
        root.mkdir(parents=True, exist_ok=True)
        return root
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    if base:
        root = Path(base)
        root.mkdir(parents=True, exist_ok=True)
        return root
    try:
        root = Path.home() / ".image-triage"
    except RuntimeError:
        root = Path.cwd() / ".image-triage"
    root.mkdir(parents=True, exist_ok=True)
    return root
