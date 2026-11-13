import json
import os
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from image_triage.qml_shell.cull_model import CullModel
from image_triage.qml_shell.photos import PhotoModel
from image_triage.qml_shell.native_cull_controls import NativeCullControls


def test_edit_save_reload_and_invalid_advanced_profile(tmp_path, monkeypatch):
    python = os.environ.get("CLI_CULLER_PYTHON")
    if not python:
        pytest.skip("Requires the isolated native interpreter")
    from image_triage import cli_culler_runtime
    monkeypatch.setattr(cli_culler_runtime, "load_native_runtime", lambda: {"python": python, "default_settings": {"models": []}})
    app = QApplication.instance() or QApplication([])
    model = CullModel(PhotoModel(parent=app), memory=True, parent=app)
    model._native = True
    model._settings = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    def loaded():                                   # the engine's defaults are read on a worker thread
        from PySide6.QtTest import QTest
        model._rebuild()
        for _ in range(3000):
            if model._native_controls is not None or model._native_load is None:
                break
            QTest.qWait(10)
        assert model._native_controls is not None, model._native_error

    loaded()
    assert model._native_controls.value("models.search") is True
    for feature in ("faces", "search", "ranking"):
        model.toggleNativeFeature(feature)
    assert set(model._native_controls.draft["models"]) == {"faces", "maniqa", "appeal"}
    model.setNativePreset("careful")
    assert model._native_controls.value("gates.clip_region_reject") == .5
    model.setNativeValue("bursts_keep", 0)
    model.setNativeValue("scene_keep", 3)
    from PySide6.QtTest import QTest
    def saved_profile():
        for _ in range(3000):
            if model._native_save is None:
                return
            QTest.qWait(10)
        pytest.fail("native settings validation did not finish")
    assert not model.saveNativeDraft()
    saved_profile()
    assert not model._native_error
    saved = model._settings.value("ai/cli_culler_settings_json")
    assert json.loads(saved)["bursts_keep"] == 0
    assert not model.saveNativeSettings('{"gates":{"clip_region_reject":0.01}}')
    saved_profile()
    assert model._native_error
    assert model._settings.value("ai/cli_culler_settings_json") == saved
    model._native_controls = None
    loaded()
    assert model._native_controls.value("bursts_keep") == 0
    assert model._native_controls.value("scene_keep") == 3
    assert model._native_controls.value("gates.quality_reject") == 0
    model.setNativePreset("strict")
    model.close()
    saved_profile()
    assert json.loads(model._settings.value("ai/cli_culler_settings_json"))["gates"]["clip_region_reject"] == .25


def test_cpu_and_cuda_choices_use_distinct_score_caches(tmp_path, monkeypatch):
    from image_triage import cli_culler_runtime
    from image_triage.qml_shell.cull_native import NativeCullRun
    monkeypatch.setattr(cli_culler_runtime, "load_native_runtime", lambda: {"python": __file__, "worker": __file__, "app_path": str(tmp_path), "namespace": "test", "default_settings": {"models": []}})
    app = QApplication.instance() or QApplication([])
    tasks = []
    for device in ("cpu", "cuda"):
        settings = QSettings(str(tmp_path / (device + ".ini")), QSettings.Format.IniFormat)
        settings.setValue("ai/cli_culler_device", device)
        run = NativeCullRun(settings, app)
        run._pool = SimpleNamespace(start=tasks.append)
        run.start(folder=str(tmp_path))
    assert tasks[0].device == "cpu" and tasks[1].device == "cuda"
    assert tasks[0].paths.cache != tasks[1].paths.cache


def test_fine_tuning_opens_on_click_and_is_hidden_when_reopened(tmp_path):
    from PySide6.QtCore import QObject, QPointF, Qt, QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtTest import QTest
    from image_triage.qml_shell.app import QML_DIR, _configure_qt_dll_search
    from image_triage.qml_shell.theme import Theme
    _configure_qt_dll_search()
    from PySide6 import QtQuick  # noqa: F401 - register the native window wrapper
    app = QApplication.instance() or QApplication([])
    model = CullModel(PhotoModel(parent=app), memory=True, parent=app)
    model._native = True
    model._settings = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    model._native_controls = NativeCullControls({"models": ["faces"]}, {})
    model.openCull()
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(QML_DIR))
    engine.rootContext().setContextProperty("cull", model)
    engine.rootContext().setContextProperty("theme", Theme(parent=app))
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(str(item) for item in items))
    engine.loadData(b'import QtQuick; import QtQuick.Window; Window { width: 920; height: 2200; visible: true; NativeCullOptions { width: 900 } }', QUrl.fromLocalFile(str(QML_DIR / "NativeControlsTest.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    QTest.qWait(100)
    options = window.findChild(QObject, "nativeCullOptions")
    link = window.findChild(QObject, "nativeFineTune")
    assert options is not None and not options.property("customizing")
    point = link.mapToScene(QPointF(link.width() / 2, link.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    QTest.qWait(50)
    assert options.property("customizing")
    def visual_child(item, name):
        if item.objectName() == name:
            return item
        for child in item.childItems():
            found = visual_child(child, name)
            if found is not None:
                return found
        return None
    control = visual_child(window.contentItem(), "nativeControl_bursts_keep")
    assert control is not None
    start = control.mapToScene(QPointF(44, 10)).toPoint()
    end = control.mapToScene(QPointF(110, 10)).toPoint()
    QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(window, end, 30)
    assert "bursts_keep" not in model._native_controls.draft
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
    QTest.qWait(50)
    assert model._native_controls.value("bursts_keep") >= 4
    # Profile persistence is exercised separately with the real native validator.
    model._native_controls.dirty = False
    model.close()
    model.openCull()
    QTest.qWait(50)
    assert not options.property("customizing")
    assert not warnings, warnings
    window.close()
    engine.deleteLater()
    QTest.qWait(50)


def test_search_import_failure_preserves_decisions(tmp_path, monkeypatch):
    from image_triage import ai_results, ai_workflow, cli_culler_index
    from image_triage.qml_shell.ai_scores import load_bundle
    bundle = SimpleNamespace(native_document={"files": {"a.jpg": {"decision": "keep"}}}, summary={})
    monkeypatch.setattr(ai_workflow, "existing_hidden_ai_report_dir", lambda folder: tmp_path / "active.json")
    monkeypatch.setattr(ai_results, "load_ai_bundle", lambda path: bundle)
    def failed(folder, active):
        raise ValueError("Model mismatch")
    monkeypatch.setattr(cli_culler_index, "import_active_index", failed)
    assert load_bundle(str(tmp_path)) is bundle
    assert bundle.summary["native_index_warning"] == "Model mismatch"
    assert bundle.native_document["files"]["a.jpg"]["decision"] == "keep"
