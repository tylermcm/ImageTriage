from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QSettings


def test_qsettings_never_touches_the_real_registry() -> None:
    settings = QSettings("Codex", "Image Triage")
    assert settings.format() == QSettings.Format.IniFormat
    assert Path(settings.fileName()).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())


def test_direct_registry_path_qsettings_never_touches_the_real_registry() -> None:
    # image_triage/app_identity.py constructs QSettings via a direct native
    # registry path (not the two-arg organization/application form above);
    # the sandbox must catch that pattern too.
    settings = QSettings(r"HKEY_CURRENT_USER\Software\Image Triage", QSettings.Format.NativeFormat)
    assert settings.format() == QSettings.Format.IniFormat
    assert Path(settings.fileName()).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())


def test_app_data_and_logs_are_redirected_to_temp() -> None:
    temp_root = Path(tempfile.gettempdir()).resolve()
    for name in ("LOCALAPPDATA", "APPDATA", "IMAGE_TRIAGE_LOG_DIR"):
        assert Path(os.environ[name]).resolve().is_relative_to(temp_root), name


def test_standard_paths_resolve_inside_the_sandbox() -> None:
    from PySide6.QtCore import QStandardPaths

    temp_root = Path(tempfile.gettempdir()).resolve()
    for location in (
        QStandardPaths.StandardLocation.AppDataLocation,
        QStandardPaths.StandardLocation.GenericDataLocation,
        QStandardPaths.StandardLocation.CacheLocation,
    ):
        assert Path(QStandardPaths.writableLocation(location)).resolve().is_relative_to(temp_root), location
