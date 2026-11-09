"""Modal windows block the app while their own controls remain usable."""
import pytest
from PySide6.QtCore import QByteArray, QObject, QPoint, QPointF, QUrl, Qt, QMetaObject
from PySide6.QtGui import QGuiApplication, QWheelEvent, QInputMethodEvent
from PySide6.QtQml import QQmlComponent
from PySide6.QtTest import QTest

from test_qml_shell import _load_main, pump, qapp, rows  # noqa: F401


def items(root):
    yield root
    for child in root.childItems():
        yield from items(child)


def spy(engine, parent):
    component = QQmlComponent(engine)
    component.setData(QByteArray(b'''import QtQuick
Item { width: 3000; height: 2000; z: 10000
    property int taps: 0
    TapHandler { onTapped: parent.taps++ }
}'''), QUrl())
    item = component.create()
    assert item, component.errors()
    item.setParent(parent)
    item.setParentItem(parent)
    return item


@pytest.mark.parametrize("modal_name", ["handoffWindow", "settingsWindow", "filtersWindow"])
@pytest.mark.parametrize("background", ["grid", "viewer"])
def test_modal_body_and_dismissal_do_not_click_background(qapp, tmp_path, modal_name, background):
    engine, window, warnings = _load_main(qapp, tmp_path)
    engine.rootContext().contextProperty("photos").setRows(rows(3), "/p")
    if background == "viewer":
        engine.rootContext().contextProperty("popout").open(0)
    pump(qapp, .8)
    content = (window.findChild(QObject, "viewer") if background == "viewer"
               else window.findChild(QObject, "librarySidePanel").parentItem().parentItem())
    underlay = spy(engine, content)
    point = window.contentItem().mapToScene(QPointF(window.width()*.7, window.height()*.6)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    assert underlay.property("taps") == 1
    modal = window.findChild(QObject, modal_name)
    QMetaObject.invokeMethod(modal, "open")
    assert not content.isEnabled()
    pump(qapp, .4)
    assert modal.isVisible()
    assert not content.isEnabled()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    pump(qapp, .1)
    assert underlay.property("taps") == 1
    QTest.keyClick(window, Qt.Key_Escape)
    assert not content.isEnabled()  # The closing fade still shields the background.
    pump(qapp, .4)
    assert not modal.isVisible()
    assert content.isEnabled()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    assert underlay.property("taps") == 2
    if background == "viewer":
        assert window.activeFocusItem() is not None
    window.close(); underlay.deleteLater(); engine.deleteLater(); pump(qapp, .2)


def test_handoff_recipe_dropdown_blocks_controls_below_and_still_selects(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path)
    modal = window.findChild(QObject, "handoffWindow")
    QMetaObject.invokeMethod(modal, "open")
    pump(qapp, .5)
    # Passive controls behind the dropdown must not see its row clicks.
    underlay = spy(engine, modal)
    underlay.setZ(-1)
    name_field = next(item for item in items(modal) if item.property("placeholder") == "Export Recipe")
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, name_field.mapToScene(QPointF(40, 15)).toPoint())
    QTest.keyClick(window, Qt.Key_A, Qt.ControlModifier)
    edit = QInputMethodEvent()
    edit.setCommitString("UI edit")
    QGuiApplication.sendEvent(window.activeFocusItem(), edit)
    assert engine.rootContext().contextProperty("handoff").get("name") == "UI edit"
    select = None
    for item in items(modal):
        options = item.property("options")
        options = options.toVariant() if hasattr(options, "toVariant") else options
        if isinstance(options, list) and any("Proofing JPEGs" in str(option.get("label", "")) for option in options):
            select = item
            break
    assert select is not None
    select.setProperty("menuMax", 120)
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, select.mapToScene(QPointF(50, 15)).toPoint())
    pump(qapp, .4)
    popup = next(o for o in window.findChildren(QObject) if o.property("opened") is True and o.property("parent") == select)
    menu_view = popup.property("contentItem")
    position = menu_view.mapToScene(QPointF(50, 50))
    event = QWheelEvent(position, QPointF(window.mapToGlobal(position.toPoint())), QPoint(), QPoint(0, -120),
                        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QGuiApplication.sendEvent(window, event)
    pump(qapp, .2)
    assert menu_view.property("contentY") > 0
    menu_view.setProperty("contentY", 0)
    pump(qapp, .1)
    label = next(item for item in items(popup.property("contentItem")) if "Proofing JPEGs" in str(item.property("text") or ""))
    before = underlay.property("taps")
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, label.mapToScene(QPointF(20, 8)).toPoint())
    pump(qapp, .3)
    assert underlay.property("taps") == before
    assert engine.rootContext().contextProperty("handoff").get("load").startswith("builtin:")
    assert modal.isVisible() and not popup.property("opened")
    window.close(); underlay.deleteLater(); engine.deleteLater(); pump(qapp, .2)
