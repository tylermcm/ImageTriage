"""Offscreen checks for placement guards; never launch the native editor."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication, QWidget

from scripts import _desktop


def test_secondary_monitor_required_before_window_creation(monkeypatch):
    primary = object()
    app = SimpleNamespace(screens=lambda: [primary], primaryScreen=lambda: primary)
    monkeypatch.setenv(_desktop.MONITOR_ENV, "secondary")
    with pytest.raises(RuntimeError, match="no test window was opened"):
        _desktop._check_screen(app)


def test_secondary_and_named_monitor_selection(monkeypatch):
    primary = SimpleNamespace(name=lambda: "primary")
    secondary = SimpleNamespace(name=lambda: "other")
    app = SimpleNamespace(screens=lambda: [primary, secondary], primaryScreen=lambda: primary)
    monkeypatch.setenv(_desktop.MONITOR_ENV, "secondary")
    assert _desktop._check_screen(app) is secondary
    monkeypatch.setenv(_desktop.MONITOR_ENV, "other")
    assert _desktop._check_screen(app) is secondary


def test_physical_placement_does_not_require_virtual_desktop_marker(monkeypatch):
    app = QApplication.instance() or QApplication([])
    screen = app.primaryScreen()  # offscreen QScreen; no visible platform window
    monkeypatch.setenv(_desktop.MONITOR_ENV, "secondary")
    monkeypatch.setattr(_desktop, "_check_screen", lambda _app: screen)
    monkeypatch.setattr(_desktop, "_home", lambda: None)
    prevent = Mock()
    monkeypatch.setattr(_desktop, "_prevent_activation", prevent)
    monkeypatch.setattr(_desktop, "_send_to_back", Mock())
    widget = QWidget()
    try:
        _desktop.place(widget)
        assert not widget.isVisible()
        assert widget.screen() == screen
        assert screen.availableGeometry().contains(widget.geometry())
        assert widget.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        assert widget.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
        prevent.assert_called_once_with(int(widget.winId()))
    finally:
        widget.close()
        widget.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()


def test_missing_monitor_does_not_create_window_handle(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv(_desktop.MONITOR_ENV, "nonexistent-screen-for-test")
    widget = QWidget()
    try:
        assert not widget.testAttribute(Qt.WidgetAttribute.WA_WState_Created)
        with pytest.raises(RuntimeError, match="unavailable"):
            _desktop.place(widget)
        assert not widget.testAttribute(Qt.WidgetAttribute.WA_WState_Created)
        assert not widget.isVisible()
    finally:
        widget.close()


def test_protected_preview_never_requests_activation(monkeypatch):
    from image_triage.preview import FullScreenPreview

    app = QApplication.instance() or QApplication([])
    preview = FullScreenPreview()
    preview.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
    activate, raise_window, focus = Mock(), Mock(), Mock()
    monkeypatch.setattr(preview, "activateWindow", activate)
    monkeypatch.setattr(preview, "raise_", raise_window)
    monkeypatch.setattr(preview, "setFocus", focus)
    try:
        preview.show_entries([])
        preview._editor_engaged = True
        preview.close_editor()
        activate.assert_not_called()
        raise_window.assert_not_called()
        focus.assert_not_called()
    finally:
        preview.close()
        app.processEvents()
