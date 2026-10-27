"""The minimize, maximize and close buttons of the frameless window, pressed with real mouse clicks (not by calling the function behind them)."""
from __future__ import annotations

import pytest
from PySide6.QtCore import Q_ARG, QMetaObject, QPoint, Qt
from PySide6.QtGui import QWindow
from PySide6.QtTest import QTest

from test_qml_shell import _load_main, pump, qapp  # noqa: F401  (the same helpers and application fixture)

WIDTH = 1440
MINIMIZE, MAXIMIZE, CLOSE = 115, 69, 23             # the centres of the three 46 px buttons, counted from the window's right edge


@pytest.fixture
def shell(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path)
    window.setWidth(WIDTH)
    window.setHeight(900)
    pump(qapp, 0.5)
    yield window, warnings
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)


def click(qapp, window, from_right, y=20):
    x = window.width() - from_right                  # the buttons follow the right edge when the window changes size
    QTest.mouseMove(window, QPoint(x, y))
    pump(qapp, 0.1)
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, y))
    pump(qapp, 0.4)


def test_the_maximize_button_maximizes_and_restores(qapp, shell):
    window, warnings = shell
    assert window.visibility() == QWindow.Visibility.Windowed
    click(qapp, window, MAXIMIZE)
    assert window.visibility() == QWindow.Visibility.Maximized
    click(qapp, window, MAXIMIZE)
    assert window.visibility() == QWindow.Visibility.Windowed
    assert not [w for w in warnings if "TopBar" in w], warnings


def test_the_minimize_button_minimizes(qapp, shell):
    window, warnings = shell
    click(qapp, window, MINIMIZE)
    assert window.visibility() == QWindow.Visibility.Minimized


def test_the_close_button_closes_the_window(qapp, shell):
    window, warnings = shell
    click(qapp, window, CLOSE)
    assert not window.isVisible()


def test_library_zen_mode_is_fullscreen_and_escape_restores_window(qapp, shell):
    window, warnings = shell
    original_geometry = window.geometry()
    assert QMetaObject.invokeMethod(window, "runCommand", Q_ARG("QVariant", "view.zen_mode"))
    pump(qapp, 0.4)
    assert window.visibility() == QWindow.FullScreen
    QTest.keyClick(window, Qt.Key_Escape)
    pump(qapp, 0.4)
    assert window.visibility() == QWindow.Windowed
    assert window.geometry() == original_geometry
