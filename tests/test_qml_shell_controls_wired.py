"""Controls the shell shows are connected to something, and commands act on the photo the person is looking at.

Covers the palette over the viewer, the rail's pins, the context menu's Open in editor, the one Delete shortcut, the grid's 0 and W keys,
the filters window in a narrow window, the keyboard on the shared controls, and the viewer keeping its zoom while stepping."""
from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Q_ARG, QMetaObject, QObject, QPoint, QUrl, Qt
from PySide6.QtQml import QQmlComponent
from PySide6.QtTest import QTest

from image_triage.qml_shell import app as shell_app
from image_triage.qml_shell import settings_model as sm
from image_triage.qml_shell.settings_model import SettingsModel
from test_qml_shell import _load_main, pump, qapp, rows  # noqa: F401  (the same helpers and application fixture)

QML = Path(shell_app.QML_DIR)


def source(name: str) -> str:
    return (QML / name).read_text(encoding="utf-8")


@pytest.fixture
def shell(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path)
    context = engine.rootContext()
    context.contextProperty("photos").setRows(rows(12), "/p")
    window.setWidth(1440)
    window.setHeight(900)
    window.show()
    pump(qapp, 0.4)
    yield window, context, qapp
    context.contextProperty("popout").close()
    window.close()
    pump(qapp, 0.1)


def run(shell, command):
    window, context, app = shell
    context.contextProperty("cmdPalette").executed.emit(command)
    pump(app, 0.05)


def focus_grid(shell, row):
    window, context, app = shell
    grid = window.findChild(QObject, "photoGrid")
    grid.setProperty("currentIndex", row)
    QMetaObject.invokeMethod(grid, "forceActiveFocus")
    pump(app, 0.1)
    return grid


# -- commands act on the photo that is shown ---------------------------------------------------------------------------------------

def test_a_command_over_the_viewer_acts_on_the_photo_it_shows(shell):
    window, context, app = shell
    photos, popout = context.contextProperty("photos"), context.contextProperty("popout")
    focus_grid(shell, 0)
    photos.selectRange(0, 2)                                    # photos picked in the grid behind the viewer are not what the viewer shows
    popout.open(5)
    pump(app, 0.2)
    before = [(photos.row_at(i)["decision"], photos.row_at(i)["rating"]) for i in range(3)]
    run(shell, "review.rate.2")
    run(shell, "review.reject_selection")
    assert (photos.row_at(5)["decision"], photos.row_at(5)["rating"]) == ("rej", 2)
    assert [(photos.row_at(i)["decision"], photos.row_at(i)["rating"]) for i in range(3)] == before
    assert window.property("commandRow") == 5
    popout.close()
    pump(app, 0.3)
    assert window.property("commandRow") == window.findChild(QObject, "photoGrid").property("currentIndex")


def test_a_command_the_shell_does_not_know_says_so(shell):
    window, context, app = shell
    toasts = context.contextProperty("toasts")
    before = len(toasts.items)
    run(shell, "no.such_command")
    assert len(toasts.items) == before + 1 and toasts.items[-1]["title"] == "That command is not available"


# -- the rail's pins and the context menu --------------------------------------------------------------------------------------------

def test_the_rail_has_no_pin_without_a_handler(shell):
    window, context, app = shell
    rail = window.findChild(QObject, "rail")
    keys = [m["k"] for m in rail.property("pinMeta").toVariant()]
    assert "compare" not in keys and "newfolder" not in keys
    text = source("Rail.qml")
    run_pin = text[text.index("function runPin(k) {"):text.index("Binding { target: UiState; property: \"railPin\"")]
    menus = ("review", "view", "filters", "sort", "columns")
    assert [k for k in keys if k not in menus and 'k === "%s"' % k not in run_pin] == []
    assert "UiState.compare" not in text + source("Main.qml") + source("UiState.qml")


def test_the_editor_and_shortcuts_pins_do_something(shell):
    window, context, app = shell
    popout, reference = context.contextProperty("popout"), context.contextProperty("reference")
    rail = window.findChild(QObject, "rail")
    focus_grid(shell, 4)
    QMetaObject.invokeMethod(rail, "runPin", Q_ARG("QVariant", "editor"))
    pump(app, 0.2)
    assert popout.isOpen and popout.row == 4                    # the editor is started from the viewer, on that photo
    popout.close()
    pump(app, 0.2)
    QMetaObject.invokeMethod(rail, "runPin", Q_ARG("QVariant", "shortcuts"))
    pump(app, 0.1)
    assert reference.view["window"] == "keys"
    reference.close()


