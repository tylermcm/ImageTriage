"""The Cull window and the AI Center say what really happened: what a run skipped, where it ran, what is not installed. A run that has
ended cannot disturb the next one, the mockup's scripted paths cannot touch a real folder, and the things that cannot be undone ask first."""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QMetaObject, QObject, QSettings, Qt, Q_ARG
from PySide6.QtTest import QSignalSpy, QTest

from image_triage.cascade import launch
from image_triage.cascade.task import CascadeRunSignals, Scorers
from image_triage.qml_shell import ai_center, ai_scores
from image_triage.qml_shell.ai_center import AiCenterBackend
from image_triage.qml_shell.ai_scores import AiScores
from image_triage.qml_shell.cull_model import CullModel
from image_triage.qml_shell.cull_real import CullRun
from image_triage.qml_shell.photos import PhotoModel
from test_qml_shell import _load_main
from test_qml_shell_ai_center import FakeOps, centre, find, settled, until
from test_qml_shell_cull_real import isolated, pump, qapp, wait_for, world  # noqa: F401 - fixtures


def analyse(qapp, cull, toasts):
    done = []
    cull.runFinished.connect(lambda: done.append(True))
    cull.start()
    assert wait_for(qapp, lambda: done), toasts.items
    return cull.view


def sample_rows(count=6):
    return [{"path": f"/p/{i}.jpg", "name": f"IMG_{i}", "ext": "JPG", "raw": False, "decision": "", "rating": 0, "ai": 50} for i in range(count)]


# ---- what the review page says about the run ----------------------------------------------------------------------------------------


def test_a_run_whose_ranking_could_not_start_is_not_called_ranked(qapp, world, monkeypatch):
    shoot, photos, toasts, files, cull, tmp = world

    def no_rankers(*args, **kwargs):
        def factory(cancel, progress):
            raise RuntimeError("the quality model's files are missing")
        return factory

    monkeypatch.setattr(launch, "default_scorer_factory", no_rankers)
    cull._s.update(rt="ready", gpu=True, rank=True, eyes=False, semantic=False, models={"quality": True, "appeal": True})
    view = analyse(qapp, cull, toasts)
    assert view["page"] == "rev" and cull._s["ranked"] is False  # it was asked for; it did not run
    assert view["runNotes"] == ["Ranking skipped: the quality model's files are missing"]
    assert view["revNote"].startswith("Ranking did not run")


def test_the_review_page_names_the_device_the_ranker_reported(qapp, world, monkeypatch):
    shoot, photos, toasts, files, cull, tmp = world

    class Ranker:
        active_device = "cpu"  # asked for the graphics card, the worker fell back

        def __call__(self, keys):
            return {key: 0.5 for key in keys}

    monkeypatch.setattr(launch, "default_scorer_factory", lambda *a, **k: (lambda cancel, progress: Scorers(maniqa=Ranker(), names=("maniqa",))))
    cull._s.update(rt="ready", gpu=True, rank=True, eyes=False, semantic=False, models={"quality": True, "appeal": False})
    view = analyse(qapp, cull, toasts)
    assert cull._s["ranked"] is True and "Ranking ran on the CPU." in view["revNote"]
    assert any(note.startswith("Ranking ran on the CPU: the AI runtime could not use the graphics card") for note in view["runNotes"])


def test_a_saved_report_brings_its_notes_back(qapp, world, monkeypatch):
    shoot, photos, toasts, files, cull, tmp = world
    monkeypatch.setattr(launch, "default_scorer_factory", lambda *a, **k: (lambda cancel, progress: Scorers(notes=("People were not updated: not installed.",))))
    cull._s.update(rt="ready", gpu=True, rank=True, eyes=False, semantic=False, models={"quality": True, "appeal": True})
    analyse(qapp, cull, toasts)
    other_photos = PhotoModel(parent=qapp)
    other = CullModel(other_photos, memory=False, parent=qapp, toasts=toasts)
    other._ai = None
    other_photos.loadFolder(str(shoot))
    assert wait_for(qapp, lambda: other._s["done"] and other._real)
    other.openReview()
    assert other.view["runNotes"] == ["People were not updated: not installed."]


