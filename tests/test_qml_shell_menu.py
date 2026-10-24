"""The QML shell's photo and folder menus and its toasts."""
from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QCoreApplication, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface

from image_triage.qml_shell import app as shell_app
from image_triage.qml_shell.batch_model import BatchModel
from image_triage.qml_shell.collections_model import CollectionsModel
from image_triage.qml_shell.cull_model import CullModel
from image_triage.qml_shell.filters_model import FiltersModel
from image_triage.qml_shell.handoff_model import HandoffModel
from image_triage.qml_shell.menu_model import MenuModel, ToastModel
from image_triage.qml_shell.palette_model import PaletteModel
from image_triage.qml_shell.photos import PhotoModel
from image_triage.qml_shell.settings_model import SettingsModel
from image_triage.qml_shell.theme import Theme
from image_triage.qml_shell.thumbs import ThumbService
from image_triage.qml_shell.transfer_model import TransferModel
from qml_support import collection_models

QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Software)
NAMES = ["DSC_4201", "DSC_4203", "DSC_4207", "DSC_4210", "DSC_4214", "DSC_4219", "DSC_4222", "DSC_4226"]


@pytest.fixture(scope="module")
def qapp():
    return QCoreApplication.instance() or QGuiApplication([])


def pump(app, seconds=0.2):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


@pytest.fixture
def photos(qapp, tmp_path):
    model = PhotoModel(parent=qapp)
    rows = []
    for name in NAMES:
        path = tmp_path / f"{name}.jpg"
        path.write_bytes(b"x" * 2000)
        rows.append({"path": str(path), "name": name, "ext": "JPG", "raw": False, "decision": "", "rating": 3, "ai": 70})
    model.setRows(rows, str(tmp_path))
    return model


@pytest.fixture
def ctx(qapp, photos):
    collections = CollectionsModel(photos, memory=True, parent=qapp)
    cull = CullModel(photos, memory=True, parent=qapp)
    toasts = ToastModel(parent=qapp)
    menu = MenuModel(photos, collections, cull, toasts, memory=True, parent=qapp)
    requests = []
    menu.uiRequest.connect(lambda what, arg: requests.append((what, arg)))
    return menu, toasts, photos, collections, cull, requests


def keys(items):
    return [i.get("key") for i in items if i["kind"] == "row"]


# ---- toasts -------------------------------------------------------------------------------------------------------------------

def test_at_most_three_toasts_and_the_action_runs_once(qapp):
    t = ToastModel(parent=qapp)
    ran = []
    first = t.push("success", "one", "", "Undo", lambda: ran.append(1))
    for n in "two three four".split():
        t.push("info", n)
    assert [i["title"] for i in t.items] == ["two", "three", "four"]
    t.trigger(first)                                             # it already left
    assert ran == []
    last = t.push("success", "five", "", "Undo", lambda: ran.append(5))
    t.trigger(last)
    assert ran == [5] and "five" not in [i["title"] for i in t.items]


def test_toasts_leave_by_themselves_unless_still(qapp):
    t = ToastModel(parent=qapp)
    t.push("success", "short", seconds=0.05)
    pump(qapp, 0.3)
    assert t.items == []
    t.setStill(True)
    t.push("success", "stays", seconds=0.05)
    pump(qapp, 0.3)
    assert len(t.items) == 1


# ---- the photo menu -----------------------------------------------------------------------------------------------------------

def test_the_photo_menu_has_what_the_mockup_has(ctx):
    menu, *_ = ctx
    menu.openPhoto(1)
    v = menu.view
    assert v["open"] and v["kind"] == "photo"
    assert [d["key"] for d in v["items"][0]["decs"]] == ["win", "keep", "rej"]
    assert keys(v["items"]) == ["open", "edit", "with", "share", "coll", "mv", "rename", "resize", "convert", "cpath", "cname", "reveal", "del"]
    assert next(i for i in v["items"] if i.get("key") == "del")["danger"]
    menu.close()
    assert not menu.view["open"]


def test_deciding_from_the_menu_and_undoing(ctx):
    menu, toasts, photos, *_ = ctx
    menu.openPhoto(1)
    menu.act("rej")
    assert photos.get(1)["decision"] == "rej" and toasts.items[-1]["title"] == "Marked DSC_4203 as Reject" and toasts.items[-1]["action"] == "Undo"
    toasts.trigger(toasts.items[-1]["id"])
    assert photos.get(1)["decision"] == ""
    menu.openPhoto(1)
    menu.act("win")
    menu.openPhoto(1)
    assert next(d for d in menu.view["items"][0]["decs"] if d["key"] == "win")["on"]
    menu.act("win")                                              # the same again clears it
    assert photos.get(1)["decision"] == "" and toasts.items[-1]["title"] == "Cleared the decision on DSC_4203"


