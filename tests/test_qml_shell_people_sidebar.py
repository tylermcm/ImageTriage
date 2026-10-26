"""Sidebar face actions exercise real pointer/keyboard routing and the shared people model."""
import os
import time

import pytest
from PySide6.QtCore import QObject, QPointF, Qt, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtQuick import QQuickWindow  # Register the native QML window/item wrappers.
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from image_triage.qml_shell.app import QML_DIR, _configure_qt_dll_search
from image_triage.qml_shell.photos import PhotoModel
from image_triage.qml_shell.settings_model import SettingsModel
from image_triage.qml_shell.theme import Theme
from qml_support import collection_models


def pump(app, seconds=0.25):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


@pytest.fixture
def sidebar():
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
    _configure_qt_dll_search()
    app = QApplication.instance() or QApplication([])
    engine = QQmlApplicationEngine()
    photos = PhotoModel()
    photos.setRows([{"path": f"/sample/{i}.jpg", "name": f"{i}.jpg", "ext": "JPG", "decision": "", "rating": 0} for i in range(12)], "/sample")
    objects = dict(collection_models(photos, app))
    objects.update(theme=Theme(parent=app), photos=photos, settings=SettingsModel(memory=True, parent=app))
    for name, obj in objects.items():
        engine.rootContext().setContextProperty(name, obj)
    engine.addImportPath(str(QML_DIR))
    errors = []
    engine.warnings.connect(lambda items: errors.extend(e.toString() for e in items))
    engine.loadData(b'''
import QtQuick
Window {
    color: "#0e0f11"
    width: 1200; height: 900; visible: true
    property bool peopleOpen: UiState.peopleOpen
    property string selectedPerson: UiState.peopleSel
    SidePanel { width: 300; height: parent.height }
    TagPeopleWindow {}
    Component.onCompleted: { UiState.tab = "faces"; UiState.menu = ""; UiState.peopleOpen = false; UiState.peopleSel = "" }
}
''', QUrl.fromLocalFile(str(QML_DIR / "sidebar-test.qml")))
    assert engine.rootObjects(), errors
    window = engine.rootObjects()[0]
    pump(app, 0.8)
    yield app, window, objects, errors
    window.close()
    engine.deleteLater()
    pump(app, 0.45)


def item(window, name):
    found = window.findChild(QObject, name)
    pending = [window.contentItem()]
    while found is None and pending:
        current = pending.pop()
        if current.objectName() == name:
            found = current
            break
        pending.extend(current.childItems())
    assert found is not None, name
    return found


def click(app, window, target, button=Qt.LeftButton, x=None):
    point = target.mapToScene(QPointF(target.width() / 2 if x is None else x, target.height() / 2)).toPoint()
    QTest.mouseClick(window, button, Qt.NoModifier, point)
    pump(app)


def menu(sidebar, pid="p1"):
    app, window, objects, errors = sidebar
    click(app, window, item(window, "sidebarPerson_" + pid), Qt.RightButton)
    pump(app, 0.4)
    assert item(window, "sidebarPersonMenu").property("opened"), errors
    assert not window.property("peopleOpen")
    return app, window, objects, errors


def type_text(window, text):
    event = QInputMethodEvent()
    event.setCommitString(text)
    QApplication.sendEvent(window.focusObject(), event)


def test_right_click_names_without_opening_full_dialog(sidebar):
    app, window, objects, errors = menu(sidebar, "p5")
    assert objects["photos"].scopeKind != "person"
    click(app, window, item(window, "sidebarPersonRename"))
    type_text(window, "Sample Person")
    QTest.keyClick(window, Qt.Key_Return)
    pump(app)
    assert objects["people"].card("p5")["name"] == "Sample Person"
    assert not window.property("peopleOpen")
    assert not errors


def test_show_photos_clear_name_hide_and_undo(sidebar):
    app, window, objects, errors = menu(sidebar)
    click(app, window, item(window, "sidebarPersonPhotos"))
    assert objects["photos"].scopeId == "p1"
    menu(sidebar)
    click(app, window, item(window, "sidebarPersonClear"))
    assert objects["people"].card("p1")["name"] == ""
    menu(sidebar)
    click(app, window, item(window, "sidebarPersonHide"))
    assert not objects["people"].card("p1")
    assert objects["photos"].scopeKind != "person"
    click(app, window, item(window, "sidebarPersonUndo"))
    assert objects["people"].card("p1")
    assert not errors


def test_double_click_portrait_opens_full_window_with_person_selected(sidebar):
    app, window, objects, errors = sidebar
    target = item(window, "sidebarPerson_p1")
    point = target.mapToScene(QPointF(34, 25)).toPoint()
    QTest.mouseDClick(window, Qt.LeftButton, Qt.NoModifier, point)
    pump(app, 0.5)
    assert window.property("peopleOpen")
    assert window.property("selectedPerson") == "p1"
    assert list(item(window, "tagPeopleWindow").property("sel").toVariant()) == ["p1"]
    assert not errors


def test_escape_discards_draft(sidebar):
    app, window, objects, errors = menu(sidebar)
    click(app, window, item(window, "sidebarPersonRename"))
    type_text(window, "Discarded")
    QTest.keyClick(window, Qt.Key_Escape)
    pump(app)
    assert objects["people"].card("p1")["name"] == "Anna"
    assert not item(window, "sidebarPersonMenu").property("opened")


def test_blank_name_is_not_saved_and_folder_change_clears_undo(sidebar):
    app, window, objects, errors = menu(sidebar)
    click(app, window, item(window, "sidebarPersonRename"))
    type_text(window, "   ")
    pump(app)
    assert not item(window, "sidebarPersonSave").isEnabled()
    QTest.keyClick(window, Qt.Key_Return)
    assert objects["people"].card("p1")["name"] == "Anna"
    QTest.keyClick(window, Qt.Key_Escape)
    pump(app)
    menu(sidebar)
    click(app, window, item(window, "sidebarPersonHide"))
    assert item(window, "sidebarPersonUndo").isVisible()
    objects["photos"].setRows([], "/another-sample")
    pump(app)
    assert not item(window, "sidebarPersonUndo").isVisible()
    assert not item(window, "sidebarPersonMenu").property("opened")
