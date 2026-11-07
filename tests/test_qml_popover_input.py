"""Popover interactions must not activate items behind their surface."""
import pytest
from PySide6.QtCore import QByteArray, QObject, QPointF, QUrl, Qt, QMetaObject
from PySide6.QtQml import QQmlComponent
from PySide6.QtTest import QTest

from test_qml_shell import _load_main, pump, qapp  # noqa: F401


@pytest.mark.parametrize("menu", ["view", "fView"])
def test_popover_toggle_does_not_click_underlying_controls(qapp, tmp_path, menu):
    engine, window, warnings = _load_main(qapp, tmp_path)
    pump(qapp, .8)
    component = QQmlComponent(engine)
    component.setData(QByteArray(b'''import QtQuick
Item {
    property int taps: 0
    property int presses: 0
    width: 3000; height: 2000; z: 10000
    MouseArea { anchors.fill: parent; onPressed: parent.presses++ }
    TapHandler { onTapped: parent.taps++ }
}'''), QUrl())
    underlay = component.create()
    assert underlay, component.errors()
    underlay.setParentItem(window.contentItem())
    popup = next(o for o in window.findChildren(QObject)
                 if o.property("name") == menu and o.property("opened") is not None)
    QMetaObject.invokeMethod(popup, "open")
    pump(qapp, .4)
    assert popup.property("opened")
    content = popup.property("contentItem")
    column = next(o for o in content.childItems()[0].childItems() if any(c.property("label") == "Filenames" for c in o.childItems()))
    row = next(o for o in column.childItems() if o.property("label") == "Filenames")
    before = row.property("checked")
    point = row.mapToScene(QPointF(row.width()/2, row.height()/2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    pump(qapp, .1)
    assert row.property("checked") != before
    assert underlay.property("presses") == 0
    assert underlay.property("taps") == 0
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
    pump(qapp, .1)
    assert row.property("checked") == before
    # Empty menu space and outside dismissal must also consume the gesture.
    padding = content.mapToScene(QPointF(-3, 2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, padding)
    pump(qapp, .1)
    assert underlay.property("taps") == 0
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, window.contentItem().mapToScene(QPointF(window.width()-30, 100)).toPoint())
    pump(qapp, .2)
    assert not popup.property("opened")
    assert underlay.property("taps") == 0
    window.close()
    underlay.deleteLater()
    engine.deleteLater()
    pump(qapp, .2)
