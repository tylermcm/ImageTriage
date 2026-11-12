from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading
import time

import numpy as np
from PIL import Image
import pytest
from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from image_triage.cli_culler_runs import (
    activate_generation, cull_run_paths, load_active_generation, validate_generation,
)
from image_triage.cli_culler_task import CliCullerRunTask
from image_triage import cli_culler_task

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def _task_application():
    # The process task needs only QtCore, but sibling application regressions
    # create widgets in the same interpreter and require QApplication.
    app = QApplication.instance() or QApplication([])
    yield app


def document():
    return {"schema": "aiculler-decisions/1", "files": {
        "拍摄/a.jpg": {"decision": "keep", "capture": "capture-a", "score": None}},
        "warnings": [], "models": {}}


def export(root):
    root.mkdir(parents=True)
    target = root / "decisions.json"
    target.write_text(json.dumps(document()), encoding="utf-8")
    return {"decisions": str(target), "index": None}


def task_for(tmp_path, body):
    worker = tmp_path / "fake_worker.py"
    worker.write_text(
        "import json, sys, pathlib, time, subprocess, os\n"
        "assert sys.stdin.readline() == 'start\\n'\n"
        "request = json.loads(pathlib.Path(sys.argv[-1]).read_text(encoding='utf-8'))\n"
        "root = pathlib.Path(request['staging_root'])\n" + body,
        encoding="utf-8")
    shoot = tmp_path / "shoot"
    shoot.mkdir(exist_ok=True)
    return CliCullerRunTask(folders=[shoot], python=Path(os.environ.get("CLI_CULLER_PYTHON", sys.executable)), worker=worker,
                           app_path=ROOT, settings={}, cache_namespace="test-v1")


def run_in_pool(task):
    app = QApplication.instance()
    pool = QThreadPool()
    seen = {"finished": [], "failed": [], "cancelled": [], "progress": [], "detail": []}
    for kind in seen:
        getattr(task.signals, kind).connect(lambda *args, key=kind: seen[key].append(args))
    pool.start(task)
    deadline = time.monotonic() + 30
    try:
        while pool.activeThreadCount() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        if pool.activeThreadCount():
            task.cancel()
            assert pool.waitForDone(5000), "Task failed to stop after cancellation"
            pytest.fail("Task did not finish before timeout")
        app.processEvents()
    finally:
        task.cancel()
        pool.waitForDone(5000)
    assert sum(len(seen[key]) for key in ("finished", "failed", "cancelled")) == 1
    return seen


def test_rejects_index_from_another_generation_and_corrupt_index(tmp_path):
    event = export(tmp_path / "run" / "one")
    other = tmp_path / "run" / "two"
    other.mkdir()
    event["index"] = str(other / "index.npz")
    Path(event["index"]).write_bytes(b"broken")
    with pytest.raises(ValueError, match="same generation"):
        validate_generation(event, tmp_path / "run")
    event["index"] = str(tmp_path / "run" / "one" / "index.npz")
    Path(event["index"]).write_bytes(b"broken")
    with pytest.raises(ValueError):
        validate_generation(event, tmp_path / "run")


def test_rejects_existing_decisions_from_another_run(tmp_path):
    event = export(tmp_path / "old-run")
    (tmp_path / "new-run").mkdir()
    with pytest.raises(ValueError, match="outside this run"):
        validate_generation(event, tmp_path / "new-run")


def test_index_mapping_and_atomic_reload_integrity(tmp_path):
    event = export(tmp_path / "run")
    index = tmp_path / "run" / "index.npz"
    event["index"] = str(index)
    arrays = {"meta": np.asarray(json.dumps({"schema": "aiculler-index/1"})),
              "captures": np.asarray(["capture-a"]), "files": np.asarray(["拍摄/a.jpg"]),
              "file_capture": np.asarray([0], dtype=np.int32)}
    np.savez_compressed(index, **arrays)
    active = tmp_path / "active.json"
    activate_generation(active, validate_generation(event, tmp_path / "run"))
    assert load_active_generation(active).to_document() == document()
    arrays["file_capture"] = np.asarray([4], dtype=np.int32)
    np.savez_compressed(index, **arrays)
    with pytest.raises(ValueError, match="out of range"):
        validate_generation(event, tmp_path / "run")
    with pytest.raises(ValueError, match="integrity"):
        load_active_generation(active)