# ---- a run that has ended cannot disturb the next one -------------------------------------------------------------------------------


def test_the_late_end_of_an_earlier_run_does_not_reset_the_results_on_screen(qapp, world):
    shoot, photos, toasts, files, cull, tmp = world
    cull._s.update(rt="missing", eyes=False, rank=False)
    analyse(qapp, cull, toasts)
    flags, shown = dict(cull._flags), len(toasts.items)
    assert flags and cull._s["done"]
    # what a cancelled run of the folder that was just left sends when it finally stops
    cull._run.cancelled.emit()
    cull._run.failed.emit("the folder went away")
    cull._run.finished.emit(None, False, False)
    pump(qapp, 0.1)
    assert cull._s["done"] and cull._real and cull._flags == flags and cull.view["page"] == "rev"
    assert len(toasts.items) == shown  # and no "could not finish" for a run nobody is waiting for


def test_a_second_analysis_cannot_start_while_the_first_is_still_stopping(qapp, world):
    shoot, photos, toasts, files, cull, tmp = world
    cull._s.update(rt="missing", eyes=False, rank=False)
    analyse(qapp, cull, toasts)
    flags = dict(cull._flags)
    cull._run._task = object()  # a cancelled task that has not said it stopped
    try:
        cull.rerun()
        cull.start()
        assert not cull.analyzing and cull.view["page"] == "prep" and cull._flags == flags
        assert toasts.items[-1]["kind"] == "error" and "still stopping" in toasts.items[-1]["title"]
        with pytest.raises(RuntimeError, match="still stopping"):
            cull._run.start(folder=str(shoot), records=[], cascade=None, blink_threshold=0.6, decision=None, protected=[])
    finally:
        cull._run._task = None


def test_a_run_object_ignores_the_signals_of_a_task_it_no_longer_runs(qapp):
    run = CullRun(qapp)
    ended: list[str] = []
    run.failed.connect(ended.append)
    old, current = CascadeRunSignals(), CascadeRunSignals()
    for signals in (old, current):
        signals.failed.connect(run._failed)
    run._task, run._signals, run.running = object(), current, True
    old.failed.emit("folder", "an old task ended late")
    assert ended == [] and run.alive and run.running
    current.failed.emit("folder", "this one is ours")
    assert ended == ["this one is ours"] and not run.alive


# ---- semantic search and People --------------------------------------------------------------------------------------------------


def test_semantic_search_that_cannot_run_says_why(qapp, world, monkeypatch):
    shoot, photos, toasts, files, cull, tmp = world
    monkeypatch.setattr(CullModel, "_clip_files", staticmethod(lambda: (None, None)))  # the search model is not installed
    cull._s.update(rt="ready", eyes=False, rank=False, semantic=True)
    view = analyse(qapp, cull, toasts)
    assert any(note.startswith("Semantic search was skipped: the search model is not installed") for note in view["runNotes"])


def test_a_search_index_that_fails_shows_its_message(qapp, world):
    shoot, photos, toasts, files, cull, tmp = world
    cull._s.update(rt="missing", eyes=False, rank=False)
    analyse(qapp, cull, toasts)
    cull._semantic_token, cull._semantic_task = 4, object()
    cull._semantic_failed(str(shoot), 3, "an older index")  # replaced by a newer one: not this run's
    assert cull._semantic_task is not None and cull.view["runNotes"] == []
    cull._semantic_failed(str(shoot), 4, "onnxruntime could not be loaded")
    assert cull._semantic_task is None
    assert cull.view["runNotes"] == ["Semantic search could not finish: onnxruntime could not be loaded"]
    cull._face_token, cull._face_task = 2, object()
    cull._face_failed(str(shoot), 2, "the face finder exited")
    assert "People were not updated: the face finder exited" in cull.view["runNotes"]
    assert cull.view["tiles"][-1]["l"] == "People · not updated"


