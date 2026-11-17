"""The QML shell's batch rename and export: rename uses the Widgets app's planner; export (which replaced resize and convert) plans names,
folders and what blocks Apply."""
from __future__ import annotations

import os
import time

import pytest
from PySide6.QtCore import QCoreApplication, QObject, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface

from image_triage.models import ImageRecord
from image_triage.qml_shell import app as shell_app
from image_triage.qml_shell.pocketdrop_model import PocketDropModel
from qml_support import collection_models
from image_triage.qml_shell.handoff_model import HandoffModel
from image_triage.qml_shell.transfer_model import TransferModel
from image_triage.qml_shell.batch_model import PREVIEW_ROW_LIMIT, BatchModel
from image_triage.qml_shell.filters_model import FiltersModel
from image_triage.qml_shell.photos import PhotoModel
from image_triage.qml_shell.theme import Theme

QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Software)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtGui import QGuiApplication as QApplication
    return QCoreApplication.instance() or QApplication([])


def pump(app, seconds=0.2):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


@pytest.fixture
def folder(tmp_path):
    """Four real files: two RAW with a JPG companion each, two plain JPGs."""
    for name in ("A1.NEF", "A1.JPG", "A2.NEF", "A2.JPG", "B1.jpg", "B2.jpg"):
        (tmp_path / name).write_bytes(b"x")
    return tmp_path


def grid(qapp, folder):
    model = PhotoModel(parent=qapp)
    rows = []
    for name, companions in (("A1.NEF", ("A1.JPG",)), ("A2.NEF", ("A2.JPG",)), ("B1.jpg", ()), ("B2.jpg", ())):
        raw = name.endswith("NEF")
        rows.append({"path": str(folder / name), "name": name, "ext": name.split(".")[1].upper(), "raw": raw, "decision": "", "rating": 0, "ai": 70,
                     "companions": tuple(str(folder / c) for c in companions)})
    model.setRows(rows, str(folder))
    return model


def test_rename_uses_the_widgets_planner_and_companions_follow(qapp, folder):
    from image_triage.batch_rename import BatchRenameRules, build_batch_rename_preview

    model = grid(qapp, folder)
    batch = BatchModel(model, parent=qapp)
    batch.begin("rename")
    batch.set("prefix", "2026_")
    batch.set("sep", "-")
    batch.set("pad", 2)
    rows = batch.rows()
    records = [ImageRecord(path=str(folder / r["name"]), name=r["name"], size=0, modified_ns=0,
                           companion_paths=tuple(str(folder / c) for c in r.get("companions", ()))) for r in model._rows]
    expected = build_batch_rename_preview(records, BatchRenameRules(prefix="2026_", sequence_enabled=True, sequence_separator="-", sequence_padding=2))
    assert [r["next"] for r in rows] == [i.target_name for i in expected.items]
    assert rows[0]["next"] == "2026_A1-01.NEF" and rows[0]["note"] == "+ 2026_A1-01.JPG follows"
    assert batch.canApply and batch.summary == "4 rename(s) | 0 unchanged | 0 error(s)"


def test_rename_blocks_apply_when_a_target_already_exists_outside_the_batch(qapp, folder):
    batch = BatchModel(grid(qapp, folder), parent=qapp)
    batch.begin("rename")
    assert batch.canApply
    (folder / "B1_003.jpg").write_bytes(b"x")        # B1 is the third photo, so it would be renamed to B1_003.jpg
    batch.set("pad", 3)
    batch.set("sep", "_")
    batch.reset()
    assert not batch.canApply and batch.errorCount == 1
    row = next(r for r in batch.rows() if r["cur"] == "B1.jpg")
    assert row["status"] == "Error" and row["err"] == "A target file already exists outside the rename batch."
    assert batch.foot == "Fix 1 error before renaming."


def test_export_names_the_files_in_a_folder_next_to_the_photos(qapp, folder):
    batch = BatchModel(grid(qapp, folder), parent=qapp)
    batch.begin("export")
    rows = batch.rows()
    assert [r["next"] for r in rows] == ["A1.jpg", "A2.jpg", "B1.jpg", "B2.jpg"]           # RAW photos export too
    assert rows[0]["note"] == str(folder / "Export") and rows[0]["status"] == "Export"
    assert batch.canApply and batch.summary == "4 file(s) | JPEG | sRGB" and batch.foot == "4 will be exported."
    batch.set("format", "png")
    batch.set("xPrefix", "web_")
    assert batch.rows()[2]["next"] == "web_B1.png"
    batch.set("sub", "")                                        # an empty folder name means "Export"
    assert batch.rows()[0]["note"] == str(folder / "Export")


