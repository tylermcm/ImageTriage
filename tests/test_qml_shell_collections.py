"""The QML shell's collections, people and best-of: the Widgets app's library store, face index and planner behind the mockup's windows."""
from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QCoreApplication, QSettings, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface

from image_triage.qml_shell import app as shell_app
from image_triage.qml_shell.batch_model import BatchModel
from image_triage.qml_shell.bestof_model import BestOfModel
from image_triage.qml_shell.collections_model import ICONS, CollectionsModel
from image_triage.qml_shell.filters_model import FiltersModel
from image_triage.qml_shell.handoff_model import HandoffModel
from image_triage.qml_shell.palette_model import PaletteModel
from image_triage.qml_shell.people_model import PeopleModel
from image_triage.qml_shell.photos import PhotoModel
from image_triage.qml_shell.settings_model import SettingsModel
from image_triage.qml_shell.theme import Theme
from image_triage.qml_shell.thumbs import ThumbService
from image_triage.qml_shell.transfer_model import TransferModel
from qml_support import collection_models

QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Software)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication   # the shell is a widgets application: the popout is a Widgets window
    return QCoreApplication.instance() or QApplication([])


def pump(app, seconds=0.2):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


@pytest.fixture
def photos(qapp, tmp_path):
    model = PhotoModel(parent=qapp)
    rows = []
    for index in range(8):
        path = tmp_path / f"DSC_42{index:02d}.jpg"
        path.write_bytes(b"x" * 2000)
        rows.append({"path": str(path), "name": f"DSC_42{index:02d}", "ext": "JPG", "raw": False, "decision": "win" if index in (1, 4) else "", "rating": index % 6, "ai": 60 + index})
    model.setRows(rows, str(tmp_path))
    return model


@pytest.fixture
def collections(qapp, photos):
    return CollectionsModel(photos, memory=True, parent=qapp)


# ---- collections (sample) ----------------------------------------------------------------------------------------------------

def test_the_icon_library_has_a_diamond_ring_for_weddings(collections):
    assert len(ICONS) == 24 and len({k for k, _l, _p in ICONS}) == 24       # the mockup's 23 icons, and a 24th where its "+" tile was
    labels = {k: label for k, label, _p in ICONS}
    assert labels["ring"] == "Wedding" and collections.iconPathOf("ring").startswith("M12 21a4.5 4.5")
    assert [c["label"] for c in collections.colors()][:2] == ["No color", "Blue"]


def test_sample_collections_match_the_mockup(collections):
    rows = collections.rows
    assert [(r["name"], r["color"], r["icon"], r["count"]) for r in rows] == [
        ("Album selects", "#4c8dff", "", 6), ("Print order", "#3ccf8e", "print", 3), ("Landscapes", "#c58af9", "mountain", 6),
        ("Wedding", "#ff6b7d", "ring", 2), ("Client proofs", "", "", 0)]
    assert collections.activeId == "c1" and [r["id"] for r in rows if r["active"]] == ["c1"]


def test_names_follow_the_widgets_dialog_rules(collections):
    assert collections.nameProblem("   ", "") == "Give it a name."
    assert collections.nameProblem("  landscapes ", "") == "You already have a collection called “landscapes”."
    assert collections.nameProblem("Landscapes", "c3") == ""               # editing a collection may keep its own name
    assert collections.save("", "Print order", "", "", False) == ""        # a duplicate is not saved


def test_create_edit_and_delete(collections):
    cid = collections.save("", "Utah Portfolio", "#e0b45a", "mountain", True)
    row = collections.get(cid)
    assert (row["name"], row["color"], row["icon"], row["count"]) == ("Utah Portfolio", "#e0b45a", "mountain", 0)
    assert collections.activeId == cid and not collections.get("c1")["active"]      # one collection collects new winners
    collections.save(cid, "Utah", "", "", False)
    assert collections.get(cid)["name"] == "Utah" and collections.activeId == ""
    collections.remove(cid)
    assert not collections.get(cid)


