"""The side panel and the inspector: drag to resize within a minimum and a maximum, remembered between runs."""
from __future__ import annotations

import pytest
from PySide6.QtCore import Q_ARG, QMetaObject, QObject, QPoint, QPointF, Qt
from PySide6.QtTest import QTest

from image_triage.qml_shell.settings_model import SettingsModel
from test_qml_shell import _load_main, pump, qapp  # noqa: F401  (the same helpers and application fixture)


def open_window(qapp, tmp_path, startup, width=1440, height=900):
    engine, window, warnings = _load_main(qapp, tmp_path, startup)
    window.setWidth(width)
    window.setHeight(height)
    QMetaObject.invokeMethod(window, "applyStartup", Q_ARG("QVariant", width))
    pump(qapp, 0.9)
    return engine, window, warnings


def close(qapp, engine, window):
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)


def test_the_widths_stay_between_their_minimum_and_maximum(qapp, tmp_path):
    engine, window, warnings = open_window(qapp, tmp_path, "tab=lib;panelW=100;inspW=900")
    assert window.property("panelWanted") == 260 and window.property("inspWanted") == 352        # too small, too big
    close(qapp, engine, window)
    engine, window, warnings = open_window(qapp, tmp_path, "tab=lib;panelW=700;inspW=100")
    assert window.property("panelWanted") == 300 and window.property("inspWanted") == 300
    close(qapp, engine, window)


def test_the_grid_keeps_its_room_when_the_window_is_narrow(qapp, tmp_path):
    engine, window, warnings = open_window(qapp, tmp_path, "tab=lib;panelW=300;inspW=352", width=1100, height=800)
    panel, insp = window.property("panelWanted"), window.property("inspWanted")
    assert panel >= 260 and insp >= 300
    assert panel + insp <= window.property("availSide") + 0.5 and insp < 352        # shrunk to fit, never below the minimums
    close(qapp, engine, window)


def test_the_default_widths_are_what_the_mockup_has(qapp, tmp_path):
    engine, window, warnings = open_window(qapp, tmp_path, "tab=lib")
    assert (window.property("panelWanted"), window.property("inspWanted")) == (300, 352)
    assert window.property("panelW") == 300 and window.property("inspW") == 352
    close(qapp, engine, window)


def test_dragging_the_edge_changes_the_width_and_it_is_remembered(qapp, tmp_path):
    engine, window, warnings = open_window(qapp, tmp_path, "tab=lib;panelW=270")
    handle = window.findChild(QObject, "panelResizer")
    assert handle is not None and handle.property("visible")
    edge = handle.mapToScene(QPointF(handle.width() / 2, 100)).toPoint()
    QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, edge)
    for step in range(1, 4):
        QTest.mouseMove(window, QPoint(edge.x() + step * 8, edge.y()))
        pump(qapp, 0.02)
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(edge.x() + 24, edge.y()))
    pump(qapp, 0.3)
    assert 275 <= window.property("panelWanted") <= 295, window.property("panelWanted")           # 270 + the 24 dragged, less the grab threshold
    assert window.property("panelW") == window.property("panelWanted")                              # followed the pointer without animating
    # dragging far past the maximum stops at it
    handle = window.findChild(QObject, "panelResizer")
    edge = handle.mapToScene(QPointF(handle.width() / 2, 100)).toPoint()
    QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, edge)
    for step in range(1, 30):
        QTest.mouseMove(window, QPoint(min(1400, edge.x() + step * 30), edge.y()))
        pump(qapp, 0.01)
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(1400, edge.y()))
    pump(qapp, 0.3)
    assert window.property("panelWanted") == 300                                                    # the ceiling is the default width
    close(qapp, engine, window)


def test_dragging_the_inspector_edge_to_the_left_widens_it(qapp, tmp_path):
    engine, window, warnings = open_window(qapp, tmp_path, "tab=;inspW=310")
    handle = window.findChild(QObject, "inspResizer")
    assert handle is not None
    edge = handle.mapToScene(QPointF(handle.width() / 2, 100)).toPoint()
    QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, edge)
    for step in range(1, 5):
        QTest.mouseMove(window, QPoint(edge.x() - step * 8, edge.y()))
        pump(qapp, 0.02)
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(edge.x() - 32, edge.y()))
    pump(qapp, 0.3)
    assert 320 <= window.property("inspWanted") <= 342, window.property("inspWanted")
    close(qapp, engine, window)


def test_a_closed_panel_has_no_handle_and_a_double_click_resets(qapp, tmp_path):
    engine, window, warnings = open_window(qapp, tmp_path, "tab=;panelW=280")
    assert not window.findChild(QObject, "panelResizer").property("visible")
    close(qapp, engine, window)
    engine, window, warnings = open_window(qapp, tmp_path, "tab=lib;panelW=280")
    handle = window.findChild(QObject, "panelResizer")
    assert window.property("panelWanted") == 280
    QMetaObject.invokeMethod(handle, "reset")
    pump(qapp, 0.3)
    assert window.property("panelWanted") == 300
    close(qapp, engine, window)


def test_the_compact_window_has_no_handles(qapp, tmp_path):
    engine, window, warnings = open_window(qapp, tmp_path, "tab=lib", width=800, height=700)
    assert not window.findChild(QObject, "panelResizer").property("visible")
    assert not window.findChild(QObject, "inspResizer").property("visible")
    close(qapp, engine, window)


def test_the_widths_are_stored_with_the_settings(qapp):
    store = SettingsModel(memory=True, parent=qapp)
    assert store.layoutWidth("panel", 300) == 300
    store.setLayoutWidth("panel", 410)
    assert store.layoutWidth("panel", 300) == 410 and store.layoutWidth("inspector", 352) == 352
    store.load()                                                         # resetting the settings page does not forget the layout
    assert store.layoutWidth("panel", 300) == 410


def test_the_real_store_round_trips(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    import image_triage.app_identity as identity

    qs = QSettings(str(tmp_path / "t.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(identity, "user_settings", lambda: qs)
    store = SettingsModel(memory=False, parent=qapp)
    assert store.layoutWidth("panel", 300) == 300
    store.setLayoutWidth("panel", 377)
    assert SettingsModel(memory=False, parent=qapp).layoutWidth("panel", 300) == 377
