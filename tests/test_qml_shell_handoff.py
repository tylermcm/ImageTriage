"""The QML shell's handoff builder and transfer cards: the Widgets app's recipes, wording and storage; scripted (not real) transfers."""
from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QCoreApplication, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface

from image_triage.app_identity import user_settings
from image_triage.qml_shell import app as shell_app
from image_triage.qml_shell.batch_model import BatchModel
from image_triage.qml_shell.filters_model import FiltersModel
from image_triage.qml_shell.handoff_model import RECIPES_KEY, HandoffModel
from image_triage.qml_shell.palette_model import PaletteModel
from image_triage.qml_shell.photos import PhotoModel
from image_triage.qml_shell.pocketdrop_model import PocketDropModel
from qml_support import collection_models
from image_triage.qml_shell.settings_model import SettingsModel
from image_triage.qml_shell.theme import Theme
from image_triage.qml_shell.thumbs import ThumbService
from image_triage.qml_shell.transfer_model import TransferModel
from image_triage.workflows import built_in_workflow_recipes, load_saved_workflow_recipes

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
    for index in range(5):
        path = tmp_path / f"P{index}.jpg"
        path.write_bytes(b"x" * 2000)
        rows.append({"path": str(path), "name": f"P{index}", "ext": "JPG", "raw": False, "decision": "", "rating": 0, "ai": 70})
    model.setRows(rows, str(tmp_path))
    return model


def test_opens_on_the_first_built_in_recipe_with_the_folder_as_destination(qapp, photos, tmp_path):
    model = HandoffModel(photos, parent=qapp)
    model.begin()
    first = built_in_workflow_recipes()[0]
    assert (model.recipeId, model.title, model.blurb) == ("builtin:" + first.key, first.name, first.description)
    assert model.get("root") == str(tmp_path) and model.hasRoot
    assert model.countText == "5 image bundles" and model.foot == "Ready to run on 5 image bundles."


def test_options_are_the_widgets_lists(qapp, photos):
    model = HandoffModel(photos, memory=True, parent=qapp)
    load = [o["label"] for o in model.options("load")]
    assert load[:2] == ["Custom", "Built-in: Proofing JPEGs"] and "Saved: Wedding handoff" in load
    assert [o["label"] for o in model.options("resize")][:3] == ["Keep Original Size", "Small", "Medium"]
    assert [o["label"] for o in model.options("convert")] == ["Keep Original Format", "PNG", "JPEG", "WebP", "TIFF", "BMP"]
    assert [o["label"] for o in model.options("archFmt")] == ["ZIP", "7-Zip", "TAR.GZ"]


def test_fields_follow_the_content_mode(qapp, photos):
    model = HandoffModel(photos, memory=True, parent=qapp)
    assert model.enabled("resize") and model.enabled("strip") and not model.enabled("transfer") and not model.enabled("group")
    assert not model.enabled("archFmt")                      # export without "archive exported output"
    model.set("arch", True)
    assert model.enabled("archFmt")
    model.set("content", "full_bundle")
    assert model.enabled("transfer") and model.enabled("group") and model.enabled("archFmt") and not model.enabled("resize")
    assert model.recipeId == "custom"                        # editing makes it a custom recipe


def test_preview_steps_use_the_widgets_wording(qapp, photos):
    model = HandoffModel(photos, memory=True, parent=qapp)
    model.setRecipe("builtin:client_delivery")
    steps = model.steps()
    assert [s["head"] for s in steps] == ["Selected", "Export", "Package", "Destination", "Where it goes"]
    assert steps[1]["text"] == "Export primary deliverables, as JPG, using 2K."
    assert steps[2]["text"] == "Then package the exported output as a ZIP archive."
    assert steps[-1]["text"].startswith("Exports land in: D:\\Photos\\2026\\Georgia trip\\Kutaisi\\Delivery")
    model.setRecipe("saved:wedding_handoff")
    assert model.destructive and model.steps()[-1]["head"] == "Destructive"
    model.set("root", "")
    assert not model.hasRoot and model.steps()[-2]["tone"] == "warn" and model.foot.startswith("Choose a destination root")


def test_saving_uses_the_widgets_settings_key(qapp, photos):
    model = HandoffModel(photos, parent=qapp)
    model.begin()
    model.set("name", "Print proofs")
    model.set("sub", "Print")
    model.save()
    assert model.recipeId == "saved:print_proofs" and model.canDelete and model.saveLabel == "Update saved"
    saved = load_saved_workflow_recipes(str(user_settings().value(RECIPES_KEY, "", str)))
    assert [(r.key, r.name, r.destination_subfolder) for r in saved] == [("print_proofs", "Print proofs", "Print")]
    model.begin()                                            # a fresh open lists it
    assert "Saved: Print proofs" in [o["label"] for o in model.options("load")]
    model.setRecipe("saved:print_proofs")
    model.remove()
    assert model.recipeId == "custom" and load_saved_workflow_recipes(str(user_settings().value(RECIPES_KEY, "", str))) == []