def test_export_size_and_destination_problems_block_apply(qapp, folder):
    batch = BatchModel(grid(qapp, folder), parent=qapp)
    batch.begin("export")
    batch.set("sizeMode", "long")
    batch.set("long", "0")
    assert not batch.canApply and batch.foot == "Enter a size above zero."
    batch.set("long", "2048")
    batch.set("dest", "choose")
    assert not batch.canApply and batch.foot == "Choose the folder to export to."
    batch.set("folder", str(folder / "out"))
    assert batch.canApply and batch.rows()[0]["note"] == str(folder / "out")
    batch.set("folder", str(folder))
    batch.set("format", "jpg")
    rows = batch.rows()
    assert rows[2]["status"] == "Export" and rows[2]["next"] == "B1_1.jpg"     # B1.jpg is there: a number is added
    batch.set("exists", "skip")
    assert batch.rows()[2]["next"] == "B1_1.jpg"                  # never over the photo itself, whatever the setting
    (folder / "out").mkdir()
    (folder / "out" / "B1.jpg").write_bytes(b"x")
    batch.set("folder", str(folder / "out"))
    assert batch.rows()[2]["status"] == "Skip" and "skipped" in batch.summary
    batch.set("exists", "overwrite")
    assert batch.rows()[2]["status"] == "Overwrite"


def test_resize_and_convert_open_export(qapp, folder):
    batch = BatchModel(grid(qapp, folder), parent=qapp)
    batch.begin("resize")
    assert batch.mode == "export" and batch.get("sizeMode") == "long"
    batch.begin("convert")
    assert batch.mode == "export" and batch.get("sizeMode") == "original"


def test_presets_fill_the_settings_and_saved_ones_can_be_deleted(qapp, folder):
    batch = BatchModel(grid(qapp, folder), parent=qapp)
    batch.begin("export")
    names = [o["value"] for o in batch.options("xPreset")]
    assert "Web 2048 px" in names and names[0] == ""
    batch.usePreset("Print TIFF 16-bit Adobe RGB")
    assert batch.get("format") == "tif" and batch.get("depth") == "16" and batch.get("profile") == "adobergb"
    assert batch.get("xPreset") == "Print TIFF 16-bit Adobe RGB" and batch.presetIsBuiltin("Print TIFF 16-bit Adobe RGB")
    batch.set("quality", "70")
    assert batch.get("xPreset") == ""                            # changed: no longer that preset
    assert batch.savePreset("Web 2048 px") == "That name belongs to a built-in preset."
    assert batch.savePreset("Client proofs") == "" and batch.get("xPreset") == "Client proofs"
    assert not batch.presetIsBuiltin("Client proofs")
    batch.deletePreset("Client proofs")
    assert "Client proofs" not in [o["value"] for o in batch.options("xPreset")]


def test_only_the_shown_photos_are_in_the_batch_and_the_preview_is_capped(qapp, folder):
    model = grid(qapp, folder)
    batch = BatchModel(model, parent=qapp)
    batch.begin("rename")
    assert len(batch.rows()) == 4
    model.setMinRating(1)                            # nothing has a rating, so nothing is shown: the batch follows the grid
    assert batch.rows() == [] and not batch.canApply
    assert PREVIEW_ROW_LIMIT == 1200


def test_mock_mode_stages_files_so_the_planners_see_real_paths(qapp):
    model = PhotoModel(parent=qapp)
    model.loadMock(os.path.join(os.path.dirname(__file__), "..", "docs"))     # any directory: the paths only need to be strings here
    batch = BatchModel(model, memory=True, parent=qapp)
    batch.begin("rename")
    assert batch.canApply and len(batch.rows()) == 12 and batch.rows()[0]["note"].endswith("follows")
    batch.cleanup()


def test_apply_is_not_connected_and_only_reports_the_count(qapp, folder):
    batch = BatchModel(grid(qapp, folder), parent=qapp)
    seen = []
    batch.applied.connect(lambda mode, n: seen.append((mode, n)))
    batch.begin("export")
    batch.apply()
    assert seen == [("export", 4)]
    assert sorted(os.listdir(folder)) == ["A1.JPG", "A1.NEF", "A2.JPG", "A2.NEF", "B1.jpg", "B2.jpg"]     # nothing changed on disk