def test_the_people_pass_is_skipped_with_a_note_when_the_recognizer_is_missing(qapp, world, monkeypatch):
    from image_triage.aiculler_workflow import aiculler_db_path, build_aiculler_workflow_paths

    shoot, photos, toasts, files, cull, tmp = world
    cull._s.update(rt="missing", eyes=False, rank=False)
    analyse(qapp, cull, toasts)
    model, tokenizer = tmp / "model.onnx", tmp / "tokenizer.json"
    model.write_bytes(b"")
    tokenizer.write_bytes(b"")
    monkeypatch.setattr(CullModel, "_clip_files", staticmethod(lambda: (model, tokenizer)))
    database = aiculler_db_path(build_aiculler_workflow_paths(str(shoot), resolve=False))
    database.parent.mkdir(parents=True, exist_ok=True)
    database.write_bytes(b"")
    monkeypatch.setattr(launch, "face_recognizer_missing", lambda: "the face recognizer (about 166 MB) is not installed.")
    cull._start_face(str(shoot))
    assert getattr(cull, "_face_task", None) is None  # nothing was started, so nothing can download it behind the person's back
    assert cull.view["runNotes"] == ["People were not updated: the face recognizer (about 166 MB) is not installed."]


def test_the_semantic_switch_is_honoured_with_the_eyes_check_on_and_people_wait_for_the_cull(qapp, world, monkeypatch):
    shoot, photos, toasts, files, cull, tmp = world
    monkeypatch.setattr(launch, "default_scorer_factory", lambda *a, **k: (lambda cancel, progress: Scorers()))
    indexed, people = [], []
    monkeypatch.setattr(cull, "_start_semantic", lambda records: indexed.append(len(records)))
    monkeypatch.setattr(cull, "_start_face", lambda folder: people.append(cull.analyzing))
    cull._s.update(rt="ready", eyes=True, faceOk=True, rank=False, semantic=False)
    view = analyse(qapp, cull, toasts)
    assert indexed == []  # switched off means off, whatever the eyes check does
    assert people == [False]  # the People pass started once, after the cull had ended (it reads the faces the cull found)
    assert any("Semantic search is switched off" in note for note in view["runNotes"])

    cull.rerun()
    cull._s.update(semantic=True)
    analyse(qapp, cull, toasts)
    assert indexed == [4] and people == [False, False]


# ---- the AI Center -----------------------------------------------------------------------------------------------------------------


def _with_extras(**changes) -> FakeOps:
    ops = FakeOps(**changes)
    ops.raw["models"]["search"] = {"state": "missing", "mb": 98, "problem": "TinyCLIP is not downloaded yet."}
    ops.raw["models"]["people"] = {"state": "missing", "mb": 166, "problem": "AdaFace is not downloaded yet."}
    sizes = {"face": 4, "quality": 543, "appeal": 2130, "search": 98, "people": 166}
    ops.bundle_mb = lambda key: sizes[key]
    return ops


