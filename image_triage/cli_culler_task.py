"""Qt runnable for isolated native culling, with owned subprocess lifetime."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import uuid

from PySide6.QtCore import QObject, QRunnable, Signal

from .cli_culler_process import WorkerOutput, WorkerProcessTree, bootstrap_script
from .cli_culler_runs import activate_generation, cull_run_paths, validate_generation
from .cli_culler_worker import PROTOCOL


class CliCullerSignals(QObject):
    started = Signal(str)
    stage = Signal(str, int, int, str)
    progress = Signal(str, str, int, int, str)
    detail = Signal(str, str)
    runtime = Signal(dict)
    finished = Signal(str, str, str)
    failed = Signal(str, str)
    cancelled = Signal(str, str)


class _Cancelled(Exception):
    pass


class _NotWritable(RuntimeError):
    """The results cannot be saved where this run keeps them. Found before the engine starts; there is no log to point at."""


def _discard_incomplete_run(root: Path) -> None:
    """Allow Windows to release recently terminated processes' file handles."""
    deadline = time.monotonic() + 1.0
    while True:
        try:
            shutil.rmtree(root)
            return
        except FileNotFoundError:
            if not root.exists():
                return
            raise
        except OSError as error:
            # Access denied, sharing/lock violation, or a directory still in
            # use. Other errors must remain visible rather than retry forever.
            if getattr(error, "winerror", None) not in (5, 32, 33, 145) or time.monotonic() >= deadline:
                raise
            time.sleep(0.02)