def test_adding_and_removing_photos(qapp, photos, collections):
    cid = collections.save("", "Shortlist", "", "", False)
    assert collections.addSelection(cid, 2) == 1 and collections.addSelection(cid, 2) == 0
    assert collections.addVisible(cid) == 7 and collections.get(cid)["count"] == 8
    collections.removeSelection(cid, 0)
    assert collections.get(cid)["count"] == 7
    assert [e["id"] for e in collections.pickerEntries()][:3] == [cid, "c1", "c3"]       # recent first, then A to Z
    assert collections.pickerEntries()[0]["recent"] is True


def test_opening_a_collection_scopes_the_grid(photos, collections):
    cid = collections.save("", "Shortlist", "", "", False)
    collections.addSelection(cid, 1)
    collections.addSelection(cid, 3)
    collections.open(cid)
    assert (photos.scopeKind, photos.scopeId, photos.shown, photos.total) == ("collection", cid, 2, 2)
    collections.closeScope()
    assert (photos.scopeKind, photos.shown, photos.total) == ("", 8, 8)
    collections.openWinners()
    assert (photos.scopeKind, photos.shown) == ("winners", 2)
    assert collections.winnersCount == 2


def test_the_badge_is_for_the_collecting_collection_in_the_plain_view_only(photos, collections):
    collections.addSelection("c1", 0)
    row = photos.get(0)
    mark = collections.markFor(row["path"], row["name"])
    assert mark["name"] == "Album selects" and mark["color"] == "#4c8dff"
    assert collections.markFor(photos.get(5)["path"], photos.get(5)["name"]) == {}      # not in it
    collections.open("c1")
    assert collections.markFor(row["path"], row["name"]) == {}


# ---- collections (the Widgets library store) ----------------------------------------------------------------------------------

def test_the_real_store_keeps_collections_and_their_icons(qapp, photos, tmp_path, monkeypatch):
    import image_triage.library_store as library_store

    monkeypatch.setattr(library_store, "app_data_root", lambda: tmp_path / "data")
    QSettings.setPath(QSettings.Format.NativeFormat, QSettings.Scope.UserScope, str(tmp_path / "cfg"))
    model = CollectionsModel(photos, memory=False, parent=qapp)
    cid = model.save("", "Wedding", "#ff6b7d", "ring", True)
    assert cid and model.get(cid)["icon"] == "ring" and model.activeId == cid
    assert model.addVisible(cid) == 8
    reopened = CollectionsModel(photos, memory=False, parent=qapp)               # what is in the store and the settings survives
    row = reopened.get(cid)
    assert (row["name"], row["color"], row["icon"], row["here"], row["active"]) == ("Wedding", "#ff6b7d", "ring", 8, True)
    reopened.removeSelection(cid, 0)
    assert reopened.get(cid)["here"] == 7
    reopened.remove(cid)
    assert not reopened.rows and reopened.activeId == ""


# ---- people ------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def people(qapp, photos):
    return PeopleModel(photos, memory=True, parent=qapp)


def test_the_faces_sidebar_lists_recurring_people_named_first(people):
    rows = people.sidebar()
    assert [r["name"] for r in rows][:4] == ["Anna", "Mark", "Lena", "Jonas"] and len(rows) == 8
    assert all(r["count"] >= 2 for r in rows) and rows[4]["init"] == "?"
    people.setQuery("mar")
    assert [r["name"] for r in people.sidebar()] == ["Mark"]
    people.setQuery("zzz")
    assert people.sidebar() == [] and people.emptyText() == "No matching people\nin this folder"


def test_the_window_filters_and_counts(people):
    assert people.stats() == {"named": 4, "unnamed": 10, "photos": 867}
    assert len(people.listPeople("", False, False)) == 12 and len(people.listPeople("", False, True)) == 14
    assert len(people.listPeople("", True, False)) == 8 and [p["name"] for p in people.listPeople("an", False, False)] == ["Anna"]