def test_open_in_editor_from_the_context_menu_reaches_the_viewer(shell):
    window, context, app = shell
    menu, popout, toasts = context.contextProperty("ctxMenu"), context.contextProperty("popout"), context.contextProperty("toasts")
    menu.openPhoto(3)
    assert not any("Photoshop" in item.get("label", "") for item in menu.view["items"])
    menu.setSub("with")
    assert [item["key"] for item in menu.view["subItems"] if item.get("kind") == "row"] == ["with:default", "with:other"]
    menu.setSub("")
    before = len(toasts.items)
    menu.act("edit")
    pump(app, 0.2)
    assert popout.isOpen and popout.row == 3
    assert not any("connected" in item["title"] for item in toasts.items[before:])


# -- keys -----------------------------------------------------------------------------------------------------------------------------

def test_delete_is_one_shortcut_from_the_registry(qapp, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings

    path = str(tmp_path / "settings.ini")
    monkeypatch.setattr("image_triage.app_identity.user_settings", lambda parent=None: QSettings(path, QSettings.Format.IniFormat))
    assert 'sequence: "Delete"' not in source("Main.qml") and "Qt.Key_Delete" not in source("Viewer.qml")
    m = SettingsModel()
    assert m.seq("delete_selection") == "Del" and m.keyIs("delete_selection", Qt.Key.Key_Delete, 0)
    assert {b["cmd"]: b["seq"] for b in m.shortcutBindings}["review.delete_selection"] == "Del"
    m.set("k:delete_selection", "Ctrl+Backspace")
    assert m.save() and SettingsModel().seq("delete_selection") == "Ctrl+Backspace"
    assert [b["seq"] for b in SettingsModel().shortcutBindings if b["cmd"] == "review.delete_selection"] == ["Ctrl+Backspace"]      # one, not two
    m.set("k:delete_selection", "")                             # cleared: no key deletes, also after a restart
    assert m.save() and SettingsModel().seq("delete_selection") == ""
    again = SettingsModel()
    again.set("k:delete_selection", "Delete")                   # back on the default
    assert again.save() and SettingsModel().seq("delete_selection") == "Del"


def test_shortcuts_lists_only_what_the_shell_carries_out(qapp, tmp_path, monkeypatch):
    import re
    from PySide6.QtCore import QSettings

    path = str(tmp_path / "settings.ini")
    qs = QSettings(path, QSettings.Format.IniFormat)
    qs.setValue("shortcuts/compare_mode", "V")                  # saved by an earlier version for a row that is no longer listed
    qs.sync()
    monkeypatch.setattr("image_triage.app_identity.user_settings", lambda parent=None: QSettings(path, QSettings.Format.IniFormat))
    m = SettingsModel()
    listed = {f.id[2:] for f in m._fields.values() if f.type == "keys"}
    assert listed == set(sm.LISTED_ACTIONS)
    qml = "\n".join(p.read_text(encoding="utf-8") for p in QML.glob("*.qml"))
    asked = set(re.findall(r'settings\.(?:keyIs|seq)\("([a-z_]+)"', qml))
    bound = {action for action, _cmd in sm.BOUND_ACTIONS}
    assert listed == (bound | asked) & {row[0] for row in sm._shortcut_registry()}, (listed ^ (bound | asked))
    for gone in ("new_folder", "grid_view", "details_view", "compare_mode", "quick_rerank_ai_culling", "send_to_editor_pipeline", "tag_at_cursor"):
        assert gone not in listed
    main = source("Main.qml")
    body = main[main.index("function runCommand(id) {"):main.index("Connections { target: cmdPalette; function onExecuted(id)")]
    for action, command in sm.BOUND_ACTIONS:
        assert 'id === "%s"' % command in body, command        # every bound row reaches a command the shell has
    m.set("k:undo", "Ctrl+Alt+U")
    assert m.save()
    assert QSettings(path, QSettings.Format.IniFormat).value("shortcuts/compare_mode") == "V"      # what was stored for an unlisted row is left alone


def test_zero_takes_the_rating_off_in_the_grid(shell):
    window, context, app = shell
    photos = context.contextProperty("photos")
    focus_grid(shell, 5)
    assert photos.row_at(5)["rating"] == 5
    QTest.keyClick(window, Qt.Key.Key_0)
    pump(app, 0.1)
    assert photos.row_at(5)["rating"] == 0
    assert sm.FIXED_KEYS["0"] == "Clear the rating"


def test_a_press_that_clears_the_decision_does_not_move_on(shell):
    window, context, app = shell
    photos, settings = context.contextProperty("photos"), context.contextProperty("settings")
    assert settings.value("autoAdvance") is True
    grid = focus_grid(shell, 0)
    assert photos.row_at(0)["decision"] == "win"
    QTest.keyClick(window, Qt.Key.Key_W)                         # W on a winner takes the decision off
    pump(app, 0.1)
    assert photos.row_at(0)["decision"] == "" and grid.property("currentIndex") == 0
    QTest.keyClick(window, Qt.Key.Key_W)                         # W again marks it, and the grid moves on
    pump(app, 0.1)
    assert photos.row_at(0)["decision"] == "win" and grid.property("currentIndex") == 1


def test_the_viewer_keeps_its_zoom_while_stepping(shell):
    window, context, app = shell
    popout = context.contextProperty("popout")
    viewer = window.findChild(QObject, "viewer")
    popout.open(1)
    pump(app, 0.3)
    QMetaObject.invokeMethod(viewer, "setZoom", Q_ARG("QVariant", 1), Q_ARG("QVariant", 200), Q_ARG("QVariant", 200))
    assert viewer.property("zoomed") is True
    QMetaObject.invokeMethod(viewer, "step", Q_ARG("QVariant", 1))
    pump(app, 0.2)
    assert popout.row == 2 and viewer.property("zoomed") is True and viewer.property("zoomScale") == 1
    popout.close()
    pump(app, 0.3)
    popout.open(2)
    pump(app, 0.2)
    assert viewer.property("zoomed") is False                    # opening the viewer still starts fitted


def test_a_closed_viewer_asks_for_no_filmstrip_thumbnails():
    text = source("Viewer.qml")
    assert 'path: v.visible || v.shown ? fs.path : ""' in text
    assert "bucket: 120" not in source("BatchWindow.qml")        # a size no other view uses would be a cache of its own
    assert 'Text { text: "Cancel"' not in text                   # nothing can stop the editor opening, so nothing offers to


# -- the filters window -------------------------------------------------------------------------------------------------------------

def test_the_filters_window_fits_a_narrow_window_and_keeps_an_unapplied_query(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path, "filtersOpen=true")
    window.setWidth(900)
    window.setHeight(760)
    QMetaObject.invokeMethod(window, "applyStartup", Q_ARG("QVariant", 900))
    window.show()
    pump(qapp, 0.6)
    filters = window.findChild(QObject, "filtersWindow")
    adv = engine.rootContext().contextProperty("advFilters")
    assert filters.property("shown") is True
    col, win_w = filters.property("colW"), filters.property("winW")
    assert col < 312 and 248 + 32 + 4 + 2 * col + 16 <= win_w - 32          # both columns end inside the page
    adv.set("rating", 3)
    pump(qapp, 0.1)
    assert filters.property("dirty") is True
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(8, 400))
    pump(qapp, 0.3)
    assert filters.property("shown") is True and adv.get("rating") == 3     # a click beside it does not throw the query away
    adv.apply()
    pump(qapp, 0.1)
    assert filters.property("dirty") is False
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(8, 400))
    pump(qapp, 0.4)
    assert filters.property("shown") is False                                # nothing to lose: it closes as before
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)
    wide = Path(shell_app.QML_DIR / "FiltersWindow.qml").read_text(encoding="utf-8")
    assert "width: 312" not in wide and '"M5 12h14"' not in wide + source("BatchWindow.qml") + source("SettingsWindow.qml")


