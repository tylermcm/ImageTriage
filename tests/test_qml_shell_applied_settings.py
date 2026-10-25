"""Settings the QML shell now acts on: performance logging, the catalog summary, and Advance after Accept or Reject in the grid."""
from __future__ import annotations

import types

import pytest
from PySide6.QtCore import QObject, QSettings, Qt
from PySide6.QtTest import QTest

from image_triage.qml_shell.settings_model import SettingsModel
from test_qml_shell import _load_main, pump, qapp, rows  # noqa: F401  (the same helpers and application fixture)


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = str(tmp_path / "settings.ini")
    monkeypatch.setattr("image_triage.app_identity.user_settings", lambda parent=None: QSettings(path, QSettings.Format.IniFormat))
    return path


def test_saving_the_performance_log_switch_starts_and_stops_the_logger(qapp, store, monkeypatch):
    calls = []
    fake = types.SimpleNamespace(set_enabled=lambda enabled, reason="": calls.append((enabled, reason)))
    monkeypatch.setattr("image_triage.perf.perf_logger", lambda: fake)
    model = SettingsModel(parent=qapp)
    model.load()
    model.set("perfLog", True)
    assert model.save()
    assert calls == [(True, "settings")]
    model.set("perfLog", False)
    model.save()
    assert calls[-1] == (False, "settings")
    model.set("autoAdvance", False)                        # another setting changing does not touch the logger
    model.save()
    assert len(calls) == 2


def test_the_catalog_summary_is_the_catalogs_own_numbers(qapp, store, monkeypatch):
    stats = types.SimpleNamespace(error_message="", folder_count=7, record_count=1234, feature_count=56, last_indexed_at="2026-05-14 08:00")
    monkeypatch.setattr("image_triage.catalog.CatalogRepository", lambda *a, **k: types.SimpleNamespace(stats=lambda: stats))
    model = SettingsModel(parent=qapp)
    model.load()
    model.readCatalogSummary()
    text = model._catalog_text
    assert "Indexed image bundles: 1234" in text and "Indexed folders: 7" in text and "Catalog cache reads:" in text
    assert "12,408" not in model.text("catalogInfo")             # not the sample numbers any more


def test_a_catalog_that_cannot_be_read_says_so(qapp, store, monkeypatch):
    def broken(*a, **k):
        raise OSError("locked")

    monkeypatch.setattr("image_triage.catalog.CatalogRepository", broken)
    model = SettingsModel(parent=qapp)
    model.load()
    model.readCatalogSummary()
    assert "could not be read" in model._catalog_text


def test_the_grid_goes_on_after_a_winner_or_a_reject_when_the_setting_is_on(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path)
    context = engine.rootContext()
    model, settings = context.contextProperty("photos"), context.contextProperty("settings")
    model.setRows(rows(12), "/p")
    window.setWidth(1440)
    window.setHeight(900)
    window.show()
    pump(qapp, 0.5)
    grid = window.findChild(QObject, "photoGrid")
    grid.forceActiveFocus()
    settings.set("autoAdvance", True)
    grid.setProperty("currentIndex", 2)
    pump(qapp, 0.1)
    QTest.keyClick(window, Qt.Key.Key_W)
    assert grid.property("currentIndex") == 3
    QTest.keyClick(window, Qt.Key.Key_X)
    assert grid.property("currentIndex") == 4
    QTest.keyClick(window, Qt.Key.Key_K)                               # Keep stays
    assert grid.property("currentIndex") == 4
    settings.set("autoAdvance", False)
    QTest.keyClick(window, Qt.Key.Key_W)
    assert grid.property("currentIndex") == 4
    window.close()
    pump(qapp, 0.1)
