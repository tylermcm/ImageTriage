"""The QML shell's advanced filters: the Widgets app's own query, wording, presets and saved-search format, and what the shell applies."""
from __future__ import annotations

import json
import time

import pytest
from PySide6.QtCore import QCoreApplication, QObject, QSettings, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface

from image_triage.filtering import (
    FileTypeFilter,
    ReviewStateFilter,
    active_filter_labels,
    builtin_filter_presets,
    deserialize_saved_filter_preset,
)
from image_triage.models import FilterMode
from image_triage.qml_shell import app as shell_app
from image_triage.qml_shell.pocketdrop_model import PocketDropModel
from qml_support import collection_models
from image_triage.qml_shell.handoff_model import HandoffModel
from image_triage.qml_shell.transfer_model import TransferModel
from image_triage.qml_shell import filters_model as fm
from image_triage.qml_shell.batch_model import BatchModel
from image_triage.qml_shell.filters_model import FiltersModel
from image_triage.qml_shell.photos import PhotoModel
from image_triage.qml_shell.theme import Theme

QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Software)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication   # the shell is a widgets application: the popout is a Widgets window
    return QCoreApplication.instance() or QApplication([])


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = str(tmp_path / "settings.ini")
    monkeypatch.setattr("image_triage.app_identity.user_settings", lambda parent=None: QSettings(path, QSettings.Format.IniFormat))
    return path


def pump(app, seconds=0.2):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def photos(qapp):
    model = PhotoModel(parent=qapp)
    decisions = ["win", "keep", "rej", ""]
    model.setRows([
        {"path": f"/x/p{i}.{'nef' if i % 2 else 'jpg'}", "name": f"p{i}", "ext": "NEF" if i % 2 else "JPG", "raw": bool(i % 2),
         "decision": decisions[i % 4], "rating": i % 6, "ai": 70} for i in range(12)
    ], "/x")
    return model


def test_built_in_presets_are_the_widgets_apps_own(qapp, store):
    model = FiltersModel(photos(qapp), parent=qapp)
    assert [p["name"] for p in model.presets()["builtin"]] == [p.name for p in builtin_filter_presets()]
    assert model.presets()["mine"] == []


def test_chips_use_the_widgets_wording_in_its_order(qapp, store):
    model = FiltersModel(photos(qapp), parent=qapp)
    model.set("ftype", "RAW")
    model.set("review", "Unreviewed")
    model.set("camera", "Sony")
    model.set("rating", 3)
    model.set("isoMin", "3200")
    model.set("focMax", "50")
    model.set("after", "2026-01-02")
    model.set("conf", 0.5)
    chips = [c["label"] for c in model.chips()]
    assert chips == active_filter_labels(model._query())
    assert 'Camera "Sony"' in chips and "Rating >= 3" in chips and "ISO >= 3200" in chips and "Focal <= 50mm" in chips


def test_picking_a_preset_fills_the_form_and_editing_deselects_it(qapp, store):
    model = FiltersModel(photos(qapp), parent=qapp)
    model.pick("b:Unreviewed RAW")
    assert (model.get("ftype"), model.get("review"), model.selected) == ("RAW", "Unreviewed", "b:Unreviewed RAW")
    model.set("rating", 2)
    assert model.selected == ""
    model.pick("b:Near Duplicates")
    assert model.get("quick") == FilterMode.DUPLICATES.value and model.get("ftype") == FileTypeFilter.ALL.value


def test_a_bad_date_is_flagged_and_ignored(qapp, store):
    model = FiltersModel(photos(qapp), parent=qapp)
    model.set("after", "2026-13-01")
    assert model.dateBad("after") and model._query().captured_after is None
    model.set("after", "2026-02-03")
    assert not model.dateBad("after") and model._query().captured_after.isoformat() == "2026-02-03"
    model.set("before", "")
    assert not model.dateBad("before")