def test_the_search_model_and_the_face_recognizer_can_be_installed_from_the_center(qapp):
    from image_triage.ai_manifest import MODEL_BUNDLES
    from image_triage.cascade.rankers import register_bundles

    register_bundles()
    assert ai_center.MODELS["search"] == "tinyclip" and ai_center.MODELS["people"] == "faces_identity"
    assert all(bundle in MODEL_BUNDLES for bundle in ai_center.MODELS.values())  # the model store knows every one of them

    ops = _with_extras(installed=["cpu"], torch=["cpu"])
    ops.raw["models"]["face"]["state"] = "ready"
    cull = CullModel(PhotoModel(parent=qapp), memory=False, ai=AiCenterBackend(ops, parent=qapp), parent=qapp)
    until(qapp, lambda: cull.view["center"]["rtState"] == "Installed")
    rows = {m["key"]: m for m in cull.view["center"]["models"]}
    assert rows["search"]["btn"] == "Install" and "98 MB" in rows["search"]["desc"]
    assert rows["people"]["btn"] == "Install" and "166 MB" in rows["people"]["desc"]
    # and the Cull window does not promise what is not installed
    assert not cull.view["semantic"]["on"] and "not installed" in cull.view["semantic"]["desc"]
    cull._set(page="run")
    assert any("People will not be updated" in note for note in cull.view["notes"])
    cull.modelAction("search")
    until(qapp, lambda: ("install", "search") in ops.calls)
    settled(qapp, cull._ai)
    until(qapp, lambda: cull.view["semantic"]["on"])


def test_repair_installs_the_graphics_card_build_it_promises(qapp):
    ops = FakeOps(installed=["cpu"], torch=["cpu"], gpu="NVIDIA GeForce RTX 4070")
    cull = CullModel(PhotoModel(parent=qapp), memory=False, ai=AiCenterBackend(ops, parent=qapp), parent=qapp)
    until(qapp, lambda: cull.view["center"]["rtState"] == "Installed")
    assert "Repair the runtime to add the graphics card build" in cull.view["center"]["gpuDesc"]
    assert cull._ai.variant() == "cpu" and cull._ai.variant(repair=True) == "gpu"
    plan = cull.setupPlan()
    assert plan["title"] == "Repair the AI runtime?" and "the build that uses your graphics card" in plan["body"] and "2.9 GB" in plan["body"]
    cull.setupRuntime()
    until(qapp, lambda: ("runtime", "gpu", True) in ops.calls)
    settled(qapp, cull._ai)


def test_a_repair_without_a_graphics_card_stays_on_the_cpu_build(qapp):
    ops = FakeOps(installed=["cpu"], torch=["cpu"])
    ai = AiCenterBackend(ops, parent=qapp)
    ai.refresh()
    until(qapp, lambda: ai.state()["checked"])
    assert ai.variant(repair=True) == "cpu"


def test_check_again_asks_the_driver_for_the_graphics_card_again(qapp, monkeypatch):
    from image_triage import ai_platform

    answers = iter(["Old Card", "New Card"])
    monkeypatch.setattr(ai_platform, "_gpu_name", None)
    monkeypatch.setattr(ai_platform, "_query_nvidia_gpu_name", lambda: next(answers))
    assert ai_platform.nvidia_gpu_name() == "Old Card" and ai_platform.nvidia_gpu_name() == "Old Card"  # remembered for the run
    ai_platform.forget_gpu_name()
    assert ai_platform.nvidia_gpu_name() == "New Card"

    ops = FakeOps()
    ops.forgot = 0
    ops.forget_gpu = lambda: setattr(ops, "forgot", ops.forgot + 1)
    ai = AiCenterBackend(ops, parent=qapp)
    ai.recheck()
    until(qapp, lambda: ai.state()["checked"])
    assert ops.forgot == 1


def test_the_setup_text_does_not_promise_that_a_cancelled_download_is_kept(qapp):
    ops = FakeOps()
    cull = CullModel(PhotoModel(parent=qapp), memory=False, ai=AiCenterBackend(ops, parent=qapp), parent=qapp)
    until(qapp, lambda: cull.view["center"]["rtState"] == "Not set up")
    ops.gate.clear()
    cull.setupRuntime()
    until(qapp, lambda: cull.view["center"]["rtInstalling"])
    text = cull.view["center"]["rtDesc"]
    assert "kept" not in text and "downloads it again from the start" in text
    cull.cancelSetup()
    ops.gate.set()
    settled(qapp, cull._ai)


# ---- the folder's AI data ---------------------------------------------------------------------------------------------------------