def test_clearing_names_asks_first():
    text = source("TagPeopleWindow.qml")
    button = text[text.index('text: "Clear Selected"'):]
    button = button[:button.index("}")]
    assert "clearCard.open()" in button and "clearNames" not in button
    card = text[text.index("id: clearCard"):]
    assert "onAccepted: { people.clearNames(root.sel)" in card[:card.index("\n    }")]


def test_the_face_index_shows_no_meter_without_a_fraction():
    text = source("SidePanel.qml")
    assert 'Meter { visible: parent.info.pct !== ""' in text and "void people.revision; return people.indexInfo()" in text


def test_the_floating_bar_sizes_are_the_thumbnail_sizes(shell):
    window, context, app = shell
    assert "[9, 4, 1]" not in source("FloatingBar.qml")
    assert 'UiState.setDens(["s", "m", "l"][i])' in source("FloatingBar.qml")


# -- the keyboard on the shared controls ----------------------------------------------------------------------------------------------

CONTROLS = """
import QtQuick
import QtQuick.Window
Window {
    id: w
    width: 500; height: 300; visible: true
    property int presses: 0
    property bool on: false
    property int segment: 0
    property string choice: "a"
    property int answers: 0
    property int cancels: 0
    function ask() { card.open() }
    Column {
        SButton { objectName: "button"; text: "Go"; focus: true; onClicked: w.presses++ }
        SSwitch { objectName: "switch"; name: "Thing"; checked: w.on; onToggled: (v) => w.on = v }
        Seg { objectName: "seg"; labels: ["One", "Two", "Three"]; current: w.segment; onActivated: (i) => w.segment = i }
        SSelect { objectName: "select"; name: "Letter"; options: [{ label: "A", value: "a" }, { label: "B", value: "b" }, { label: "C", value: "c" }]; current: w.choice; onPicked: (v) => w.choice = v }
        PopItem { objectName: "item"; label: "Row"; width: 200; onClicked: w.presses += 10 }
    }
    ConfirmCard { id: card; objectName: "card"; title: "Sure?"; cancelText: "Cancel"; onAccepted: w.answers++; onRejected: w.cancels++ }
}
"""