def test_saved_searches_use_the_widgets_key_and_format_and_overwrite_by_name(qapp, store):
    model = FiltersModel(photos(qapp), parent=qapp)
    assert not model.saveAs("Nothing")                      # a search with no filters is not saved, as in the Widgets app
    model.set("review", "Accepted")
    model.set("rating", 4)
    assert model.saveAs("Keepers") and model.clash("keepers")
    raw = QSettings(store, QSettings.Format.IniFormat).value(fm.SAVED_KEY, "", str)
    payload = json.loads(raw)
    preset = deserialize_saved_filter_preset(payload[0])
    assert preset.name == "Keepers" and preset.query.review_state == ReviewStateFilter.ACCEPTED and preset.query.min_rating == 4
    model.set("rating", 5)
    assert model.saveAs("keepers")                           # same name, other case: replaced, not duplicated
    assert [p["name"] for p in FiltersModel(photos(qapp), parent=qapp).presets()["mine"]] == ["keepers"]
    model.remove("keepers")
    assert FiltersModel(photos(qapp), parent=qapp).presets()["mine"] == []


def test_apply_filters_the_grid_by_decision_rating_and_format(qapp, store):
    grid = photos(qapp)
    model = FiltersModel(grid, parent=qapp)
    assert model.matchCount == 12
    model.set("review", "Accepted")
    assert model.matchCount == sum(1 for r in grid._all if r["decision"] in ("win", "keep"))
    assert grid.shown == 12 and model.dirty                  # nothing changes until Apply
    model.apply()
    assert grid.shown == model.matchCount and not model.dirty
    model.set("ftype", "RAW")
    model.set("rating", 3)
    model.apply()
    assert all(r["raw"] and r["rating"] >= 3 and r["decision"] in ("win", "keep") for r in grid._rows)
    model.reset()
    model.apply()
    assert grid.shown == 12
    model.set("quick", "Winners Only")
    model.apply()
    assert {r["decision"] for r in grid._rows} == {"win"}
    grid.clearFilters()
    model.clearApplied()
    assert grid.shown == 12 and not model.dirty and model.filterCount == 0


def test_filters_the_shell_cannot_act_on_are_counted(qapp, store):
    model = FiltersModel(photos(qapp), parent=qapp)
    model.set("review", "Accepted")
    assert model.unsupported == 0
    model.set("camera", "Sony")
    model.set("isoMin", "800")
    model.set("ai", "Top Picks")
    model.set("ftype", "TIFF")
    assert model.unsupported == 4
    assert model.begin() is None and model.unsupported == 0   # opening shows what the grid is using


def test_filters_window_opens_from_the_palette_and_closes_in_qml(qapp, tmp_path, store):
    from PySide6.QtQml import QQmlApplicationEngine
    from image_triage.cache import DiskThumbnailCache
    from image_triage.qml_shell.palette_model import PaletteModel
    from image_triage.qml_shell.settings_model import SettingsModel
    from image_triage.qml_shell.thumbs import ThumbService

    grid = photos(qapp)
    palette = PaletteModel(memory=True, parent=qapp)
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(str(i) for i in items))
    for name, obj in (("theme", Theme(parent=qapp)), ("photos", grid), ("settings", SettingsModel(memory=True, parent=qapp)), ("cmdPalette", palette),
                      ("advFilters", FiltersModel(grid, memory=True, parent=qapp)), ("batch", BatchModel(grid, memory=True, parent=qapp)), *collection_models(grid, qapp), ("handoff", HandoffModel(grid, memory=True, parent=qapp)), ("transfers", TransferModel(grid, memory=True, parent=qapp)), ("thumbs", ThumbService(DiskThumbnailCache(tmp_path / "c"), parent=qapp)),
                      ("startup", "")):
        engine.rootContext().setContextProperty(name, obj)
    engine.addImportPath(str(shell_app.QML_DIR))
    engine.load(QUrl.fromLocalFile(str(shell_app.QML_DIR / "Main.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    window.setWidth(1440)
    window.setHeight(900)
    window.show()
    pump(qapp, 0.4)
    card = window.findChild(QObject, "filtersWindow")
    assert card is not None and card.property("shown") is False
    palette.run("search.advanced_filters")
    pump(qapp, 0.5)
    assert card.property("shown") is True
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    QTest.keyClick(window, Qt.Key.Key_Escape)
    pump(qapp, 0.4)
    assert card.property("shown") is False
    assert not [w for w in warnings if "ReferenceError" in w or "TypeError" in w or "is not a type" in w or "unavailable" in w], warnings
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)
