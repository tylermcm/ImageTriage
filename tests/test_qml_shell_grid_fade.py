"""The fade from the bottom of the grid to above the floating button bar, and the setting that picks its look."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, QObject, QSettings
from PySide6.QtGui import QGuiApplication

from image_triage.qml_shell.settings_model import SettingsModel
from test_qml_shell import _load_main, pump, qapp, rows  # noqa: F401  (the same helpers and application fixture)


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = str(tmp_path / "settings.ini")
    monkeypatch.setattr("image_triage.app_identity.user_settings", lambda parent=None: QSettings(path, QSettings.Format.IniFormat))
    return path


def test_it_is_soft_for_real_users_and_off_for_the_sample_page(qapp, store):
    real = SettingsModel(parent=qapp)
    real.load()
    assert real.gridFade == "soft"
    sample = SettingsModel(parent=qapp, memory=True)
    sample.load()
    assert sample.gridFade == "off"                      # the grid mockup has no fade by default, so the pixel checks hold


def test_each_choice_sets_the_height_and_off_hides_it(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path)
    engine.rootContext().contextProperty("photos").setRows(rows(12), "/p")
    window.show()
    settings = engine.rootContext().contextProperty("settings")
    fade = window.findChild(QObject, "gridFade")
    seen = {}
    for kind in ("soft", "deep", "dark", "off"):
        settings.set("gridFade", kind)
        pump(qapp, 0.2)
        seen[kind] = (fade.property("visible"), fade.property("height"))
    window.close()
    assert seen == {"soft": (True, 150), "deep": (True, 210), "dark": (True, 170), "off": (False, 150)}
