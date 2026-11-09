"""The Collections pane previews recent members as a two-column card grid."""
from PySide6.QtCore import QObject, QPointF, Qt, QMetaObject, Q_ARG
from PySide6.QtTest import QTest

from image_triage.qml_shell.collections_model import CollectionsModel
from image_triage.qml_shell.photos import PhotoModel
from test_qml_shell import _load_main, pump, qapp, rows  # noqa: F401


def visual_items(root):
    pending = [root]
    while pending:
        item = pending.pop()
        yield item
        pending.extend(item.childItems())


def test_remove_selection_button_refreshes_the_collection_grid(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path)
    context = engine.rootContext()
    photos = context.contextProperty("photos")
    collections = context.contextProperty("collections")
    photos.setRows(rows(3), "/p")
    cid = collections.save("", "Button removal", "", "", False)
    collections.addVisible(cid)
    collections.open(cid)
    pump(qapp, .8)
    window.findChild(QObject, "photoGrid").setProperty("currentIndex", 1)
    label = next(item for item in visual_items(window.contentItem()) if item.property("text") == "Remove selection")
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, label.mapToScene(QPointF(label.width()/2, label.height()/2)).toPoint())
    pump(qapp, .2)
    assert photos.shown == 2 and collections.get(cid)["count"] == 2
    assert window.findChild(QObject, "photoGrid").property("count") == 2
    collections.removeSelection(cid, 0)
    collections.removeSelection(cid, 0)
    pump(qapp, .1)
    assert photos.shown == 0 and photos.scopeId == cid
    window.close()
    engine.deleteLater()
    pump(qapp, .2)


def test_collection_previews_use_newest_global_members_not_the_open_folder(qapp, tmp_path, monkeypatch):
    import image_triage.library_store as storage

    monkeypatch.setattr(storage, "app_data_root", lambda: tmp_path / "data")
    store = storage.LibraryStore()
    paths = [str(tmp_path / "another-shoot" / name) for name in ("z-old.jpg", "c.jpg", "b.jpg", "a.nef", "a.jpg")]
    album = store.create_collection(name="Latest", item_paths=paths[:3])
    store.add_paths_to_collection(album.id, paths[3:])
    assert store.recent_collection_paths(album.id) == tuple(reversed(paths))
    photos = PhotoModel(parent=qapp)
    model = CollectionsModel(photos, memory=False, parent=qapp)
    card = model.get(album.id)
    assert card["previewThumbs"] == [paths[4], paths[2], paths[1]]
    assert card["thumbs"] == ["", "", ""]  # Existing folder-scoped consumers keep their data.
    store.remove_paths_from_collection(album.id, paths[3:])
    model._reload()
    assert model.get(album.id)["previewThumbs"] == [paths[2], paths[1], paths[0]]


def test_collection_cards_have_two_columns_and_keep_menu_and_open_actions_separate(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path, "tab=coll")
    window.setWidth(1440)
    QMetaObject.invokeMethod(window, "applyStartup", Q_ARG("QVariant", 1440))
    pump(qapp, .8)
    grid = window.findChild(QObject, "collectionCardGrid")
    assert grid.property("columns") == 2
    cards = sorted([item for item in visual_items(window.contentItem()) if item.objectName().startswith("collectionCard_")], key=lambda item: item.objectName())
    assert len(cards) == 5
    first, second, third = [item.parentItem() for item in cards[:3]]
    assert first.y() == second.y() and second.x() > first.x()
    assert third.y() > first.y() and third.x() == first.x()
    assert second.x() + second.width() <= grid.width() + .5
    photos = engine.rootContext().contextProperty("photos")
    menu = next(item for item in visual_items(cards[0]) if item.objectName() == "collectionCardMenu")
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, menu.mapToScene(QPointF(12, 12)).toPoint())
    pump(qapp, .3)
    assert photos.scopeKind == ""  # More never also opens the collection.
    QTest.keyClick(window, Qt.Key_Escape)
    pump(qapp, .2)
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, cards[0].mapToScene(QPointF(30, 110)).toPoint())
    pump(qapp, .2)
    assert photos.scopeKind == "collection" and photos.scopeId == "c1"
    assert not [w for w in warnings if any(name in w for name in ("SidePanel.qml", "CollectionCard.qml"))], warnings
    window.close()
    engine.deleteLater()
    pump(qapp, .2)