@pytest.fixture
def controls(qapp, tmp_path):
    from PySide6.QtQml import QQmlApplicationEngine
    from image_triage.qml_shell.theme import Theme

    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(str(i) for i in items))
    engine.rootContext().setContextProperty("theme", Theme(parent=qapp))
    engine.addImportPath(str(shell_app.QML_DIR))
    component = QQmlComponent(engine)
    component.setData(CONTROLS.encode(), QUrl.fromLocalFile(str(QML / "ControlsUnderTest.qml")))
    window = component.create()
    assert window is not None, component.errorString()
    window.requestActivate()
    pump(qapp, 0.3)
    yield window, qapp, warnings
    window.close()
    engine.deleteLater()
    pump(qapp, 0.1)


def test_tab_reaches_the_controls_and_keys_work_them(controls):
    window, app, warnings = controls
    button = window.findChild(QObject, "button")
    for name in ("button", "switch", "seg", "select", "item"):
        assert window.findChild(QObject, name).property("activeFocusOnTab") is True, name
    QMetaObject.invokeMethod(button, "forceActiveFocus")
    pump(app, 0.05)
    QTest.keyClick(window, Qt.Key.Key_Space)
    QTest.keyClick(window, Qt.Key.Key_Return)
    assert window.property("presses") == 2
    QTest.keyClick(window, Qt.Key.Key_Tab)                       # on to the switch
    pump(app, 0.05)
    assert window.findChild(QObject, "switch").property("activeFocus") is True
    QTest.keyClick(window, Qt.Key.Key_Space)
    assert window.property("on") is True
    QTest.keyClick(window, Qt.Key.Key_Tab)                       # the segmented control: the arrows choose
    pump(app, 0.05)
    assert window.findChild(QObject, "seg").property("activeFocus") is True
    QTest.keyClick(window, Qt.Key.Key_Right)
    QTest.keyClick(window, Qt.Key.Key_Right)
    QTest.keyClick(window, Qt.Key.Key_Right)                     # stops at the last one
    assert window.property("segment") == 2
    QTest.keyClick(window, Qt.Key.Key_Left)
    assert window.property("segment") == 1
    QTest.keyClick(window, Qt.Key.Key_Tab)                       # the dropdown: Down and Up choose without opening it
    pump(app, 0.05)
    QTest.keyClick(window, Qt.Key.Key_Down)
    assert window.property("choice") == "b"
    QTest.keyClick(window, Qt.Key.Key_Up)
    QTest.keyClick(window, Qt.Key.Key_Up)
    assert window.property("choice") == "a"
    QTest.keyClick(window, Qt.Key.Key_Tab)                       # a menu row
    pump(app, 0.05)
    assert window.findChild(QObject, "item").property("activeFocus") is True
    QTest.keyClick(window, Qt.Key.Key_Return)
    assert window.property("presses") == 12
    assert not [w for w in warnings if "Error" in w], warnings


