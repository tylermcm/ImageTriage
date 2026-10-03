"""The hidden menu bar's shortcuts must all be adopted onto the window, every time.

The classic menu bar is hidden (the top bar's Menu button replaces it), and Qt does not fire the
shortcuts of a hidden menu's actions, so ``MainWindow._adopt_menu_bar_shortcuts`` registers every
menu action that has a shortcut on the window itself.

It used to remember which actions it had visited by ``id(action)`` of the PySide wrappers that
``QMenu.actions()`` hands back. Those wrappers are temporary: once one is dropped, Python can give
its address to the wrapper of a *different* action, which was then skipped as "already visited".
Which shortcuts got lost therefore varied from run to run (Compare/C, Clear Filters, Details View /
Grid View and Save Current Workspace Preset were seen missing in an unmodified build). The synthetic
test below makes that reuse likely on purpose; the real-window test checks the user-visible
invariant, which holds on every run now.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt, qInstallMessageHandler
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget

from image_triage.window import MainWindow


def _menu_actions_with_shortcuts(menu_bar) -> tuple[list, list]:
    """Every shortcut-bearing leaf action under ``menu_bar``, found with strong references held.

    Returns ``(actions, keep_alive)``; ``keep_alive`` pins every wrapper so no address is reused
    while the walk is still running."""
    keep_alive: list = []
    found: list = []
    pending = list(menu_bar.actions())
    while pending:
        action = pending.pop()
        keep_alive.append(action)
        submenu = action.menu()
        if submenu is not None:
            pending.extend(submenu.actions())
        elif not action.isSeparator() and action.shortcuts():
            found.append(action)
    return found, keep_alive


class _Host(QMainWindow):
    """Just enough of MainWindow for the adopt method (``menuBar``, ``addAction``, ``actions``)."""


def _build_host(menu_count: int, actions_per_menu: int, nesting: int) -> tuple[_Host, set[str]]:
    host = _Host()
    expected: set[str] = set()
    for menu_index in range(menu_count):
        menu = host.menuBar().addMenu(f"Menu {menu_index}")
        for depth in range(nesting):
            for action_index in range(actions_per_menu):
                text = f"m{menu_index} d{depth} a{action_index}"
                # No Python reference is kept: the wrapper is dropped straight away, as in the app.
                menu.addAction(text).setShortcut(QKeySequence("Ctrl+Alt+K"))
                expected.add(text)
            menu.addAction(f"m{menu_index} d{depth} no shortcut")  # must not be adopted
            menu.addSeparator()
            menu = menu.addMenu(f"Submenu {depth}")
    return host, expected


def test_every_shortcut_in_a_large_nested_menu_tree_is_adopted_every_time() -> None:
    QApplication.instance() or QApplication([])
    for round_number in range(25):
        host, expected = _build_host(menu_count=6, actions_per_menu=20, nesting=4)
        MainWindow._adopt_menu_bar_shortcuts(host)
        adopted = {action.text() for action in QWidget.actions(host)}
        missing = sorted(expected - adopted)
        assert not missing, f"round {round_number}: {len(missing)} shortcut action(s) not adopted, e.g. {missing[:5]}"
        assert adopted <= expected, "an action without a shortcut was adopted"
        host.deleteLater()


def test_adopting_twice_does_not_register_anything_twice() -> None:
    QApplication.instance() or QApplication([])
    host, expected = _build_host(menu_count=3, actions_per_menu=8, nesting=2)
    MainWindow._adopt_menu_bar_shortcuts(host)
    once = QWidget.actions(host)
    MainWindow._adopt_menu_bar_shortcuts(host)
    assert len(QWidget.actions(host)) == len(once) == len(expected)
    host.deleteLater()


@pytest.fixture
def fresh_window(dialogs):
    """A new real window per test, built the way startup builds it.

    Not the suite's shared window: other tests re-apply shortcut overrides on that one (which puts
    F11 back on the Zen Mode action that startup clears), and showing it would disturb the layout
    state the top-bar tests rely on."""
    from tests.test_preview_lazy_build import _fresh_window

    with _fresh_window() as window:
        yield window


def test_real_window_adopts_every_menu_bar_shortcut(fresh_window) -> None:
    window = fresh_window
    actions, keep_alive = _menu_actions_with_shortcuts(window.menuBar())
    assert len(actions) > 20, "the real menu tree should carry dozens of shortcuts"
    adopted = QWidget.actions(window)
    # The palette entry is the one deliberate exception (see the next test).
    expected = [action for action in actions if action is not window.actions.open_command_palette]
    missing = sorted({action.text() for action in expected if action not in adopted})
    assert not missing, f"menu actions with shortcuts that the window did not adopt: {missing}"
    del keep_alive


def test_the_palette_action_is_not_adopted_because_its_key_has_its_own_shortcut(fresh_window) -> None:
    window = fresh_window
    assert window.actions.open_command_palette.shortcuts(), "the menu entry still shows its key"
    assert window.actions.open_command_palette not in QWidget.actions(window)
    assert window._command_palette_shortcut_main is not None


def test_no_two_window_level_shortcuts_share_a_key(fresh_window) -> None:
    """Qt fires neither of two same-context shortcuts that share a key ("Ambiguous shortcut
    overload"), so adopting every menu shortcut must not collide with the window's QShortcuts."""
    window = fresh_window
    owners: dict[str, list[str]] = {}
    for action in QWidget.actions(window):
        if action.isEnabled() and action.shortcutContext() != Qt.ShortcutContext.WidgetShortcut:
            for sequence in action.shortcuts():
                owners.setdefault(sequence.toString(), []).append(f"action {action.text()!r}")
    for shortcut in window.findChildren(QShortcut):
        if shortcut.isEnabled() and shortcut.parent() is window:
            owners.setdefault(shortcut.key().toString(), []).append(f"QShortcut {shortcut.key().toString()}")
    clashes = {key: names for key, names in owners.items() if key and len(names) > 1}
    assert not clashes, f"keys claimed by more than one window-level shortcut: {clashes}"


def test_ctrl_k_opens_the_palette_with_every_menu_shortcut_adopted(fresh_window, monkeypatch) -> None:
    window = fresh_window
    opened: list[str] = []
    monkeypatch.setattr(window._command_palette, "open", lambda context="main": opened.append(context))
    logged: list[str] = []
    previous = qInstallMessageHandler(lambda mode, context, message: logged.append(message))
    try:
        window.show()
        window.activateWindow()
        QApplication.setActiveWindow(window)
        QApplication.processEvents()
        QTest.keyClick(window, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
        QApplication.processEvents()
    finally:
        qInstallMessageHandler(previous)
        window.hide()
    assert not [line for line in logged if "Ambiguous shortcut" in line], logged
    assert opened == ["main"], "Ctrl+K did not open the command palette"
