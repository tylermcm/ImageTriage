"""Exercise the Library controls with overflowing drives and a large folder tree."""
import pytest
import time
from PySide6.QtCore import QObject, QPoint, QPointF, QUrl, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from image_triage.qml_shell.app import QML_DIR
from image_triage.qml_shell.photos import PhotoModel
from image_triage.qml_shell.theme import Theme
from qml_support import collection_models
from test_qml_shell import pump, qapp  # noqa: F401


@pytest.fixture
def sidebar(qapp, tmp_path):
    QQuickStyle.setStyle("Basic")  # the application selects the same style before loading QML
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(str(item) for item in items))
    photos = PhotoModel(parent=engine)
    theme = Theme(parent=engine)
    context = engine.rootContext()
    context.setContextProperty("theme", theme)
    context.setContextProperty("photos", photos)
    for name, obj in collection_models(photos, engine):
        context.setContextProperty(name, obj)
    engine.addImportPath(str(QML_DIR))
    engine.loadData(b'''import QtQuick
import QtQuick.Window
Window {
    width: 300; height: 700; visible: true
    SidePanel { anchors.fill: parent }
}''', QUrl.fromLocalFile(str(QML_DIR / "LibraryScrollHarness.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    nav = context.contextProperty("libraryNav")
    nav._dir_rows.set_rows([
        {"path": f"sample:{i}", "name": f"Photography folder {i:03}", "count": "", "depth": 0,
         "icon": "chevR", "current": False, "expandable": True}
        for i in range(200)
    ])
    nav._drv_rows.set_rows([
        {"path": f"sample:drive{i}", "name": f"Photos ({chr(67 + i)}:)", "detail": "620 GB free of 915 GB", "ratio": 0.3}
        for i in range(10)
    ])
    pump(qapp, 0.7)
    yield window, context, warnings, tmp_path
    assert not warnings, warnings
    window.close()
    engine.deleteLater()
    pump(qapp, 0.1)


def item(window, name):
    found = window.findChild(QObject, name)
    assert found is not None, name
    return found


def click(qapp, window, control):
    pos = control.mapToScene(QPointF(control.width() / 2, control.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, pos)
    pump(qapp, 0.25)


def test_drive_pin_uses_red_ink_without_a_mouse_focus_highlight(qapp, sidebar):
    window, context, warnings, tmp_path = sidebar
    panel = item(window, "librarySidePanel")
    button = item(window, "pinDrivesButton")
    icon = button.findChild(QObject, "libraryButtonIcon")
    panel.setProperty("drivesPinned", False)
    pump(qapp, .1)
    assert not icon.property("filled")
    click(qapp, window, button)
    assert panel.property("drivesPinned") is True
    assert icon.property("color") == context.contextProperty("theme").rej
    assert icon.property("filled") is True
    assert not button.hasActiveFocus()
    click(qapp, window, button)
    assert icon.property("filled") is False
    assert icon.property("color") == context.contextProperty("theme").fg3


def test_double_clicking_a_folder_row_opens_it_and_reveals_its_children(qapp, sidebar):
    from image_triage.drive_list_model import DriveListModel
    from image_triage.qml_shell.library_nav import LibraryNavModel
    from image_triage.qml_shell.photos import GridModel
    window, context, warnings, tmp_path = sidebar
    root = tmp_path / "drive"
    folder = root / "Photography"
    (folder / "Portland").mkdir(parents=True)
    (folder / "China").mkdir()
    photos = context.contextProperty("photos")
    photos.setFilter("keep")
    grid = GridModel(photos)
    nav = LibraryNavModel(photos, persist=False, drives=DriveListModel(roots_provider=lambda: [], probe=lambda p: (False, "", None)),
                          drive_root=lambda p: str(root), parent=context.engine())
    context.setContextProperty("libraryNav", nav)
    nav.openFolder(str(root))
    deadline = time.monotonic() + 5
    while not nav.tree and time.monotonic() < deadline:
        pump(qapp, .02)
    pump(qapp, .1)
    pending = [window.contentItem()]
    row = None
    while pending:
        candidate = pending.pop()
        if candidate.objectName() == "libraryFolderRow" and candidate.property("folder") == str(folder):
            row = candidate
            break
        pending.extend(candidate.childItems())
    assert row is not None
    pos = row.mapToScene(QPointF(120, row.height() / 2)).toPoint()
    QTest.mouseDClick(window, Qt.LeftButton, Qt.NoModifier, pos)
    deadline = time.monotonic() + 5
    while (photos.loading or len(nav.tree) < 3) and time.monotonic() < deadline:
        pump(qapp, .02)
    assert nav.openPath == str(folder)
    assert [r["name"] for r in nav.tree] == ["Photography", "China", "Portland"]
    assert grid.folders == 2
    nav.toggle(str(folder))
    pump(qapp, .5)
    assert [r["name"] for r in nav.tree] == ["Photography"]
    QTest.mouseDClick(window, Qt.LeftButton, Qt.NoModifier, pos)
    pump(qapp, .2)
    assert [r["name"] for r in nav.tree] == ["Photography", "China", "Portland"]


def wheel(qapp, window, control, *, angle=-120, pixels=0, settle=0.19):
    y = control.height() - 80 if control.objectName() == "libraryScroll" else min(50, control.height() / 2)
    pos = control.mapToScene(QPointF(120, y))
    event = QWheelEvent(pos, window.mapToGlobal(pos.toPoint()), QPoint(0, pixels), QPoint(0, angle),
                        Qt.NoButton, Qt.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    qapp.sendEvent(window, event)
    pump(qapp, settle)


def test_pin_collapse_restore_and_resize(qapp, sidebar):
    window, context, warnings, tmp_path = sidebar
    panel = item(window, "librarySidePanel")
    scroll = item(window, "libraryScroll")
    drives = item(window, "driveScroll")
    section = item(window, "driveSection")
    initial_y = section.mapToScene(QPointF()).y()
    scroll.setProperty("contentY", 400)
    pump(qapp, 0.1)
    assert section.mapToScene(QPointF()).y() == pytest.approx(initial_y - 400)
    scroll.setProperty("contentY", 0)
    click(qapp, window, item(window, "pinDrivesButton"))
    assert panel.property("drivesPinned")
    fixed_y = section.mapToScene(QPointF()).y()
    scroll.setProperty("contentY", 700)
    pump(qapp, 0.2)
    assert section.mapToScene(QPointF()).y() == fixed_y
    assert drives.height() <= 5 * 43.2
    assert section.height() <= (window.height() - 44) * 0.30 + 1
    window.grabWindow().save(str(tmp_path / "sidebar-pinned.png"))
    click(qapp, window, item(window, "collapseDrivesButton"))
    assert panel.property("drivesCollapsed") and drives.height() == 0
    assert context.contextProperty("uiMemory").get("drivesPinned", False) is True
    assert context.contextProperty("uiMemory").get("drivesCollapsed", False) is True
    component = QQmlComponent(context.engine(), QUrl.fromLocalFile(str(QML_DIR / "SidePanel.qml")))
    restored = component.create(context)
    assert restored is not None, component.errors()
    assert restored.property("drivesPinned") and restored.property("drivesCollapsed")
    restored.deleteLater()
    click(qapp, window, item(window, "collapseDrivesButton"))
    window.setHeight(400)
    pump(qapp, 0.2)
    assert section.height() <= (window.height() - 44) * 0.30 + 1
    assert scroll.height() > 200
    # A disappearing drive and a short tree cannot leave the pane stranded at an old offset.
    context.contextProperty("libraryNav")._drv_rows.set_rows([])
    context.contextProperty("libraryNav")._dir_rows.set_rows([])
    pump(qapp, 0.2)
    assert scroll.property("contentY") == 0 and scroll.y() == 88


def test_pinning_keeps_the_search_above_drives_without_moving_or_recreating_it(qapp, sidebar):
    window, context, warnings, tmp_path = sidebar
    search = item(window, "libraryFolderSearchField")
    text_input = item(window, "libraryFolderSearch")
    scroll = item(window, "libraryScroll")
    initial_y = search.mapToScene(QPointF()).y()
    text_input.setProperty("text", "Photography")
    click(qapp, window, item(window, "pinDrivesButton"))
    assert search.mapToScene(QPointF()).y() == initial_y
    assert item(window, "driveSection").mapToScene(QPointF()).y() >= initial_y + search.height()
    scroll.setProperty("contentY", 700)
    pump(qapp, 0.2)
    assert search.mapToScene(QPointF()).y() == initial_y
    click(qapp, window, item(window, "collapseDrivesButton"))
    assert search.mapToScene(QPointF()).y() == initial_y
    assert text_input.property("text") == "Photography"
    scroll.setProperty("contentY", 0)
    click(qapp, window, item(window, "pinDrivesButton"))
    assert search.mapToScene(QPointF()).y() == initial_y
    assert item(window, "libraryFolderSearch") is text_input


def test_wheel_ramps_resets_preserves_touchpad_and_returns_to_top(qapp, sidebar):
    window, context, warnings, tmp_path = sidebar
    scroll = item(window, "libraryScroll")
    wheel(qapp, window, scroll)
    assert scroll.property("contentY") == pytest.approx(96, abs=1)
    # Consecutive notches ramp up; a pause and a direction change restore precision.
    before = scroll.property("contentY")
    wheel(qapp, window, scroll, settle=0.03)
    wheel(qapp, window, scroll)
    assert scroll.property("contentY") - before > 192
    pump(qapp, 0.25)
    before = scroll.property("contentY")
    wheel(qapp, window, scroll)
    assert scroll.property("contentY") - before == pytest.approx(96, abs=1)
    before = scroll.property("contentY")
    wheel(qapp, window, scroll, angle=0, pixels=-17)
    assert scroll.property("contentY") - before == pytest.approx(17, abs=1)
    before = scroll.property("contentY")
    wheel(qapp, window, scroll, angle=120)
    assert before - scroll.property("contentY") == pytest.approx(96, abs=1)
    popup = item(window, "libraryBackToTop")
    assert popup.isVisible()
    window.grabWindow().save(str(tmp_path / "sidebar-scrolled.png"))
    click(qapp, window, popup)
    assert scroll.property("contentY") == pytest.approx(0, abs=1)
    assert not popup.isVisible()


def test_drive_wheel_is_independent_and_hands_off_at_the_end(qapp, sidebar):
    window, context, warnings, tmp_path = sidebar
    scroll = item(window, "libraryScroll")
    drives = item(window, "driveScroll")
    wheel(qapp, window, drives)
    assert drives.property("contentY") == pytest.approx(43.2, abs=1)
    assert scroll.property("contentY") == 0
    drives.setProperty("contentY", drives.property("contentHeight") - drives.height())
    wheel(qapp, window, drives)
    assert scroll.property("contentY") > 0