def test_naming_merging_ignoring(people):
    people.name("p5", "  Eve  ")
    assert people.card("p5")["name"] == "Eve" and people.card("p5")["init"] == "E"
    assert people.stats()["named"] == 5
    people.nameAll(["p6", "p7"], "Sam")                     # merge as the same person: one card carrying both faces' photos
    assert people.card("p6") == {"id": "p6", "name": "Sam", "count": 44 + 38, "hue": people.card("p6")["hue"], "init": "S"} and not people.card("p7")
    hidden = people.ignore(["p8", "p9"])
    assert hidden == ["p8", "p9"] and not people.card("p8") and len(people.listPeople("", False, True)) == 14 - 1 - 2
    people.restore(hidden)
    assert people.card("p8") and people.card("p9")
    people.clearNames(["p5"])
    assert people.card("p5")["name"] == ""
    assert people.photoCount(["p5", "p6"]) == 61 + 44 + 38


def test_opening_a_person_scopes_the_grid(photos, people):
    people.openPerson("p1")
    assert (photos.scopeKind, photos.scopeId) == ("person", "p1") and 0 < photos.shown < 8
    assert people.labelOf("p1") == "Anna" and people.labelOf("p5") == "Unnamed face (61 photos)"
    photos.set_scope()
    assert photos.shown == 8


# ---- best of -----------------------------------------------------------------------------------------------------------------

def test_best_of_builds_a_shortlist_of_what_is_in_view(qapp, photos):
    best = BestOfModel(photos, parent=qapp)
    assert [s["label"] for s in best.strategies()] == ["Balanced Shortlist", "Top N Overall", "Top Per Group"]
    assert best.total == 8 and best.defaultSize() == 8
    best.build("top_n", 3)
    assert (photos.scopeKind, photos.shown) == ("best", 3)
    assert best.summary
    photos.set_scope()
    assert photos.shown == 8


# ---- the windows --------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("startup", [
    "tab=coll;collDlg=new", "collDlgId=c3;collDlg=edit;collDlgTab=icon", "tab=coll;collMenu=c2", "tab=coll;filter=win;collPicker=true",
    "bestOfOpen=true;boN=4", "tab=faces;faceQuery=an", "peopleOpen=true;peopleSel=p5,p6", "peopleOpen=true;peopleFilter=unnamed", "tab=coll;scope=c:c1", "tab=faces;scope=p:p1",
], ids=lambda s: s.split(";")[-1] if ";" in s else s)
def test_windows_load_without_qml_errors(qapp, photos, startup):
    from PySide6.QtCore import Q_ARG, QMetaObject
    from PySide6.QtQml import QQmlApplicationEngine

    engine = QQmlApplicationEngine()
    warnings: list[str] = []
    engine.warnings.connect(lambda items: warnings.extend(str(i) for i in items))
    context = engine.rootContext()
    for name, obj in (("theme", Theme(parent=qapp)), ("photos", photos), ("settings", SettingsModel(memory=True, parent=qapp)),
                      ("cmdPalette", PaletteModel(memory=True, parent=qapp)), ("advFilters", FiltersModel(photos, memory=True, parent=qapp)),
                      ("batch", BatchModel(photos, memory=True, parent=qapp)), *collection_models(photos, qapp),
                      ("handoff", HandoffModel(photos, memory=True, parent=qapp)), ("transfers", TransferModel(photos, memory=True, parent=qapp)),
                      ("thumbs", ThumbService(parent=qapp)), ("startup", startup)):
        context.setContextProperty(name, obj)
    engine.addImportPath(str(shell_app.QML_DIR))
    engine.load(QUrl.fromLocalFile(str(shell_app.QML_DIR / "Main.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    window.setWidth(1440)
    window.setHeight(900)
    QMetaObject.invokeMethod(window, "applyStartup", Q_ARG("QVariant", 1440))
    pump(qapp, 0.8)
    assert not [w for w in warnings if any(k in w for k in ("TypeError", "ReferenceError", "is not a type", "Unable to assign", "non-existent", "Binding loop"))], warnings
    window.close()