def test_failed_pointer_replace_preserves_previous_generation(tmp_path, monkeypatch):
    event = export(tmp_path / "run")
    descriptor = validate_generation(event, tmp_path / "run")
    active = tmp_path / "active.json"
    activate_generation(active, descriptor)
    old = active.read_bytes()

    def unavailable(*args):
        raise OSError("disk unavailable")

    monkeypatch.setattr("image_triage.cli_culler_runs.os.replace", unavailable)
    with pytest.raises(OSError):
        activate_generation(active, descriptor)
    assert active.read_bytes() == old
    assert not list(tmp_path.glob("active-*.tmp"))


@pytest.mark.parametrize("body", [
    "print('not json', flush=True)\n",
    "print(json.dumps({'event':'complete','decisions':'missing.json','index':None}), flush=True)\n",
    "print(json.dumps({'event':'error','message':'model crash'}), flush=True)\n",
    "sys.stderr.write('native failure details\\n')\nsys.exit(9)\n",
    "print(json.dumps({'event':'progress','stage':'ingest','done':0,'total':0}), flush=True)\n",
])
def test_failure_keeps_previous_active_and_retains_diagnostics(tmp_path, body):
    task = task_for(tmp_path, body)
    task.paths.active.parent.mkdir(parents=True, exist_ok=True)
    task.paths.active.write_text("previous active generation")
    seen = run_in_pool(task)
    assert seen["failed"]
    assert task.paths.active.read_text() == "previous active generation"
    assert not task.run_root.exists()
    assert task.log_path.exists()
    if "native failure" in body:
        assert "native failure details" in task.log_path.read_text()


def test_incomplete_run_cleanup_retries_a_windows_file_lock(tmp_path, monkeypatch):
    root = tmp_path / "run"
    root.mkdir()
    (root / "request.json").write_text("{}")
    remove = cli_culler_task.shutil.rmtree
    attempts = []

    def temporarily_locked(path):
        attempts.append(path)
        if len(attempts) == 1:
            error = PermissionError("Windows has not released the directory yet")
            error.winerror = 32
            raise error
        remove(path)

    monkeypatch.setattr(cli_culler_task.shutil, "rmtree", temporarily_locked)
    cli_culler_task._discard_incomplete_run(root)
    assert len(attempts) == 2
    assert not root.exists()


def test_cleanup_failure_is_reported_without_changing_the_active_generation(tmp_path, monkeypatch):
    task = task_for(tmp_path, "print('not json', flush=True)\n")
    task.paths.active.parent.mkdir(parents=True, exist_ok=True)
    task.paths.active.write_text("previous active generation")

    def locked(path):
        error = PermissionError("directory remains locked")
        error.winerror = 32
        raise error

    monkeypatch.setattr(cli_culler_task, "_discard_incomplete_run", locked)
    failures = []
    task.signals.failed.connect(lambda *args: failures.append(args))
    task.run()
    assert len(failures) == 1
    assert "Could not remove incomplete generation" in failures[0][1]
    assert "directory remains locked" in failures[0][1]
    assert task.paths.active.read_text() == "previous active generation"
    assert task.run_root.exists()


