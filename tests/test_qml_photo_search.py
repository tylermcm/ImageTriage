from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QObject, QPointF, QSettings, Qt
from PySide6.QtTest import QTest

from image_triage.qml_shell.photos import PhotoModel
from image_triage.qml_shell.search_model import SearchModel
from test_qml_shell import _load_main, pump, qapp  # noqa: F401


def rows(folder):
    return [{"path": str(folder / name), "name": name, "raw": False, "rating": 3,
             "decision": decision, "ai": 0, "ext": "JPG", "companions": ()}
            for name, decision in (("red_lantern.jpg", "win"), ("beach.jpg", ""), ("DSC_0001.jpg", "rej"))]


def test_filename_and_semantic_matches_intersect_other_filters_and_scopes(qapp, tmp_path):
    photos = PhotoModel()
    photos.setRows(rows(tmp_path), str(tmp_path))
    photos.setSearch("lantern")
    assert photos.shown == 1 and photos.get(0)["name"] == "red_lantern.jpg"
    photos.search_results([str(tmp_path / "DSC_0001.jpg")], "Ready")
    assert photos.shown == 2
    photos.setFilter("win")
    assert photos.shown == 1
    photos.set_scope("collection", "test", lambda row: row["name"] == "beach.jpg")
    assert photos.shown == 0
    photos.clearFilters()
    assert photos.searchText == "" and photos.shown == 1


def test_stale_results_and_missing_index_do_not_replace_current_query(qapp, tmp_path):
    photos = PhotoModel()
    photos.setRows(rows(tmp_path), str(tmp_path))
    search = SearchModel(photos, db_path=lambda folder: tmp_path / "missing.sqlite")
    photos.setSearch("lantern")
    stale = search._token
    photos.setSearch("beach")
    search._finished(str(tmp_path), stale, {"path_keys": [str(tmp_path / "DSC_0001.jpg")]})
    assert photos.shown == 1 and photos.get(0)["name"] == "beach.jpg"
    search._timer.stop()
    search._start()
    assert "filenames" in photos.searchStatus
    photos.setRows(rows(tmp_path), str(tmp_path / "other"))
    assert photos.searchText == "" and photos.shown == 3
    search._finished(str(tmp_path), stale, {"path_keys": []})
    assert photos.shown == 3
    search.shutdown()


def test_search_default_upgrades_old_manifest_without_mutating_saved_choice(tmp_path):
    from image_triage.cli_culler_runtime import native_settings
    manifest = {"default_settings": {"models": ["faces", "maniqa", "appeal"], "refine_scenes": True}}
    settings = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    assert "search" in native_settings(settings, manifest)["models"]
    assert manifest["default_settings"]["models"] == ["faces", "maniqa", "appeal"]
    settings.setValue("ai/cli_culler_settings_json", '{"models":[]}')
    assert native_settings(settings, manifest) == {"models": []}


def test_search_uses_installed_bundle_and_preserves_explicit_model_paths(tmp_path, monkeypatch):
    from image_triage import ai_model, aiculler_workflow
    from image_triage.semantic_runtime import default_search_runtime
    runtime = aiculler_workflow.default_aiculler_runtime()
    (tmp_path / "onnx").mkdir()
    model = tmp_path / "onnx/model.onnx"
    model.touch()
    (tmp_path / "tokenizer.json").touch()
    for key in ("IMAGE_TRIAGE_AICULLER_CLIP_TEXT", "IMAGE_TRIAGE_AICULLER_CLIP_VISION", "IMAGE_TRIAGE_AICULLER_TOKENIZER", "IMAGE_TRIAGE_AICULLER_MODEL_ROOT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(aiculler_workflow, "default_aiculler_runtime", lambda: runtime)
    monkeypatch.setattr(ai_model, "resolve_aiculler_clip_model_installation", lambda: SimpleNamespace(install_dir=tmp_path))
    assert default_search_runtime().clip_text_model == model
    monkeypatch.setenv("IMAGE_TRIAGE_AICULLER_CLIP_TEXT", "explicit-model.onnx")
    assert default_search_runtime() is runtime


def test_photo_search_input_and_commands_are_independent(qapp, tmp_path):
    from image_triage.qml_shell.app import _configure_qt_dll_search
    _configure_qt_dll_search()
    engine, window, warnings = _load_main(qapp, tmp_path)
    photos = engine.rootContext().contextProperty("photos")
    photos.setRows(rows(tmp_path), str(tmp_path))
    window.setWidth(1440)
    window.setHeight(900)
    pump(qapp, .4)
    field = window.findChild(QObject, "photoSearchInput")
    palette = window.findChild(QObject, "commandPalette")
    point = field.mapToScene(QPointF(30, field.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    for char in "lantern":
        QTest.keyClick(window, char)
    pump(qapp, .1)
    assert photos.searchText == "lantern" and photos.shown == 1
    assert not palette.property("shown")
    QTest.keyClick(window, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    pump(qapp, .3)
    assert palette.property("shown") and photos.searchText == "lantern"
    QTest.keyClick(window, Qt.Key.Key_Escape)
    pump(qapp, .3)
    QTest.keyClick(window, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
    pump(qapp, .1)
    QTest.keyClick(window, Qt.Key.Key_Escape)
    assert photos.searchText == "" and photos.shown == 3
    assert not [w for w in warnings if "ReferenceError" in w or "TypeError" in w], warnings
    window.close()
    engine.deleteLater()
    pump(qapp, .2)


def test_imported_native_vectors_are_searchable_in_qml_grid(qapp, tmp_path, monkeypatch):
    import json
    import numpy as np
    from image_triage.cli_culler_index import import_native_index
    from image_triage.unified_search_task import UnifiedSearchTask
    folder_rows = rows(tmp_path)
    for row in folder_rows:
        Path(row["path"]).write_bytes(b"synthetic")
    index = tmp_path / "index.npz"
    np.savez(index, meta=np.asarray(json.dumps({"schema": "aiculler-index/1"})),
        files=np.asarray([row["path"] for row in folder_rows]), file_capture=np.asarray([0, 1, 2]),
        clip_capture=np.asarray([0, 1, 2]), clip=np.asarray([[1., 0.], [0., 1.], [1., 0.]], dtype=np.float32))
    db = tmp_path / "host.sqlite"
    import_native_index(index, db, semantic_identity="test")
    model = tmp_path / "text.onnx"
    model.touch()
    monkeypatch.setattr(UnifiedSearchTask, "_cached_text_encoder", lambda *args, **kwargs: SimpleNamespace(encode=lambda text: np.array([1., 0.])))
    photos = PhotoModel()
    photos.setRows(folder_rows, str(tmp_path))
    search = SearchModel(photos, db_path=lambda folder: db,
        runtime_loader=lambda: SimpleNamespace(clip_text_model=model, tokenizer=model))
    photos.setSearch("lanterns")
    search._timer.stop()
    search._start()
    for _ in range(100):
        pump(qapp, .02)
        if search._task is None:
            break
    assert search._task is None
    assert {photos.get(i)["name"] for i in range(photos.shown)} == {"red_lantern.jpg", "DSC_0001.jpg"}
    assert "contents" in photos.searchStatus
    search.shutdown()