def test_batch_window_opens_from_the_palette_and_switches_tools_in_qml(qapp, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtTest import QTest
    from image_triage.cache import DiskThumbnailCache
    from image_triage.qml_shell.palette_model import PaletteModel
    from image_triage.qml_shell.settings_model import SettingsModel
    from image_triage.qml_shell.thumbs import ThumbService

    folder = tmp_path / "f"
    folder.mkdir()
    for name in ("A1.NEF", "A1.JPG", "A2.NEF", "A2.JPG", "B1.jpg", "B2.jpg"):
        (folder / name).write_bytes(b"x")
    model = grid(qapp, folder)
    palette = PaletteModel(memory=True, parent=qapp)
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(str(i) for i in items))
    for name, obj in (("theme", Theme(parent=qapp)), ("photos", model), ("settings", SettingsModel(memory=True, parent=qapp)), ("cmdPalette", palette),
                      ("advFilters", FiltersModel(model, memory=True, parent=qapp)), ("batch", BatchModel(model, parent=qapp)), *collection_models(model, qapp), ("handoff", HandoffModel(model, memory=True, parent=qapp)), ("transfers", TransferModel(model, memory=True, parent=qapp)),
                      ("thumbs", ThumbService(DiskThumbnailCache(tmp_path / "c"), parent=qapp)), ("startup", "")):
        engine.rootContext().setContextProperty(name, obj)
    engine.addImportPath(str(shell_app.QML_DIR))
    engine.load(QUrl.fromLocalFile(str(shell_app.QML_DIR / "Main.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    window.setWidth(1440)
    window.setHeight(900)
    window.show()
    pump(qapp, 0.4)
    card = window.findChild(QObject, "batchWindow")
    assert card is not None and card.property("shown") is False
    palette.run("tools.batch_resize")
    pump(qapp, 0.5)
    assert card.property("shown") is True and card.property("mode") == "export" and len(card.property("rows")) == 4
    QTest.keyClick(window, Qt.Key.Key_Escape)
    pump(qapp, 0.4)
    assert card.property("shown") is False
    QTest.keyClick(window, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    pump(qapp, 0.5)
    assert card.property("shown") is True and card.property("mode") == "rename"
    assert not [w for w in warnings if "ReferenceError" in w or "TypeError" in w or "is not a type" in w or "unavailable" in w], warnings
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)


def test_a_large_batch_is_planned_off_the_ui_thread_and_only_the_newest_plan_is_kept(qapp, tmp_path):
    from image_triage.qml_shell.batch_model import SYNC_LIMIT

    model = PhotoModel(parent=qapp)
    count = SYNC_LIMIT + 200
    model.setRows([{"path": str(tmp_path / f"IMG_{i:05d}.jpg"), "name": f"IMG_{i:05d}.jpg", "ext": "JPG", "raw": False, "decision": "", "rating": 0,
                    "ai": 70, "companions": ()} for i in range(count)], str(tmp_path))
    batch = BatchModel(model, parent=qapp)
    batch.begin("resize")
    assert batch.property("checking") and not batch.property("canApply")             # nothing can be applied while it is being planned
    assert "Checking" in batch.property("summary")
    end = time.monotonic() + 10
    while batch.property("checking") and time.monotonic() < end:
        pump(qapp, 0.02)
    assert not batch.property("checking") and batch.property("canApply") and f"{count} file(s)" in batch.property("summary")
    generation = batch._cgen
    for text in ("T", "Ty", "Tyl"):                                                   # typing: each change waits for the next
        batch.set("wmText", text)
    assert batch.property("checking") and batch._cgen == generation + 3
    batch._plan_arrived(generation + 1, ("export", batch._plan(batch._batch_cache, dict(batch._v), "export")))
    assert batch.property("checking")                                                  # an older plan arriving late is dropped
    end = time.monotonic() + 10
    while batch.property("checking") and time.monotonic() < end:
        pump(qapp, 0.02)
    assert batch.property("canApply") and batch._exec[0] == "export"
    batch.setMode("rename")
    assert batch.property("checking") and batch.rows() == []                          # the export rows are not shown under rename
    end = time.monotonic() + 15
    while batch.property("checking") and time.monotonic() < end:
        pump(qapp, 0.02)
    assert batch._exec[0] == "rename" and len(batch.rows()) > 0
    batch.end()


def test_the_export_planner_reads_each_folder_once_and_still_finds_names_already_there(tmp_path):
    from image_triage import export_pipeline as pipeline

    out = tmp_path / "out"
    out.mkdir()
    (out / "A.jpg").write_bytes(b"x")
    (out / "a_1.JPG").write_bytes(b"x")                                                # a name in other case is the same name on Windows
    sources = [pipeline.ExportSource(str(tmp_path / "A.png"), "A.png"), pipeline.ExportSource(str(tmp_path / "B.png"), "B.png")]
    plan = pipeline.build_plan(sources, {**pipeline.DEFAULTS, "dest": "choose", "folder": str(out), "exists": "unique"})
    assert [i.target_name for i in plan.items] == ["A_2.jpg", "B.jpg"]
    skip = pipeline.build_plan(sources, {**pipeline.DEFAULTS, "dest": "choose", "folder": str(out), "exists": "skip"})
    assert [i.status for i in skip.items] == ["Skip", "Export"]