def test_incomplete_run_cleanup_has_a_deadline(tmp_path, monkeypatch):
    def locked(path):
        error = PermissionError("directory remains locked")
        error.winerror = 32
        raise error

    clock = iter([0.0, 2.0])
    monkeypatch.setattr(cli_culler_task.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(cli_culler_task.shutil, "rmtree", locked)
    with pytest.raises(PermissionError, match="directory remains locked"):
        cli_culler_task._discard_incomplete_run(tmp_path / "run")


@pytest.mark.parametrize("stage", ["metadata", "inference"])
def test_cancellation_stops_descendant_and_discards_output(tmp_path, stage):
    task = task_for(tmp_path,
        "cache = pathlib.Path(request['cache_root'])\ncache.mkdir(parents=True)\n"
        "(cache / 'committed-score').write_text('valid cache entry')\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "(root / 'pids.json').write_text(json.dumps([os.getpid(), child.pid]))\n"
        f"print(json.dumps({{'event':'progress','stage':{stage!r},'done':0,'total':0}}), flush=True)\n"
        "time.sleep(60)\n")
    pids = []

    def cancel_when_started():
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            path = task.run_root / "pids.json"
            if path.exists():
                try:
                    pids.extend(json.loads(path.read_text()))
                except json.JSONDecodeError:
                    continue
                task.cancel()
                return
            time.sleep(0.01)
        task.cancel()

    cancel_thread = threading.Thread(target=cancel_when_started)
    cancel_thread.start()
    seen = run_in_pool(task)
    cancel_thread.join()
    assert seen["cancelled"] and not seen["finished"]
    assert len(pids) == 2
    assert not task.paths.active.exists()
    assert not task.run_root.exists()
    assert (task.paths.cache / "committed-score").read_text() == "valid cache entry"
    if sys.platform.startswith("linux"):
        for pid in pids:
            state = Path(f"/proc/{pid}/stat")
            assert not state.exists(), "The per-run supervisor must reap metadata descendants"
    elif os.name == "nt":
        import ctypes
        from ctypes import wintypes as w

        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        api.OpenProcess.restype = w.HANDLE
        api.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        api.CloseHandle.argtypes = [w.HANDLE]
        for pid in pids:
            handle = api.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
            if handle:
                try:
                    assert api.WaitForSingleObject(handle, 1000) == 0, "Culler descendant survived cancellation"
                finally:
                    api.CloseHandle(handle)


def test_complete_event_then_nonzero_exit_never_activates(tmp_path):
    body = (
        "generation = root / 'generation'\ngeneration.mkdir()\n"
        f"document = {document()!r}\n"
        "decisions = generation / 'decisions.json'\n"
        "decisions.write_text(json.dumps(document), encoding='utf-8')\n"
        "print(json.dumps({'event':'complete','decisions':str(decisions),'index':None}), flush=True)\n"
        "sys.exit(7)\n")
    task = task_for(tmp_path, body)
    seen = run_in_pool(task)
    assert seen["failed"] and not seen["finished"]
    assert not task.paths.active.exists()
    assert not task.run_root.exists()


def test_cancel_during_validation_preserves_previous_run(tmp_path, monkeypatch):
    body = (
        "generation = root / 'generation'\ngeneration.mkdir()\n"
        f"document = {document()!r}\n"
        "decisions = generation / 'decisions.json'\n"
        "decisions.write_text(json.dumps(document), encoding='utf-8')\n"
        "print(json.dumps({'event':'complete','decisions':str(decisions),'index':None}), flush=True)\n")
    task = task_for(tmp_path, body)
    task.paths.active.parent.mkdir(parents=True, exist_ok=True)
    task.paths.active.write_text("previous")

    def cancel_while_validating(event, root):
        descriptor = validate_generation(event, root)
        task.cancel()
        return descriptor

    monkeypatch.setattr("image_triage.cli_culler_task.validate_generation", cancel_while_validating)
    seen = run_in_pool(task)
    assert seen["cancelled"] and not seen["finished"]
    assert task.paths.active.read_text() == "previous"
    assert not task.run_root.exists()


def test_worker_exit_still_cleans_descendant_holding_stdout(tmp_path):
    task = task_for(tmp_path,
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "(root / 'child.pid').write_text(str(child.pid))\n"
        "sys.exit(8)\n")
    started = time.monotonic()
    seen = run_in_pool(task)
    assert seen["failed"]
    assert time.monotonic() - started < 10


def test_older_runtime_snapshot_cannot_supply_the_cleanup_bootstrap(tmp_path):
    task = task_for(tmp_path, "print('not json', flush=True)\n")
    task.app_path = tmp_path / "old-runtime-app"
    stale = task.app_path / "image_triage/cli_culler_process.py"
    stale.parent.mkdir(parents=True)
    stale.write_text("raise RuntimeError('stale bootstrap must not run')\n")
    seen = run_in_pool(task)
    assert seen["failed"]
    assert not task.run_root.exists()
    assert "stale bootstrap" not in task.log_path.read_text()
    assert "Expecting" in seen["failed"][0][1]  # the fake worker ran and its JSON was rejected


def test_cancel_before_start_does_not_launch_or_publish(tmp_path):
    task = task_for(tmp_path, "raise AssertionError('must not launch')\n")
    task.cancel()
    seen = run_in_pool(task)
    assert seen["cancelled"]
    assert not task.run_root.exists()
    assert not task.log_path.exists()


def test_successful_generation_drains_progress_before_publication(tmp_path):
    task = task_for(tmp_path,
        "generation = root / 'generation'\ngeneration.mkdir()\n"
        f"document = {document()!r}\n"
        "decisions = generation / 'decisions.json'\n"
        "decisions.write_text(json.dumps(document), encoding='utf-8')\n"
        "for i in range(150):\n"
        "    print(json.dumps({'event':'progress','stage':'ingest','done':i,'total':150}), flush=True)\n"
        "print(json.dumps({'event':'complete','decisions':str(decisions),'index':None}), end='', flush=True)\n")
    seen = run_in_pool(task)
    assert seen["finished"] and not seen["failed"]
    assert len(seen["progress"]) == 150
    assert load_active_generation(task.paths.active).to_document() == document()


def test_cancellation_during_partial_protocol_line_does_not_wait_for_eof(tmp_path):
    task = task_for(tmp_path,
        "print(json.dumps({'event':'progress','stage':'metadata','done':0,'total':0}), flush=True)\n"
        "sys.stdout.write('{\"event\":')\nsys.stdout.flush()\ntime.sleep(60)\n")
    task.signals.progress.connect(lambda *_: task.cancel())
    started = time.monotonic()
    seen = run_in_pool(task)
    assert seen["progress"]
    assert seen["cancelled"] and not seen["finished"]
    assert time.monotonic() - started < 5
    assert not task.run_root.exists()


def test_runtime_namespaces_do_not_reuse_legacy_or_other_runtime_cache(tmp_path):
    first = cull_run_paths(tmp_path, "engine-a")
    second = cull_run_paths(tmp_path, "engine-b")
    assert first.cache != second.cache
    assert first.active == second.active
    with pytest.raises(ValueError):
        cull_run_paths(tmp_path, "../legacy")


def test_real_worker_task_activates_and_reopens_native_results(tmp_path):
    python = os.environ.get("CLI_CULLER_PYTHON")
    if not python:
        pytest.skip("Set CLI_CULLER_PYTHON to the pinned engine interpreter")
    shoot = tmp_path / "shoot"
    shoot.mkdir()
    Image.new("RGB", (200, 100), "black").save(shoot / "a.jpg")
    task = CliCullerRunTask(folders=[shoot], python=Path(python),
                           worker=ROOT / "image_triage/cli_culler_worker.py",
                           app_path=ROOT, settings={}, cache_namespace="pinned-test", workers=1)
    seen = run_in_pool(task)
    assert seen["finished"], seen["failed"]
    native = load_active_generation(task.paths.active).to_document()
    assert len(native["files"]) == 1
    assert next(iter(native["files"].values()))["decision"] == "reject"
    assert seen["progress"] and seen["detail"]