class CliCullerRunTask(QRunnable):
    def __init__(self, *, folders: list[Path], python: Path, worker: Path,
                 app_path: Path, settings: dict, cache_namespace: str,
                 device: str | None = None, workers: int | None = None,
                 runtime_manifest: dict | None = None, comparison_snapshot: bool = False):
        super().__init__()
        if not folders:
            raise ValueError("Analyze requires at least one folder root")
        self.folders = [path.resolve() for path in folders]
        self.folder = self.folders[0]
        self.python = python.absolute()  # Preserve virtualenv interpreter symlinks.
        self.worker = worker.resolve()
        self.app_path = app_path.resolve()
        self.settings = copy.deepcopy(settings)
        self.paths = cull_run_paths(self.folders[0], cache_namespace)
        self.device = device or ("cuda" if runtime_manifest and runtime_manifest.get("backend") == "cuda" else "cpu" if runtime_manifest and runtime_manifest.get("backend") == "cpu" else "auto")
        self.workers = workers
        self.runtime_manifest = copy.deepcopy(runtime_manifest)
        self.comparison_snapshot = comparison_snapshot or bool(
            runtime_manifest and runtime_manifest.get("comparison_snapshot")) or os.environ.get("IMAGE_TRIAGE_CLI_CULLER_VALIDATION") == "1"
        self.signals = CliCullerSignals()
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self._terminal = False
        self.run_root = self.paths.runs / uuid.uuid4().hex
        self.log_path = self.paths.root / "logs" / (self.run_root.name + ".log")

    def cancel(self):
        with self._lock:
            if not self._terminal:
                self._cancel.set()

    def _check_cancelled(self):
        if self._cancel.is_set():
            raise _Cancelled()

    def run(self):
        folder = str(self.folders[0])
        outcome, message = "failed", "Native culler did not complete"
        staged = True                               # a generation may have been started on disk, and is discarded unless it finished
        try:
            self._check_cancelled()
            self.signals.started.emit(folder)
            self._check_writable()
            self._check_cancelled()
            if self.runtime_manifest is not None:
                from .cli_culler_runtime import verify_native_runtime
                verify_native_runtime(self.runtime_manifest)
                self._check_cancelled()
                if self.device == "cuda":
                    from .cli_culler_runtime import verify_native_cuda
                    verify_native_cuda(str(self.python))
                    self._check_cancelled()
            descriptor = self._execute(folder)
            with self._lock:
                self._check_cancelled()
                activate_generation(self.paths.active, descriptor)
                self._terminal = True
            outcome, message = "finished", str(self.paths.active)
        except _Cancelled:
            outcome, message = "cancelled", "Analysis stopped"
        except _NotWritable as error:
            outcome, message, staged = "failed", str(error), False
        except Exception as error:
            outcome, message = "failed", f"{error}\nDiagnostics: {self.log_path}"
        finally:
            with self._lock:
                self._terminal = True
            if outcome != "finished" and staged:
                try:
                    _discard_incomplete_run(self.run_root)
                except OSError as error:
                    outcome = "failed"
                    message += f"\nCould not remove incomplete generation {self.run_root}: {error}\nDiagnostics: {self.log_path}"
        if outcome == "finished":
            self.signals.finished.emit(folder, message, "")
        elif outcome == "cancelled":
            self.signals.cancelled.emit(folder, message)
        else:
            self.signals.failed.emit(folder, message)

    def _check_writable(self) -> None:
        """Everything this run writes is kept beside the photos: the staged generation, the engine's score cache, the log and the
        pointer to the finished results. A folder that cannot be written to (a locked card, a read-only share) is found out now,
        as the cascade does (`cascade.task.CascadeRunTask._check_writable`), not after the engine has analysed the whole shoot.

        A file is really created and removed in each directory: permission bits do not tell what a card's lock switch or a
        share's access list will allow."""
        hidden_root = self.folder / ".image_triage_ai"
        # The cache is the engine's to create (under the root tested here); one left by an earlier run is tested as it stands.
        directories = [self.paths.root, self.paths.runs, self.log_path.parent, *([self.paths.cache] if self.paths.cache.exists() else [])]
        for directory in directories:
            try:
                directory.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=directory, prefix=".write-test-"):
                    pass
            except OSError as error:
                reason = error.strerror or str(error)
                raise _NotWritable(
                    f"The results cannot be saved in {hidden_root} ({reason}). The analysis keeps its results in a folder beside the photos, "
                    "so a read-only card or folder cannot be analysed. Copy the photos to a folder you can write to and analyse them there."
                ) from error

    def _execute(self, folder: str) -> dict:
        self.run_root.mkdir(parents=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        request = {"protocol": PROTOCOL, "folders": [str(path) for path in self.folders],
                   "settings": self.settings, "app_path": str(self.app_path),
                   "cache_root": str(self.paths.cache), "staging_root": str(self.run_root),
                   "device": self.device, "workers": self.workers}
        request_path = self.run_root / "request.json"
        request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        if self.comparison_snapshot:
            from .cli_culler_comparison import snapshot_start_cache
            self.signals.progress.emit(folder, "Preparing CLI comparison", 0, 0, "")
            snapshot_start_cache(self.paths.cache, self.run_root / "comparison-start", self._check_cancelled)
        complete = None
        runtime: dict = {}
        process = tree = None
        try:
            with self.log_path.open("w", encoding="utf-8") as diagnostics:
                if os.name == "nt":
                    tree = WorkerProcessTree()
                process = subprocess.Popen(
                    [str(self.python), "-I", "-X", "utf8",
                     str(bootstrap_script()),
                     *([tree.job_name] if os.name == "nt" else []),
                     str(self.worker), "--await-start", str(request_path)],
                    cwd=self.run_root, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=diagnostics, bufsize=0,
                    start_new_session=os.name != "nt",
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                if tree is None:
                    tree = WorkerProcessTree(process)
                # The worker cannot import models or spawn metadata children
                # until its Windows job/POSIX group is owned by this task.
                process.stdin.write(b"start\n")
                process.stdin.close()

                output = WorkerOutput(process.stdout)
                while True:
                    self._check_cancelled()
                    exited = process.poll() is not None
                    if exited:
                        tree.close()
                    line = output.readline(exited=exited)
                    if line is None:
                        continue
                    if not line:
                        break
                    event = json.loads(line.decode("utf-8", errors="strict"))
                    if not isinstance(event, dict):
                        raise ValueError("Native worker event must be an object")
                    diagnostics.write(json.dumps(event, ensure_ascii=True) + "\n")
                    diagnostics.flush()
                    if complete is not None:
                        raise ValueError("Native worker sent an event after completion")
                    kind = event.get("event")
                    if kind == "runtime":
                        runtime = event
                        self.signals.runtime.emit(event)
                    elif kind == "progress":
                        stage, done, total = event["stage"], event["done"], event["total"]
                        if not isinstance(stage, str) or not isinstance(done, int) or not isinstance(total, int):
                            raise ValueError("Invalid native progress event")
                        # total=0 is genuinely indeterminate, never a fake four-stage percentage.
                        self.signals.progress.emit(folder, stage, done, total, "")
                    elif kind == "complete":
                        complete = event
                    elif kind == "error":
                        raise RuntimeError(event.get("message", "Native worker failed"))
                    else:
                        raise ValueError(f"Unknown native worker event: {kind!r}")
                while process.poll() is None:
                    self._check_cancelled()
                    self._cancel.wait(0.05)
                if process.returncode != 0:
                    raise RuntimeError(f"Native worker exited with status {process.returncode}")
                if complete is None:
                    raise ValueError("Native worker exited without a completed generation")
                self._check_cancelled()
                descriptor = validate_generation(complete, self.run_root)
                # Where the ranking models ran, as the worker reported it. Kept with the generation so a reopened folder can still
                # say it; absent when no ranking model ran (the worker then reports no device).
                kept = {key: runtime[key] for key in ("device", "gpu", "torch_version", "engine_version") if isinstance(runtime.get(key), str) and runtime.get(key)}
                if kept:
                    descriptor["runtime"] = kept
                self.signals.detail.emit(folder, json.dumps(
                    {"warnings": complete.get("warnings", []), "models": complete.get("models", {})}))
                return descriptor
        finally:
            try:
                if tree is not None:
                    tree.close()
            finally:
                if process is not None:
                    if process.poll() is None:
                        process.kill()
                    process.wait()
                    if process.stdin and not process.stdin.closed:
                        process.stdin.close()
                    if process.stdout:
                        process.stdout.close()
