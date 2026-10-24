"""The QML shell's command palette: the Widgets palette's ranking, shortcuts and recents, and the overlay itself in QML."""
from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QCoreApplication, QObject, QSettings, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface
from PySide6.QtTest import QTest

from image_triage.qml_shell import app as shell_app
from image_triage.qml_shell import palette_model as pm
from image_triage.qml_shell.palette_model import PaletteModel
from image_triage.qml_shell.settings_model import SettingsModel
from image_triage.qml_shell.theme import Theme

QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Software)


@pytest.fixture(scope="module")
def qapp():
    return QCoreApplication.instance() or QGuiApplication([])


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


def titles(rows):
    return [r["pre"] + r["hit"] + r["post"] for r in rows if r["kind"] == "row"]


def test_a_query_is_ranked_by_the_widgets_palettes_own_function(qapp, store):
    from image_triage.ui.command_palette import _normalize_query, _rank_commands

    model = PaletteModel(memory=True, parent=qapp)
    for query in ("rename", "fo", "theme", "ai", "x"):
        rows = model.results(query, "main")
        expected = [c.title for _k, c in _rank_commands(model._commands, _normalize_query(query), tuple(model._recent))][:80]
        assert titles(rows) == expected, query


def test_empty_query_lists_recents_first_then_sections_in_order(qapp, store):
    model = PaletteModel(memory=True, parent=qapp)
    rows = model.results("", "main")
    assert rows[0] == {"kind": "hdr", "label": "Recent"}
    headers = [r["label"] for r in rows if r["kind"] == "hdr"]
    assert headers[:4] == ["Recent", "File", "Edit", "Tools"]
    assert len(titles(rows)) == len(model._commands)
    recent = [r["id"] for r in rows[1:4]]
    assert recent == list(pm._DEMO_RECENT)


def test_a_match_is_marked_and_a_miss_has_no_rows(qapp, store):
    model = PaletteModel(memory=True, parent=qapp)
    first = model.results("rename", "main")[0]
    assert (first["pre"], first["hit"], first["post"]) == ("Batch ", "Rename", "")
    assert model.results("zzq", "main") == []
    assert model.countText("rename", "main") == "2 of 53" and model.countText("", "main") == "53 commands"


def test_preview_context_offers_only_the_commands_that_act_on_a_photo(qapp, store):
    model = PaletteModel(memory=True, parent=qapp)
    sections = {c.section for c in model._visible("preview")}
    assert sections <= set(pm._PREVIEW_SECTIONS) and "Review" in sections and "Library" not in sections


def test_shortcuts_come_from_the_registry_and_follow_overrides(qapp, store):
    from image_triage.ui.shortcuts import save_shortcut_overrides

    save_shortcut_overrides({"open_folder": "Ctrl+Shift+O"})
    model = PaletteModel(parent=qapp)
    row = next(r for r in model.results("open folder", "main") if r.get("id") == "file.open_folder")
    assert row["keys"] == ["Ctrl", "Shift", "O"]
    row = next(r for r in model.results("mark winner", "main") if r.get("id") == "review.accept_selection")
    assert row["keys"] == ["W"]


def test_running_a_command_remembers_it_under_the_widgets_key(qapp, store):
    model = PaletteModel(parent=qapp)
    seen = []
    model.executed.connect(seen.append)
    model.run("edit.undo")
    model.run("file.open_folder")
    model.run("edit.undo")
    assert seen == ["edit.undo", "file.open_folder", "edit.undo"]
    assert QSettings(store, QSettings.Format.IniFormat).value(pm.RECENT_KEY, [], list) == ["edit.undo", "file.open_folder"]
    assert PaletteModel(parent=qapp)._recent == ["edit.undo", "file.open_folder"]
    assert titles(model.results("", "main"))[:2] == ["Undo", "Open Folder"]


def test_toggle_state_shows_beside_the_command_and_counts_for_ranking(qapp, store):
    model = PaletteModel(memory=True, parent=qapp)
    model.setState("review.compare_mode", "On")
    row = next(r for r in model.results("compare", "main") if r["id"] == "review.compare_mode")
    assert row["state"] == "On"
    model.setState("review.compare_mode", "")
    assert next(r for r in model.results("compare", "main") if r["id"] == "review.compare_mode")["state"] == ""


def test_palette_opens_filters_navigates_runs_and_closes_in_qml(qapp, tmp_path, store):
    from PySide6.QtQml import QQmlApplicationEngine
    from image_triage.cache import DiskThumbnailCache
    from image_triage.qml_shell.filters_model import FiltersModel
    from image_triage.qml_shell.photos import PhotoModel
    from image_triage.qml_shell.thumbs import ThumbService

    palette = PaletteModel(memory=True, parent=qapp)
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(str(i) for i in items))
    photos = PhotoModel(parent=qapp)
    objects = (("theme", Theme(parent=qapp)), ("photos", photos), ("advFilters", FiltersModel(photos, memory=True, parent=qapp)), ("settings", SettingsModel(memory=True, parent=qapp)),
               ("cmdPalette", palette), ("thumbs", ThumbService(DiskThumbnailCache(tmp_path / "cache"), parent=qapp)), ("startup", ""))
    for name, obj in objects:
        engine.rootContext().setContextProperty(name, obj)
    engine.addImportPath(str(shell_app.QML_DIR))
    engine.load(QUrl.fromLocalFile(str(shell_app.QML_DIR / "Main.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    window.setWidth(1440)
    window.setHeight(900)
    window.show()
    pump(qapp, 0.4)
    card = window.findChild(QObject, "commandPalette")
    assert card is not None and card.property("shown") is False

    ran = []
    palette.executed.connect(ran.append)
    QTest.keyClick(window, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    pump(qapp, 0.4)
    assert card.property("shown") is True
    for ch in "rename":
        QTest.keyClick(window, ch)
    pump(qapp, 0.2)
    items = card.property("items").toVariant() if hasattr(card.property("items"), "toVariant") else card.property("items")
    assert [i["id"] for i in items] == ["tools.batch_rename", "edit.rename_selection"]
    QTest.keyClick(window, Qt.Key.Key_Down)
    pump(qapp, 0.1)
    assert card.property("cur") == 1
    QTest.keyClick(window, Qt.Key.Key_Down)           # wraps
    pump(qapp, 0.1)
    assert card.property("cur") == 0
    QTest.keyClick(window, Qt.Key.Key_Up)
    QTest.keyClick(window, Qt.Key.Key_Return)
    pump(qapp, 0.4)
    assert ran == ["edit.rename_selection"] and card.property("shown") is False

    QTest.keyClick(window, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    pump(qapp, 0.3)
    assert card.property("shown") is True and card.property("query") == ""     # opens clean
    QTest.keyClick(window, Qt.Key.Key_Escape)
    pump(qapp, 0.4)
    assert card.property("shown") is False
    assert not [w for w in warnings if "ReferenceError" in w or "TypeError" in w or "is not a type" in w or "unavailable" in w], warnings
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)
