"""Settings > Shortcuts now moves the shell's keys: what is saved is what the grid, the viewer and the window's shortcuts answer to."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, QSettings, Qt
from PySide6.QtTest import QTest

from image_triage.qml_shell import settings_model as sm
from image_triage.qml_shell.settings_model import SettingsModel
from test_qml_shell import _load_main, pump, qapp, rows  # noqa: F401  (the same helpers and application fixture)

CTRL, SHIFT, ALT, KEYPAD = (m.value for m in (Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.ShiftModifier, Qt.KeyboardModifier.AltModifier, Qt.KeyboardModifier.KeypadModifier))


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = str(tmp_path / "settings.ini")
    monkeypatch.setattr("image_triage.app_identity.user_settings", lambda parent=None: QSettings(path, QSettings.Format.IniFormat))
    return path


# -- the model --------------------------------------------------------------------------------------------------------------------

def test_the_widgets_defaults_are_in_force_until_a_row_is_changed(qapp, store):
    m = SettingsModel()
    assert m.seq("open_folder") == "Ctrl+O" and m.seq("accept_selection") == "W" and m.seq("batch_rename_selection") == "Ctrl+Shift+R"
    assert m.seq("open_preview") == "" and m.seq("no_such_action") == ""                  # a row with no default, and no row at all
    assert m.keyIs("accept_selection", Qt.Key.Key_W, 0) and not m.keyIs("accept_selection", Qt.Key.Key_K, 0)
    assert m.keyIs("batch_rename_selection", Qt.Key.Key_R, CTRL | SHIFT)
    assert not m.keyIs("accept_selection", Qt.Key.Key_W, SHIFT)      # Shift+W is another chord than W
    assert m.keyIs("accept_selection", Qt.Key.Key_W, KEYPAD)        # the keypad flag is not part of the chord


def test_a_saved_override_replaces_the_default_and_tells_the_window(qapp, store):
    m = SettingsModel()
    changed = []
    m.shortcutsChanged.connect(lambda: changed.append(m.shortcutRevision))
    m.set("k:accept_selection", "J")
    assert m.seq("accept_selection") == "W"                                                 # not until it is saved
    assert m.save()
    assert m.seq("accept_selection") == "J" and m.keyIs("accept_selection", Qt.Key.Key_J, 0) and not m.keyIs("accept_selection", Qt.Key.Key_W, 0)
    assert changed == [1]
    assert SettingsModel().seq("accept_selection") == "J"                                   # a new session reads the same file
    m.set("autoAdvance", False)
    assert m.save() and changed == [1]                                                      # saving something else leaves the keys alone


def test_the_bindings_are_the_shortcuts_the_shell_carries_out(qapp, store):
    m = SettingsModel()
    bindings = {b["cmd"]: b["seq"] for b in m.shortcutBindings}
    assert bindings["file.open_folder"] == "Ctrl+O" and bindings["edit.undo"] == "Ctrl+Z" and bindings["file.refresh_folder"] == "F5"
    assert bindings["tools.batch_rename"] == "Ctrl+Shift+R" and bindings["workflow.best_of"] == "Ctrl+Alt+B" and bindings["ai.workflow_center"] == "Ctrl+Shift+W"
    assert "review.delete_selection" not in bindings and "review.accept_selection" not in bindings    # no default key / the grid's own key
    m.set("k:undo", "Ctrl+Alt+U")
    m.set("k:refresh_folder", "")                                                           # a cleared row has no key
    m.set("k:delete_selection", "Ctrl+Backspace")
    assert m.save()
    bindings = {b["cmd"]: b["seq"] for b in m.shortcutBindings}
    assert bindings["edit.undo"] == "Ctrl+Alt+U" and "file.refresh_folder" not in bindings and bindings["review.delete_selection"] == "Ctrl+Backspace"


def test_a_row_cannot_take_a_key_the_shell_binds_itself(qapp, store):
    m = SettingsModel()
    m.set("k:accept_selection", "I")                                                        # the inspector's
    assert not m.save()
    assert "Show or hide the inspector" in m.conflict() and m.seq("accept_selection") == "W"
    m.set("k:accept_selection", "Return")
    assert not m.save() and "Open the photo" in m.conflict()
    m.set("k:accept_selection", "J")
    assert m.save()


def test_a_default_the_shell_also_uses_is_not_a_conflict(qapp, store):
    m = SettingsModel()
    assert m.seq("adapter_label_hero") == "1"                                               # the registry's 1 to 5 are the shell's ratings too
    m.set("k:undo", "Ctrl+Alt+U")
    assert m.save()                                                                         # saving another row is not refused because of them


# -- the window -------------------------------------------------------------------------------------------------------------------

def open_window(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path)
    context = engine.rootContext()
    photos, settings = context.contextProperty("photos"), context.contextProperty("settings")
    photos.setRows([dict(r, decision="", rating=0) for r in rows(6)], "/p")
    window.setWidth(1440)
    window.setHeight(900)
    window.show()
    pump(qapp, 0.5)
    grid = window.findChild(QObject, "photoGrid")
    grid.forceActiveFocus()
    grid.setProperty("currentIndex", 1)
    pump(qapp, 0.1)
    return engine, window, warnings, photos, settings, grid


def test_the_grids_decision_keys_follow_the_saved_shortcuts(qapp, tmp_path):
    engine, window, warnings, photos, settings, grid = open_window(qapp, tmp_path)
    QTest.keyClick(window, Qt.Key.Key_W)
    assert photos.get(1)["decision"] == "win"
    settings.set("k:accept_selection", "J")
    settings.set("k:reject_selection", "Ctrl+Alt+J")
    assert settings.save()
    pump(qapp, 0.2)
    grid.setProperty("currentIndex", 2)
    QTest.keyClick(window, Qt.Key.Key_W)                                                    # the old key now does nothing
    assert photos.get(2)["decision"] == ""
    QTest.keyClick(window, Qt.Key.Key_J)
    assert photos.get(2)["decision"] == "win"
    grid.setProperty("currentIndex", 3)
    QTest.keyClick(window, Qt.Key.Key_X)
    assert photos.get(3)["decision"] == ""
    QTest.keyClick(window, Qt.Key.Key_J, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)    # a chord with modifiers works in the grid too
    assert photos.get(3)["decision"] == "rej"
    grid.setProperty("currentIndex", 3)                                                      # a reject goes on to the next photo
    QTest.keyClick(window, Qt.Key.Key_K)                                                    # an unchanged key is as it was
    assert photos.get(3)["decision"] == "keep"
    assert not [w for w in warnings if "ReferenceError" in w or "TypeError" in w or "Required property" in w], warnings
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)


def test_the_windows_shortcuts_follow_the_saved_shortcuts(qapp, tmp_path):
    engine, window, warnings, photos, settings, grid = open_window(qapp, tmp_path)
    card = window.findChild(QObject, "batchWindow")
    assert card is not None and card.property("shown") is False
    QTest.keyClick(window, Qt.Key.Key_F2)                                                   # Rename Image: its default key
    pump(qapp, 0.5)
    assert card.property("shown") is True and card.property("mode") == "rename"
    QTest.keyClick(window, Qt.Key.Key_Escape)
    pump(qapp, 0.5)
    assert card.property("shown") is False
    settings.set("k:rename_selection", "Ctrl+Alt+Y")
    assert settings.save()
    pump(qapp, 0.3)                                                                         # the Instantiator makes the new Shortcut
    QTest.keyClick(window, Qt.Key.Key_F2)
    pump(qapp, 0.4)
    assert card.property("shown") is False                                                  # F2 is free now
    QTest.keyClick(window, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
    pump(qapp, 0.5)
    assert card.property("shown") is True and card.property("mode") == "rename"
    assert not [w for w in warnings if "ReferenceError" in w or "TypeError" in w or "Required property" in w], warnings
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)