def test_run_kinds(qapp, photos):
    model = HandoffModel(photos, memory=True, parent=qapp)
    assert model.runKind() == "export"
    model.set("arch", True)
    assert model.runKind() == "archive"
    model.set("content", "full_bundle")
    assert model.runKind() == "copy"
    model.set("transfer", "move")
    assert model.runKind() == "move"
    model.set("transfer", "archive")
    assert model.runKind() == "archive"


def test_run_asks_the_transfer_cards_and_a_move_asks_first(qapp, photos):
    handoff = HandoffModel(photos, memory=True, parent=qapp)
    transfers = TransferModel(photos, memory=True, parent=qapp)
    handoff.runRequested.connect(transfers.handoff)
    handoff.run()                                            # Proofing JPEGs: starts at once
    assert transfers.card["state"] == "progress" and transfers.card["headline"] == "Exporting 5 items"
    transfers.dismiss()
    handoff.setRecipe("saved:wedding_handoff")               # moves bundles: confirm first
    handoff.run()
    assert transfers.card["state"] == "confirm" and transfers.card["title"] == "Run destructive workflow?" and transfers.card["danger"]
    transfers.confirm()
    assert transfers.card["state"] == "progress" and transfers.card["headline"] == "Moving 5 items"
    transfers.dismiss()
    handoff.set("root", "")
    handoff.run()                                            # no destination: nothing happens
    assert transfers.card["state"] == ""


def test_transfer_cards_play_through_confirm_progress_done(qapp, photos):
    transfers = TransferModel(photos, memory=True, parent=qapp)
    transfers.ask("copy", 12, "D:\\From", "E:\\To")
    card = transfers.card
    assert (card["state"], card["title"], card["body"], card["yes"]) == ("confirm", "Copy 12 items?", "Copy 12 images to:", "Copy")
    assert card["hasPath"] and card["hasCompanions"] and card["companions"]
    transfers.setCompanions(False)
    assert not transfers.card["companions"]
    transfers.confirm()
    assert transfers.card["state"] == "progress" and transfers.card["route"] == "D:\\From \u2192 E:\\To"
    pump(qapp, 0.5)
    assert 0 < transfers.card["pct"] < 100
    transfers.cancel()
    assert transfers.card["cancelLabel"] == "Cancelling\u2026"
    pump(qapp, 0.4)
    assert transfers.card["state"] == ""

    transfers.ask("move", 1, "D:\\From", "E:\\To")
    assert transfers.card["title"] == "Move 1 item?" and transfers.card["body"] == "Move 1 image to:"
    transfers.show("done", "move", 12, 1.0)
    assert (transfers.card["doneTitle"], transfers.card["doneBody"]) == ("Moved 12 items", "Moved 12 images to:")
    transfers.dismiss()
    assert not transfers.active


def test_progress_runs_to_done(qapp, photos):
    transfers = TransferModel(photos, memory=True, parent=qapp)
    transfers.handoff("copy", 3, "D:\\From", "E:\\To")
    deadline = time.monotonic() + 15
    while transfers.card["state"] == "progress" and time.monotonic() < deadline:
        pump(qapp, 0.2)
    assert transfers.card["state"] == "done" and transfers.card["doneTitle"] == "Copied 3 items"


def test_windows_load_without_qml_errors(qapp, photos):
    from PySide6.QtQml import QQmlApplicationEngine

    engine = QQmlApplicationEngine()
    warnings: list[str] = []
    engine.warnings.connect(lambda items: warnings.extend(str(i) for i in items))
    context = engine.rootContext()
    for name, obj in (("theme", Theme(parent=qapp)), ("photos", photos), ("settings", SettingsModel(memory=True, parent=qapp)),
                      ("cmdPalette", PaletteModel(memory=True, parent=qapp)), ("advFilters", FiltersModel(photos, memory=True, parent=qapp)),
                      ("batch", BatchModel(photos, memory=True, parent=qapp)), *collection_models(photos, qapp),
                      ("handoff", HandoffModel(photos, memory=True, parent=qapp)), ("transfers", TransferModel(photos, memory=True, parent=qapp)),
                      ("thumbs", ThumbService(parent=qapp)), ("startup", "handoffOpen=true;hoRecipe=saved:wedding_handoff;txState=progress;txKind=copy")):
        context.setContextProperty(name, obj)
    engine.addImportPath(str(shell_app.QML_DIR))
    engine.load(QUrl.fromLocalFile(str(shell_app.QML_DIR / "Main.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    from PySide6.QtCore import Q_ARG, QMetaObject

    window.setWidth(1440)
    window.setHeight(900)
    QMetaObject.invokeMethod(window, "applyStartup", Q_ARG("QVariant", 1440))
    pump(qapp, 0.8)
    assert not [w for w in warnings if any(k in w for k in ("TypeError", "ReferenceError", "is not a type", "Unable to assign", "non-existent"))], warnings
    window.close()
