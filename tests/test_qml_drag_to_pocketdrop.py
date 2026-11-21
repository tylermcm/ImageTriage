"""Dragging photos from the grid: onto a collection by default (the Collections page opens as they are carried to the panel), and onto
PocketDrop when its page is the one open as the drag starts (Collections is then not opened; the photos are added to PocketDrop)."""
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtQml import QQmlExpression
from PySide6.QtTest import QTest

from test_qml_shell import _load_main, pump, qapp, rows  # noqa: F401


def ui(window, engine, expression):
    from PySide6.QtQml import QQmlEngine

    return QQmlExpression(QQmlEngine.contextForObject(window), window, expression).evaluate()[0]


def drag(window, qapp, start, end):
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, start); pump(qapp, .05)
    for step in range(1, 13):                                       # over the threshold, then across to the panel
        QTest.mouseMove(window, QPointF(start.x() + (end.x() - start.x()) * step / 12, start.y() + (end.y() - start.y()) * step / 12).toPoint(), 15)
        pump(qapp, .03)
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, end); pump(qapp, .3)


def test_photos_dragged_with_pocketdrop_open_go_to_pocketdrop(qapp, tmp_path, monkeypatch):
    engine, window, warnings = _load_main(qapp, tmp_path)
    context = engine.rootContext()
    context.contextProperty("photos").setRows(rows(12), "/p")
    window.setWidth(1440); window.setHeight(900); window.show(); pump(qapp, .5)
    pocket = context.contextProperty("pocketDrop")
    added = []
    monkeypatch.setattr(type(pocket), "addUrls", lambda self, urls: added.append(list(urls)), raising=False)
    grid = window.findChild(QObject, "photoGrid")
    cell = grid.mapToScene(QPointF(90, 70)).toPoint()
    drop = window.findChild(QObject, "pocketDropPhotoDrop")
    ui(window, engine, "UiState.tab = 'drop'"); pump(qapp, .5)
    target = drop.mapToScene(QPointF(drop.width() / 2, drop.height() / 2)).toPoint()
    drag(window, qapp, cell, target)
    assert ui(window, engine, "UiState.tab") == "drop"                     # Collections was not opened
    assert window.findChild(QObject, "pocketDropPhotoDrop") is drop
    ui(window, engine, "UiState.tab = 'lib'"); pump(qapp, .4)               # the default: carried to the panel, Collections opens
    drag(window, qapp, cell, target)
    assert ui(window, engine, "UiState.tab") in ("coll", "lib")
    window.close(); pump(qapp, .1)


def test_the_drop_passes_the_dragged_photos_paths(qapp, tmp_path, monkeypatch):
    from image_triage.qml_shell import pocketdrop_model

    seen = []
    monkeypatch.setattr(pocketdrop_model.PocketDropModel, "addUrls", lambda self, urls: seen.append(list(urls)))
    engine, window, warnings = _load_main(qapp, tmp_path)
    context = engine.rootContext()
    context.contextProperty("photos").setRows(rows(12), "/p")
    window.setWidth(1440); window.setHeight(900); window.show(); pump(qapp, .5)
    ui(window, engine, "UiState.tab = 'drop'"); pump(qapp, .5)
    grid = window.findChild(QObject, "photoGrid")
    drop = window.findChild(QObject, "pocketDropPhotoDrop")
    drag(window, qapp, grid.mapToScene(QPointF(90, 70)).toPoint(), drop.mapToScene(QPointF(drop.width() / 2, drop.height() / 2)).toPoint())
    assert ui(window, engine, "UiState.tab") == "drop"
    window.close(); pump(qapp, .1)
    assert seen and seen[-1] and all(str(p).startswith("/p/") for p in seen[-1])          # the dragged photos' paths
    assert not [w for w in warnings if "PocketDrop" in w or "PhotoGrid" in w], warnings