def test_return_answers_a_confirm_card_and_escape_cancels_it(controls):
    window, app, warnings = controls
    QMetaObject.invokeMethod(window, "ask")
    pump(app, 0.5)
    QTest.keyClick(window, Qt.Key.Key_Return)
    pump(app, 0.4)
    assert (window.property("answers"), window.property("cancels")) == (1, 0)
    QMetaObject.invokeMethod(window, "ask")
    pump(app, 0.5)
    QTest.keyClick(window, Qt.Key.Key_Escape)
    pump(app, 0.4)
    assert (window.property("answers"), window.property("cancels")) == (1, 1)


def test_controls_say_what_they_are():
    for name, needle in (("PopItem.qml", "Accessible.name: row.label"), ("SSwitch.qml", "Accessible.name: sw.name"), ("SSlider.qml", "Accessible.name: sl.name"),
                         ("PhotoGrid.qml", "Accessible.role: Accessible.ListItem"), ("SSelect.qml", 'sel.name + ": " + sel.currentLabel')):
        assert needle in source(name), name
    assert "Keys.onEscapePressed" not in source("HelpMenu.qml") and 'Shortcut { sequence: "Escape"; enabled: root.v.menu' in source("HelpMenu.qml")


# -- the viewer's host: what it holds about a photo is not kept past its use -------------------------------------------------------

@pytest.fixture
def host(qapp, tmp_path):
    from PySide6.QtGui import QImage
    from image_triage.qml_shell.photos import PhotoModel
    from image_triage.qml_shell.viewer_host import ViewerHost
    from qml_support import NoThumbs

    made = []
    for index in range(3):
        path = tmp_path / f"shot{index}.{'jpg' if index == 0 else 'tif'}"
        if index == 0:
            image = QImage(40, 30, QImage.Format.Format_RGB888)
            image.fill(0)
            assert image.save(str(path))
        else:
            path.write_bytes(b"not a picture")
        made.append({"path": str(path), "name": path.name, "ext": path.suffix[1:].upper(), "raw": False, "decision": "", "rating": 0, "ai": 0})
    photos = PhotoModel(parent=qapp)
    photos.setRows(made, str(tmp_path))
    viewer = ViewerHost(photos, NoThumbs(parent=qapp), memory=True, parent=qapp)
    viewer._pool.start = lambda task: None                      # these tests are about what is asked for, not about decoding: no worker runs
    yield viewer, made
    viewer.shutdown()


def test_the_size_of_a_plain_file_is_read_once_per_photo(host, monkeypatch):
    from image_triage.qml_shell import viewer_host

    viewer, made = host
    built = []
    real = viewer_host.QImageReader
    monkeypatch.setattr(viewer_host, "QImageReader", lambda *args: built.append(args) or real(*args))
    viewer.open(0)
    assert (viewer.pixelWidth, viewer.pixelHeight) == (40, 30) and viewer.src and not viewer.loading and viewer.info["dims"]
    assert len(built) == 1


def test_a_read_that_failed_is_tried_again_on_coming_back(host):
    viewer, made = host
    viewer.open(1)
    viewer._arrived("picture", made[1]["path"], "", "", 0, 0, "The share went away")
    assert viewer.error == "The share went away" and not viewer.loading
    viewer.go(2)
    viewer.go(1)
    assert viewer.error == "" and viewer.loading and made[1]["path"] in viewer._asked


def test_a_file_changed_on_disk_is_read_again(host, tmp_path):
    import os

    viewer, made = host
    path = made[2]["path"]
    viewer.open(2)
    held = tmp_path / "held.jpg"
    held.write_bytes(b"x")
    viewer._arrived("picture", path, "", str(held), 10, 10, "")
    assert viewer.pixelWidth == 10
    viewer.go(1)
    viewer.go(2)
    assert viewer.pixelWidth == 10                               # unchanged: the picture made before is used
    stamp = os.stat(path)
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 5_000_000_000))
    viewer.go(1)
    viewer.go(2)
    assert viewer.pixelWidth == 0 and viewer.loading and not held.exists()
