"""PocketDrop's button row in a narrow side panel: Library becomes its icon alone, and a button too narrow for its label shows its icon
whole and centred rather than spilling its icon and label outside it (Add files clipped to "+ Add fil" with the icon outside)."""
from PySide6.QtCore import QObject

from test_qml_drag_to_pocketdrop import ui
from test_qml_shell import _load_main, pump, qapp, rows  # noqa: F401


def test_the_button_row_fits_a_narrow_panel(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path)
    engine.rootContext().contextProperty("photos").setRows(rows(4), "/p")
    window.setWidth(1440); window.setHeight(900); window.show(); pump(qapp, .5)
    ui(window, engine, "UiState.tab = 'drop'"); pump(qapp, .5)
    page = window.findChild(QObject, "pocketDropPhotoDrop").parentItem()
    add = window.findChild(QObject, "addButton")
    for width, compact in ((420, False), (300, True), (240, True)):
        page.setWidth(width); pump(qapp, .2)
        lib = [b for b in page.findChildren(QObject) if b.property("label") == "Library"][0]
        assert (lib.property("width") == 38) is compact
        for button in (add, lib):
            # the label is shown only where it fits; otherwise the icon alone, within the button
            assert button.property("compact") == (button.property("width") < button.property("fullWidth"))
        assert add.property("width") >= 38
    assert not [w for w in warnings if "PdButton" in w or "PocketDrop" in w], warnings


def test_a_menu_opened_low_in_the_window_opens_upward_and_stays_inside(qapp, tmp_path):
    """Right-clicking a folder near the bottom of the library list put its menu below the window's edge."""
    from pathlib import Path

    from PySide6.QtCore import QPointF, QUrl
    from PySide6.QtQml import QQmlComponent

    engine, window, warnings = _load_main(qapp, tmp_path)
    window.setWidth(1200); window.setHeight(700); window.show(); pump(qapp, .5)
    qml_dir = Path(__file__).resolve().parents[1] / "image_triage" / "qml_shell" / "qml"
    source = b"""import QtQuick
Item { id: anchorItem; x: 40; width: 200; height: 30
    Popover { id: menu; objectName: "testMenu"; placement: 2
        Repeater { model: 8; delegate: Rectangle { width: 180; height: 32 } } } }"""
    component = QQmlComponent(engine)
    component.setData(source, QUrl.fromLocalFile(str(qml_dir / "TestMenuHost.qml")))
    host = component.create(engine.rootContext())
    assert host is not None, component.errors()
    host.setParentItem(window.contentItem())
    for y, upward in ((window.height() - 40, True), (60, False)):
        host.setY(y); menu = host.findChild(QObject, "testMenu"); menu.open(); pump(qapp, .5)
        top = menu.property("background").mapToScene(QPointF(0, 0)).y()    # the menu's top, in the window
        assert (menu.property("y") < 0) is upward
        assert 0 <= top and top + menu.property("height") <= window.height(), (y, top, menu.property("height"))
        menu.close(); pump(qapp, .4)
