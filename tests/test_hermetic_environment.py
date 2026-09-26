from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings


def test_qsettings_never_touches_the_real_registry() -> None:
    settings = QSettings("Codex", "Image Triage")
    assert settings.format() == QSettings.Format.IniFormat
    assert Path(settings.fileName()).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())


def test_app_data_and_logs_are_redirected_to_temp() -> None:
    temp_root = Path(tempfile.gettempdir()).resolve()
    for name in ("LOCALAPPDATA", "APPDATA", "IMAGE_TRIAGE_LOG_DIR"):
        assert Path(os.environ[name]).resolve().is_relative_to(temp_root), name


def test_real_mask_engine_worker_cannot_be_spawned() -> None:
    with pytest.raises(AssertionError, match="mask_engine_worker"):
        subprocess.Popen(["python", "-m", "image_triage.mask_engine_worker"])


def test_standard_paths_resolve_inside_the_sandbox() -> None:
    from PySide6.QtCore import QStandardPaths

    temp_root = Path(tempfile.gettempdir()).resolve()
    for location in (
        QStandardPaths.StandardLocation.AppDataLocation,
        QStandardPaths.StandardLocation.GenericDataLocation,
        QStandardPaths.StandardLocation.CacheLocation,
    ):
        assert Path(QStandardPaths.writableLocation(location)).resolve().is_relative_to(temp_root), location