def test_the_folders_ai_data_can_be_deleted(qapp, world):
    from image_triage.ai_workflow import build_ai_workflow_paths

    shoot, photos, toasts, files, cull, tmp = world
    cull._s.update(rt="missing", eyes=False, rank=False)
    analyse(qapp, cull, toasts)
    paths = build_ai_workflow_paths(str(shoot))
    assert paths.report_dir.exists() and paths.artifacts_dir.exists()
    assert "faces" in cull.deleteDataText()["body"] and "cannot be undone" in cull.deleteDataText()["body"]

    cull._s["running"] = True  # an analysis is using those files
    cull.deleteFolderData()
    assert paths.report_dir.exists() and toasts.items[-1]["kind"] == "error"
    cull._s["running"] = False

    reread = []
    cull.runFinished.connect(lambda: reread.append(True))
    cull.deleteFolderData()
    assert wait_for(qapp, lambda: reread)
    assert not paths.report_dir.exists() and not paths.artifacts_dir.exists()
    assert not cull._s["done"] and cull._flags == {} and toasts.items[-1]["kind"] == "success"
    assert sorted(p.name for p in shoot.glob("*.jpg")) == ["a.jpg", "b.jpg", "c.jpg", "z.jpg"]  # the photos are all still there


# ---- the grid ----------------------------------------------------------------------------------------------------------------------


def test_the_grid_is_told_about_flags_only_when_they_change(qapp, monkeypatch):
    photos = PhotoModel(parent=qapp)
    photos.setRows(sample_rows(), "/p")
    cull = CullModel(photos, memory=True, parent=qapp)
    spy = QSignalSpy(cull.gridChanged)
    cull._set(running=True, page="run")
    assert spy.count() == 1  # the chip turns to "Analyzing"
    for step in range(5):
        cull._set(stage=1, p=step / 5)  # progress: nothing a grid cell shows has changed
    assert spy.count() == 1
    cull._flags = {"/p/1.jpg": "soft"}
    cull._set(running=False, done=True)
    assert spy.count() == 2
    monkeypatch.setattr(photos, "collect", lambda **kw: pytest.fail("a grid cell's flag must not copy every row"))
    assert cull.gridFlag("/p/1.jpg") == {"label": "Soft", "color": "#d9b650"} and cull.gridFlag("/p/2.jpg") == {}
    revision = cull.flagRev
    photos.setDecision(1, "rej")  # a decided frame loses its flag in the grid
    assert cull.flagRev > revision and cull.gridFlag("/p/1.jpg") == {}


# ---- the mockup's scripted paths stay on the sample page ----------------------------------------------------------------------------


def test_a_scene_cannot_write_decisions_into_a_real_folder(qapp, world):
    shoot, photos, toasts, files, cull, tmp = world
    cull.scene("cu-done")
    assert photos.rejCount == 0 and cull._flags == {} and not cull._s["done"]
    assert cull.view["window"] == ""


def test_a_real_folder_never_gets_the_scripted_run(qapp):
    photos = PhotoModel(parent=qapp)
    photos.setRows(sample_rows(), "/p")  # no scanner records: nothing the cascade could read
    cull = CullModel(photos, memory=False, ai=AiCenterBackend(FakeOps(), parent=qapp), parent=qapp)
    cull.start()
    assert not cull.analyzing and not cull._timer.isActive() and cull.view["page"] == "prep"


# ---- the native culler --------------------------------------------------------------------------------------------------------------


