"""Settings > Shortcuts now moves the shell's keys: what is saved is what the grid, the viewer and the window's shortcuts answer to."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, QPointF, QSettings, Qt
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
    assert m.seq("reject_selection") == "R" and m.keyIs("reject_selection", Qt.Key.Key_R, 0)


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
    assert bindings["review.delete_selection"] == "Del"                                     # the shell's default for a row the registry leaves empty
    assert "review.accept_selection" not in bindings and "review.compare_mode" not in bindings      # the grid's own key / a command the shell does not have
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


def test_reject_defaults_custom_choices_and_cleared_bindings_survive_reopening(qapp, store):
    model = SettingsModel()
    assert model.seq("reject_selection") == "R"
    model.set("k:reject_selection", "X")
    model.set("k:accept_selection", "")
    assert model.save()
    reopened = SettingsModel()
    assert reopened.seq("reject_selection") == "X"
    assert reopened.seq("accept_selection") == ""
    assert not reopened.keyIs("accept_selection", Qt.Key_W, 0)
    reopened.resetSection("keys")
    assert reopened.save()
    assert SettingsModel().seq("reject_selection") == "R"


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
    photos.selectRange(0, 1)                                                                # several photos: Rename is the batch window (one photo is renamed on its card)
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


def click_item(qapp, window, item):
    assert item is not None and item.isVisible()
    position = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, position)
    pump(qapp, 0.3)


def visual_item(window, name):
    pending = [window.contentItem()]
    while pending:
        candidate = pending.pop()
        if candidate.objectName() == name:
            return candidate
        pending.extend(candidate.childItems())


def test_shortcuts_guide_opens_the_editor_and_recorded_key_runs_the_action(qapp, tmp_path):
    engine, window, warnings, photos, settings, grid = open_window(qapp, tmp_path)
    reference = engine.rootContext().contextProperty("reference")
    reference.open("keys")
    pump(qapp, 0.4)
    click_item(qapp, window, window.findChild(QObject, "editShortcutsButton"))
    dialog = window.findChild(QObject, "settingsWindow")
    assert dialog.property("shown") and dialog.property("section") == "keys"
    search = dialog.findChild(QObject, "settingsSearch")
    click_item(qapp, window, search)
    for char in "Mark Winner":
        QTest.keyClick(window, ord(char.upper()), Qt.ShiftModifier if char.isupper() else Qt.NoModifier)
    pump(qapp, 0.3)
    editor = visual_item(window, "shortcutEditor_k:accept_selection")
    assert editor is not None, (search.property("text"), warnings, dialog.property("groups"))
    click_item(qapp, window, editor.findChild(QObject, "changeShortcutButton"))
    assert dialog.property("recording") == "k:accept_selection"
    # An already bound chord must be recorded instead of closing Settings.
    QTest.keyClick(window, Qt.Key_Comma, Qt.ControlModifier)
    pump(qapp, 0.2)
    assert dialog.property("shown") and settings.value("k:accept_selection") == "Ctrl+,"
    editor = visual_item(window, "shortcutEditor_k:accept_selection")
    click_item(qapp, window, editor.findChild(QObject, "changeShortcutButton"))
    QTest.keyClick(window, Qt.Key_J)
    pump(qapp, 0.2)
    assert settings.value("k:accept_selection") == "J"
    click_item(qapp, window, dialog.findChild(QObject, "saveSettingsButton"))
    assert settings.seq("accept_selection") == "J"
    assert settings.commandShortcut("review.accept_selection", "W") == "J"
    guide_rows = [row for category in reference._keys_view()["cats"] for row in category["rows"]]
    assert next(row for row in guide_rows if row["t"] == "Mark a winner")["keys"] == ["J"]
    palette = engine.rootContext().contextProperty("cmdPalette")
    assert next(row for row in palette.results("winner", "main") if row.get("id") == "review.accept_selection")["keys"] == ["J"]
    QTest.keyClick(window, Qt.Key_Escape)
    pump(qapp, 0.3)
    grid.forceActiveFocus()
    QTest.keyClick(window, Qt.Key_J)
    assert photos.get(1)["decision"] == "win"
    assert not warnings, warnings
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)


@pytest.mark.parametrize("key", [Qt.Key_W, Qt.Key_K, Qt.Key_R, Qt.Key_Right])
def test_culling_and_navigation_keep_the_current_photo_visible(qapp, tmp_path, key):
    engine, window, warnings, photos, settings, grid = open_window(qapp, tmp_path)
    photos.setRows([dict(r, decision="", rating=0) for r in rows(120)], "/p")
    pump(qapp, 0.2)
    grid.forceActiveFocus()
    grid.setProperty("currentIndex", 0)
    for _ in range(38):
        QTest.keyClick(window, key)
        pump(qapp, 0.005)
    pump(qapp, 0.2)
    current = grid.property("currentIndex")
    assert current == 38
    assert grid.property("contentY") > 0
    columns = round(grid.width() / grid.property("cellWidth"))
    top = (current // columns) * grid.property("cellHeight")
    assert top >= grid.property("contentY") - 1
    assert top + grid.property("cellHeight") <= grid.property("contentY") + grid.height() - 132 + 1
    # Rating after navigating still applies to the card that is in view.
    QTest.keyClick(window, Qt.Key_4)
    assert photos.get(current)["rating"] == 4
    assert not warnings, warnings
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)
