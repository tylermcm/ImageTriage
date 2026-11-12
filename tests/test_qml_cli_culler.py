from __future__ import annotations

import os
from pathlib import Path
import sys
import time

import pytest
from PIL import Image
from PySide6.QtCore import QSettings
from PySide6.QtGui import QGuiApplication as QApplication

from image_triage.cli_culler_results import native_ai_bundle
from image_triage.qml_shell.cull_native import NativeCullRun
from image_triage.qml_shell.cull_real import outcomes_from_bundle, reason_of
from image_triage.qml_shell.ai_scores import display_score


def test_native_review_does_not_become_a_reject_or_a_winner():
    document = {"schema": "aiculler-decisions/1", "files": {
        "a.jpg": {"decision": "keep", "capture": "a", "comparison_set": "set", "rank_in_set": 1, "set_size": 2, "score": None},
        "b.jpg": {"decision": "review", "capture": "b", "comparison_set": "set", "rank_in_set": 2, "set_size": 2},
        "c.jpg": {"decision": "reject", "capture": "c", "comparison_set": None, "reasons": ["quality_floor"]}},
        "warnings": [], "models": {}}
    bundle = native_ai_bundle(document, "decisions.json")
    outcomes = outcomes_from_bundle(bundle, [])
    assert reason_of(outcomes["a.jpg"]) is None
    assert reason_of(outcomes["b.jpg"]) == "native_review"
    assert reason_of(outcomes["c.jpg"]) == "native_quality"
    assert outcomes["a.jpg"].cluster == outcomes["b.jpg"].cluster
    assert all(not outcome.ranked and outcome.role != "pick" for outcome in outcomes.values())
    assert display_score(bundle.result_for_path("a.jpg")) == -1
    assert bundle.native_document == document


def test_validation_assembly_rejects_a_widgets_only_app_before_copying(tmp_path):
    import importlib.util
    script = Path(__file__).resolve().parents[1] / "scripts/build_cli_culler_validation.py"
    spec = importlib.util.spec_from_file_location("culler_validation_builder", script)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    old_app = tmp_path / "old-app"
    old_app.mkdir()
    (old_app / "ImageTriage.exe").write_bytes(b"old build")
    output = tmp_path / "new-build"
    with pytest.raises(ValueError, match="QML interface"):
        builder.build(old_app, tmp_path / "runtime", output)
    assert not output.exists()


def test_delayed_capture_scene_cannot_reset_a_running_analysis():
    from types import SimpleNamespace
    from image_triage.qml_shell.photos import PhotoModel
    from image_triage.qml_shell.cull_model import CullModel
    app = QApplication.instance() or QApplication([])
    photos = PhotoModel(parent=app)
    model = CullModel(photos, memory=True, parent=app)
    model._run = SimpleNamespace(running=True)
    model._s.update(running=True, page="run")
    model.scene("cu-prep")
    assert model._s["running"] and model._s["page"] == "run"


def test_qml_native_run_completes_and_reloads_exact_decisions(tmp_path, monkeypatch):
    python = os.environ.get("CLI_CULLER_PYTHON")
    if not python:
        pytest.skip("Requires the pinned worker interpreter")
    app = QApplication.instance() or QApplication([])
    root = Path(__file__).resolve().parents[1]
    from image_triage import cli_culler_runtime
    # Wheel identity verification has its own coverage; this fixture only supplies
    # the fields needed to launch the real worker from a source checkout.
    manifest = {"python": python, "worker": str(root / "image_triage/cli_culler_worker.py"),
                "app_path": str(root), "namespace": "qml-test", "default_settings": {"models": []}}
    monkeypatch.setattr(cli_culler_runtime, "load_native_runtime", lambda: manifest)
    monkeypatch.setattr(cli_culler_runtime, "verify_native_runtime", lambda value: None)
    monkeypatch.setenv("IMAGE_TRIAGE_CLI_CULLER_VALIDATION", "1")
    photo = tmp_path / "frame.jpg"
    Image.new("RGB", (300, 200), (40, 100, 160)).save(photo)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("ai/cli_culler_settings_json", '{"models": []}')
    run = NativeCullRun(settings)
    completed, errors, progress = [], [], []
    run.finished.connect(lambda *args: completed.append(args))
    run.failed.connect(errors.append)
    run.progress.connect(lambda *args: progress.append(args))
    run.start(folder=str(tmp_path))
    deadline = time.monotonic() + 45
    while run.running and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    if run.running:
        run.cancel()
        pytest.fail("Native QML worker did not finish within 45 seconds")
    assert not errors, errors
    assert len(completed) == 1 and progress
    bundle = completed[0][0]
    assert str(photo) in bundle.native_document["files"]
    from image_triage.ai_results import load_ai_bundle
    reopened = load_ai_bundle(bundle.source_path)
    assert reopened.native_document == bundle.native_document
    import json
    active = json.loads(Path(bundle.source_path).read_text(encoding="utf-8"))
    run_root = Path(active["outputs"]["decisions"]["path"]).parent.parent
    assert (run_root / "comparison-start/complete.json").is_file()
    run.shutdown()


def test_qml_shutdown_cancels_an_in_progress_worker(tmp_path, monkeypatch):
    # This fake worker needs only the stdlib; exercise shutdown on clean CI too.
    python = sys.executable
    app = QApplication.instance() or QApplication([])
    from image_triage import cli_culler_runtime
    worker = tmp_path / "worker.py"
    worker.write_text("import sys,json,pathlib,time\nassert sys.stdin.readline() == 'start\\n'\n"
                      "print(json.dumps({'event':'progress','stage':'maniqa','done':0,'total':500}),flush=True)\n"
                      "time.sleep(60)\n", encoding="utf-8")
    manifest = {"python": python, "worker": str(worker), "app_path": str(Path(__file__).resolve().parents[1]),
                "namespace": "qml-shutdown", "default_settings": {"models": []}}
    monkeypatch.setattr(cli_culler_runtime, "load_native_runtime", lambda: manifest)
    monkeypatch.setattr(cli_culler_runtime, "verify_native_runtime", lambda value: None)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    run = NativeCullRun(settings)
    progress, cancelled, errors = [], [], []
    run.progress.connect(lambda *args: progress.append(args))
    run.cancelled.connect(lambda: cancelled.append(True))
    run.failed.connect(errors.append)
    run.start(folder=str(tmp_path))
    deadline = time.monotonic() + 10
    while not progress and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert progress
    active = run._task.paths.active
    started = time.monotonic()
    run.shutdown()
    app.processEvents()
    assert time.monotonic() - started < 5
    assert cancelled == [True] and not errors
    assert not run.running and not active.exists()