def test_native_settings_that_cannot_be_read_are_not_read_again_on_every_redraw(qapp, tmp_path, monkeypatch):
    from image_triage import cli_culler_runtime

    asked = []

    def missing():
        asked.append(1)
        raise ValueError("Native runtime is missing python")

    monkeypatch.setattr(cli_culler_runtime, "load_native_runtime", missing)
    model = CullModel(PhotoModel(parent=qapp), memory=True, parent=qapp)
    model._native = True
    model._settings = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    def load_ended():                                  # the settings are read on a worker thread: wait for its answer to arrive
        for _ in range(400):
            if model._native_load is None:
                return
            QTest.qWait(5)
        raise AssertionError("the native settings load never ended")

    model._rebuild()
    load_ended()
    for _ in range(5):
        model._rebuild()
    load_ended()
    assert len(asked) == 1 and "missing python" in model.view["nativeError"] and not model.view["nativeLoading"]
    model.openCull()  # opening the window is a reason to look again
    load_ended()
    assert len(asked) == 2
    assert not model.saveNativeDraft() and model._settings.value("ai/cli_culler_settings_json") is None  # an empty profile is never saved over the real one
    load_ended()
    model.shutdown()


def test_a_native_run_reports_its_warnings_and_its_device(qapp, tmp_path, monkeypatch):
    from image_triage import ai_results, cli_culler_runtime
    from image_triage.qml_shell.cull_native import NativeCullRun

    monkeypatch.setattr(cli_culler_runtime, "load_native_runtime", lambda: {"python": __file__, "worker": __file__, "app_path": str(tmp_path), "namespace": "test", "default_settings": {"models": []}})
    monkeypatch.setattr(ai_results, "load_ai_bundle", lambda path: SimpleNamespace(native_document={"models": {"maniqa": {}}, "files": {}}))
    run = NativeCullRun(QSettings(str(tmp_path / "native.ini"), QSettings.Format.IniFormat), qapp)
    tasks = []
    run._pool = SimpleNamespace(start=tasks.append)
    ended = []
    run.finished.connect(lambda bundle, eyes, ranked: ended.append(ranked))
    run.start(folder=str(tmp_path))
    with pytest.raises(RuntimeError):
        run.start(folder=str(tmp_path))  # the first one has not ended
    signals = tasks[0].signals
    signals.runtime.emit({"event": "runtime", "device": "cuda", "gpu": "Test Card 9000"})
    signals.detail.emit(str(tmp_path), json.dumps({"warnings": ["3 files could not be read"], "models": {}}))
    signals.finished.emit(str(tmp_path), "active.json", "")
    assert wait_for(qapp, lambda: ended, 5)
    assert ended == [True] and run.notes == ["3 files could not be read"] and run.device_text == "the graphics card (Test Card 9000)"
    assert not run.alive


# ---- the saved report is read once ---------------------------------------------------------------------------------------------------


def test_two_readers_of_one_report_share_a_single_read(monkeypatch):
    reads, release = [], threading.Event()

    def slow(folder):
        reads.append(folder)
        release.wait(5)
        return SimpleNamespace(folder=folder)

    stamp = ["v1"]
    monkeypatch.setattr(ai_scores, "_shared_entry", None)
    monkeypatch.setattr(ai_scores, "load_bundle", slow)
    monkeypatch.setattr(ai_scores, "_report_stamp", lambda folder: stamp[0])
    got = []
    threads = [threading.Thread(target=lambda: got.append(ai_scores.load_bundle_shared("/shoot"))) for _ in range(2)]
    for thread in threads:
        thread.start()
    time.sleep(0.2)
    release.set()
    for thread in threads:
        thread.join(5)
    assert reads == ["/shoot"] and len(got) == 2 and got[0] is got[1]
    stamp[0] = "v2"  # a new analysis wrote a new report
    assert ai_scores.load_bundle_shared("/shoot") is not got[0] and len(reads) == 2


def test_opening_a_folder_reads_its_report_once_for_the_window_and_the_inspector(qapp, world, monkeypatch):
    shoot, photos, toasts, files, cull, tmp = world
    cull._s.update(rt="missing", eyes=False, rank=False)
    analyse(qapp, cull, toasts)
    reads = []
    real = ai_scores.load_bundle
    monkeypatch.setattr(ai_scores, "_shared_entry", None)
    monkeypatch.setattr(ai_scores, "load_bundle", lambda folder: (reads.append(folder), real(folder))[1])
    other_photos = PhotoModel(parent=qapp)
    other = CullModel(other_photos, memory=False, parent=qapp, toasts=toasts)
    other._ai = None
    scores = AiScores(other_photos, parent=qapp)
    other_photos.loadFolder(str(shoot))
    assert wait_for(qapp, lambda: other._s["done"] and scores._state == "ready")
    assert len(reads) == 1


