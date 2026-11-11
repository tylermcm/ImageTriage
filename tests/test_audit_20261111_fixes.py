"""Regressions for the second batch of fixes in docs/audit_20261111.md."""
from __future__ import annotations

import importlib
import time

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QGuiApplication

from image_triage import ai_platform
from image_triage.qml_shell import photos as photos_module
from image_triage.qml_shell.photos import PhotoModel


@pytest.fixture(scope="module")
def qapp():
    return QCoreApplication.instance() or QGuiApplication([])


def _wait(app, done, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not done():
        app.processEvents()
        time.sleep(0.01)
    assert done()


def test_a_folder_that_cannot_be_read_reports_why_instead_of_looking_empty(qapp, tmp_path, monkeypatch):
    def denied(folder):
        raise PermissionError(13, "denied")

    monkeypatch.setattr(photos_module, "scan_folder", denied)
    photos = PhotoModel(parent=qapp)
    photos.loadFolder(str(tmp_path))
    _wait(qapp, lambda: not photos.loading and photos.loadError != "")
    assert photos.loadError == "Access to this folder is denied." and photos.shown == 0

    monkeypatch.undo()                                      # the next folder reads fine: the error clears
    (tmp_path / "ok").mkdir()
    (tmp_path / "ok" / "a.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    photos.loadFolder(str(tmp_path / "ok"))
    _wait(qapp, lambda: not photos.loading and photos.shown == 1)
    assert photos.loadError == ""


def test_a_failed_gpu_detection_is_asked_again_and_a_found_card_is_remembered(monkeypatch):
    answers = iter([None, "NVIDIA Test Card", "should not be asked"])
    monkeypatch.setattr(ai_platform, "_gpu_name", None)
    monkeypatch.setattr(ai_platform, "_query_nvidia_gpu_name", lambda: next(answers))
    assert ai_platform.nvidia_gpu_name() is None            # the driver tool timed out
    assert ai_platform.nvidia_gpu_name() == "NVIDIA Test Card"
    assert ai_platform.nvidia_gpu_name() == "NVIDIA Test Card"


@pytest.mark.parametrize("script", ["setup_msi.py", "setup_linux.py"])
def test_every_module_the_freeze_scripts_force_include_exists(script):
    from pathlib import Path
    import re

    text = (Path(__file__).resolve().parents[1] / script).read_text(encoding="utf-8")
    includes = text[text.index('"includes"'):text.index('"excludes"')]     # excludes may name retired modules on purpose
    for name in sorted(set(re.findall(r'"((?:image_triage|aiculler)(?:\.\w+)+)"', includes))):
        assert importlib.util.find_spec(name) is not None, f"{script} includes {name}, which does not exist"