def test_collections_submenu_ticks_and_toggles(ctx):
    menu, toasts, photos, collections, *_ = ctx
    menu.openPhoto(1)
    menu.setSub("coll")
    sub = menu.view["subItems"]
    ticked = [i["label"] for i in sub if i.get("tick")]
    assert ticked == ["Album selects", "Print order", "Landscapes"] and sub[-1]["key"] == "coll:new"
    menu.act("coll:c2")                                          # leave Print order
    assert collections.get("c2")["count"] == 2 and toasts.items[-1]["title"] == "Removed from Print order"
    toasts.trigger(toasts.items[-1]["id"])
    assert collections.get("c2")["count"] == 3


def test_what_only_the_window_can_do_is_requested(ctx):
    menu, toasts, photos, collections, cull, requests = ctx
    menu.openPhoto(2)
    menu.act("rename")
    menu.openPhoto(2)
    menu.act("share")
    menu.openPhoto(2)
    menu.setSub("coll")
    menu.act("coll:new")
    assert requests[:2] == [("batch", "rename"), ("share", 2)] and ("newCollection", 0) in requests
    assert toasts.items[-1]["title"] == "Added DSC_4207 to PocketDrop" or any("PocketDrop" in t["title"] for t in toasts.items)


def test_what_is_not_connected_says_so_and_changes_nothing(ctx):
    menu, toasts, photos, *_ = ctx
    for key in ("del", "mv:copy", "with:default", "open"):
        menu.openPhoto(0)
        menu.act(key)
        assert toasts.items[-1]["kind"] == "warn" and "connected yet" in toasts.items[-1]["title"], key
    assert photos.get(0)["decision"] == "" and photos.shown == len(NAMES)


def test_a_flagged_frame_has_its_banner_and_restore(qapp, ctx):
    menu, toasts, photos, collections, cull, requests = ctx
    cull.scene("cu-grid")
    row = next(i for i in range(photos.shown) if photos.get(i)["name"] == "DSC_4210")
    menu.openPhoto(row)
    flag = menu.view["items"][0]
    assert flag["kind"] == "flag" and flag["label"] == "Soft"
    menu.act("flagrestore")
    assert cull.flagInfo(photos.get(row)["path"]) == {} and toasts.items[-1]["title"] == "Restored DSC_4210"
    toasts.trigger(toasts.items[-1]["id"])
    assert cull.flagInfo(photos.get(row)["path"])["label"] == "Soft"
    menu.openPhoto(row)
    menu.act("flagwhy")
    assert requests[-1] == ("review", photos.get(row)["path"])


# ---- the folder menu ----------------------------------------------------------------------------------------------------------

def test_the_folder_menu_reflects_the_view(ctx):
    menu, toasts, photos, collections, cull, requests = ctx
    menu.openFolder(True, False, "m", "name", False)
    assert next(i for i in menu.view["items"] if i.get("key") == "clear")["off"]
    menu.setSub("view")
    ticks = {i["key"]: i["tick"] for i in menu.view["subItems"] if i["kind"] == "row"}
    assert ticks == {"view:names": True, "view:badges": False, "dens:s": False, "dens:m": True, "dens:l": False}
    menu.setSub("sort")
    assert [i["label"] for i in menu.view["subItems"] if i.get("tick")] == ["Name"]
    menu.act("sort:rating")
    assert photos.get(0)["rating"] >= photos.get(photos.shown - 1)["rating"] and requests[-1] == ("sort", "rating")
    menu.openFolder(True, True, "m", "name", True)
    menu.act("fpath")
    assert toasts.items[-1]["title"] == "Folder path copied"


@pytest.mark.parametrize("scene", ["cx-photo", "cx-coll", "cx-with", "cx-move", "cx-flag", "cx-bg", "cx-sort", "tt-undo", "tt-stack", "tt-warn"])
def test_every_scene_loads_without_qml_errors(qapp, photos, scene):
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
                      ("thumbs", ThumbService(parent=qapp)), ("startup", f"ctxScene={scene}")):
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