# ---- the windows ask before what cannot be undone ------------------------------------------------------------------------------------


def test_clear_cancel_and_delete_ask_first(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path)
    window.setWidth(1440)
    window.setHeight(900)
    photos = engine.rootContext().contextProperty("photos")
    photos.setRows(sample_rows(20), str(tmp_path))
    real = CullModel(photos, memory=False, ai=AiCenterBackend(FakeOps(), parent=qapp), parent=qapp)
    engine.rootContext().setContextProperty("cull", real)
    until(qapp, lambda: real.view["center"]["rtState"] != "Checking…")
    real._flags = {row["path"]: "soft" for row in photos.all_rows()[:3]}
    real._set(done=True)
    real.openCenter()
    pump(qapp, 0.9)

    click = lambda name: QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre(window, name))
    click("aiClearButton")
    pump(qapp, 0.5)
    card = window.findChild(QObject, "aiClearCard")
    assert card.property("opened") and real._s["done"] and len(real._flags) == 3  # nothing is cleared until the card is answered
    QMetaObject.invokeMethod(card, "accepted", Q_ARG(bool, False))
    assert not real._s["done"] and real._flags == {}
    card.close()
    pump(qapp, 0.4)

    assert find(window, "aiFolderData").property("visible")
    body = find(window, "aiCenterWindow")
    assert body is not None
    delete = window.findChild(QObject, "aiDeleteDataCard")
    QMetaObject.invokeMethod(find(window, "aiCenterWindow"), "deletePressed")
    pump(qapp, 0.5)
    assert delete.property("opened") and "Delete the AI data" in delete.property("title")
    delete.close()
    pump(qapp, 0.4)

    real.close()
    real._set(window="cull", page="run", running=True)
    pump(qapp, 0.9)
    click("cullCancelButton")
    pump(qapp, 0.5)
    cancel = window.findChild(QObject, "cullCancelCard")
    assert cancel.property("opened") and real.analyzing  # still running until the card is answered
    QMetaObject.invokeMethod(cancel, "accepted", Q_ARG(bool, False))
    assert not real.analyzing and real.view["page"] == "prep"
    assert not [w for w in warnings if "ReferenceError" in w or "TypeError" in w or "Required property" in w], warnings
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)


def test_the_review_page_shows_the_runs_notes(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path)
    window.setWidth(1440)
    window.setHeight(900)
    photos = engine.rootContext().contextProperty("photos")
    photos.setRows(sample_rows(20), str(tmp_path))
    real = CullModel(photos, memory=False, ai=AiCenterBackend(FakeOps(), parent=qapp), parent=qapp)
    engine.rootContext().setContextProperty("cull", real)
    real._flags = {row["path"]: "soft" for row in photos.all_rows()[:3]}
    real._stacks = [[row] for row in photos.all_rows()]
    real._real = True
    real._run_notes = ["Ranking skipped: the quality model's files are missing"]
    real._set(done=True, window="cull", page="rev")
    pump(qapp, 0.9)
    notes = find(window, "cullRunNotes")
    assert notes is not None and notes.property("visible") and notes.property("height") > 20

    def texts(item):
        found = [item.property("text")] if isinstance(item.property("text"), str) else []
        for child in item.childItems():
            found += texts(child)
        return found

    assert "Ranking skipped: the quality model's files are missing" in texts(notes)
    assert not [w for w in warnings if "ReferenceError" in w or "TypeError" in w or "Required property" in w], warnings
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)
