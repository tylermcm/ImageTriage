"""Background indexing of the whole library for semantic search, and its coverage.

Opt-in: nothing runs until the user chooses Library > Catalog > Index Library For
Search; after that, indexing continues whenever the app is idle, across restarts,
until the user pauses it.

A run has two halves:

1. Reconcile (a QRunnable, no model): every catalog folder's existing per-folder
   embeddings are copied into the derived library index (library_semantic), which
   also tells us which folders still have photos without an embedding.
2. Embed (a separate worker process, aiculler/library_index_worker.py, at below-
   normal priority): fills those folders' own feature stores. Each finished batch
   is reconciled into the library index, so coverage grows as it goes.

The worker yields to the user: any key, click or wheel in the app, or the editor
owning the GPU, pauses it at the next image; it resumes after a quiet spell.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from PySide6.QtCore import QEvent, QObject, QRunnable, Qt, QThreadPool, QTimer, Signal

from .library_semantic import (
    Coverage,
    LibrarySemanticStore,
    LibraryVectorIndex,
    describe_coverage,
    folder_feature_db_path,
)
from .proc_utils import _headless_background_popen_kwargs
from .scanner import normalized_path_key
from .semantic_plan import SEMANTIC_EMBEDDING_DIM, collect_indexable_sources, compute_semantic_model_identity

if TYPE_CHECKING:
    from .window import MainWindow


ENABLED_KEY = "library_index/enabled"
WORKER_EVENT_PREFIX = "LIBRARY_INDEX "
_BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
# Seconds without a key press, click or wheel in the app before indexing runs (or resumes).
IDLE_SECONDS = 20.0
_TICK_MS = 2000
_INPUT_EVENTS = frozenset(
    {
        QEvent.Type.KeyPress,
        QEvent.Type.MouseButtonPress,
        QEvent.Type.MouseButtonDblClick,
        QEvent.Type.Wheel,
        QEvent.Type.TouchBegin,
    }
)


@dataclass
class IdleGate:
    """Pure decision of whether background work may run now (clock injectable for tests)."""
    idle_seconds: float = IDLE_SECONDS
    clock: Callable[[], float] = time.monotonic
    last_activity: float = field(default_factory=time.monotonic)

    def touch(self) -> None:
        self.last_activity = self.clock()

    def may_run(self, *, blocked: bool) -> bool:
        return not blocked and (self.clock() - self.last_activity) >= self.idle_seconds


class _ReconcileSignals(QObject):
    finished = Signal(object)  # _ReconcileResult
    failed = Signal(str)


@dataclass
class _ReconcileResult:
    jobs: list[dict]
    coverage: Coverage
    changed: bool
    partial: bool = False  # only some folders were reconciled (a worker batch), not a full pass


class _ReconcileTask(QRunnable):
    """Copy existing per-folder embeddings into the library index; list folders still needing work."""

    def __init__(self, *, store_factory, folders: dict[str, tuple[str, list[str]]], model_identity: str,
                 skip_folder_key: str = "", only_folders: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.store_factory = store_factory
        self.folders = folders
        self.model_identity = model_identity
        self.skip_folder_key = skip_folder_key
        self.only_folders = {normalized_path_key(folder) for folder in only_folders}
        self.signals = _ReconcileSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            store: LibrarySemanticStore = self.store_factory()
            jobs: list[dict] = []
            totals: dict[str, int] = {}
            changed = False
            for folder, (root, paths) in self.folders.items():
                sources = collect_indexable_sources(paths)
                totals[root] = totals.get(root, 0) + len(sources)
                folder_key = normalized_path_key(folder)
                if self.only_folders and folder_key not in self.only_folders:
                    continue
                result = store.sync_folder(folder, sources, model_identity=self.model_identity, root=root)
                changed = changed or result.status == "synced" or result.removed > 0
                if result.status == "offline" or not result.needs_embedding:
                    continue
                if folder_key == self.skip_folder_key:
                    continue  # the open folder: the app's own per-folder indexer is on it
                jobs.append({"folder": folder, "db": str(folder_feature_db_path(folder)), "sources": sources})
            if not self.only_folders:
                store.forget_folders_except(normalized_path_key(folder) for folder in self.folders)
            coverage = store.coverage(totals, model_identity=self.model_identity)
            self.signals.finished.emit(
                _ReconcileResult(jobs=jobs, coverage=coverage, changed=changed, partial=bool(self.only_folders))
            )
        except Exception as exc:  # surfaced in the menu line, never fatal
            self.signals.failed.emit(str(exc))


class LibraryIndexController(QObject):
    """Owns library indexing runs, idle-yield, and the coverage line in the Library menu."""

    coverage_changed = Signal(object)  # Coverage
    index_changed = Signal()  # searchable vectors changed; reload LibraryVectorIndex
    worker_line = Signal(str)  # emitted from the output reader thread
    worker_exited = Signal(int)

    def __init__(self, window: "MainWindow", *, store_factory=None) -> None:
        super().__init__(window)
        self._window = window
        self._store_factory = store_factory or LibrarySemanticStore
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._gate = IdleGate()
        self._process: subprocess.Popen | None = None
        self._control_path: Path | None = None
        self._jobs_path: Path | None = None
        self._state = "idle"  # idle | reconciling | indexing | paused | complete | unavailable | error
        self._coverage: Coverage | None = None
        self._message = ""
        self._pending_folder_syncs: set[str] = set()
        self._folders: dict[str, tuple[str, list[str]]] = {}
        self._model_identity = ""
        self._index: LibraryVectorIndex | None = None
        self._index_stale = True
        self._live_signals: list[_ReconcileSignals] = []
        self._worker_log: list[str] = []
        self.worker_line.connect(self._handle_worker_line, Qt.ConnectionType.QueuedConnection)
        self.worker_exited.connect(self._handle_worker_finished, Qt.ConnectionType.QueuedConnection)
        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._tick)

    # -- Lifecycle --------------------------------------------------------------

    def install(self) -> None:
        """Called once the window is built: watch input for idle-yield and start ticking."""
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            app.aboutToQuit.connect(self.shutdown)
        self._timer.start()
        self.refresh_status_action()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt override
        # Runs for every event app-wide, so it only ever stamps a time.
        if event.type() in _INPUT_EVENTS:
            self._gate.touch()
        return False

    def enabled(self) -> bool:
        return bool(self._window._settings.value(ENABLED_KEY, False, bool))

    def start(self, checked: bool = False) -> None:
        """Index Library For Search: opt in, and start now rather than waiting for the idle delay."""
        del checked
        self._window._settings.setValue(ENABLED_KEY, True)
        if self._process is not None:
            self._write_control("run")
            self._state = "indexing"
        elif self._state not in {"reconciling"}:
            self._begin_reconcile()
        self.refresh_status_action()

    def pause(self, checked: bool = False) -> None:
        del checked
        self._window._settings.setValue(ENABLED_KEY, False)
        if self._process is not None:
            self._write_control("pause")
        self._state = "paused"
        self.refresh_status_action()

    def shutdown(self) -> None:
        self._timer.stop()
        process = self._process
        if process is None:
            return
        self._write_control("stop")
        try:
            process.wait(timeout=1.5)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass
        self._process = None

    def state(self) -> str:
        return self._state

    def coverage(self) -> Coverage | None:
        return self._coverage

    # -- Search API for the coming Find Similar / Discover UI --------------------------

    def vector_index(self) -> LibraryVectorIndex | None:
        """The loaded index, reloaded after vectors changed. Load cost grows with the library (one
        SQLite read + one matrix build); call it off the GUI thread for large libraries."""
        if self._index is None or self._index_stale:
            if not self._model_identity:
                self._model_identity = self._resolve_model_identity()
            if not self._model_identity:
                return None
            self._index = LibraryVectorIndex.load(self._store_factory(), model_identity=self._model_identity)
            self._index_stale = False
        return self._index

    # -- Scheduling --------------------------------------------------------------

    def _blocked(self) -> bool:
        # The editor owns the GPU while it is open (see records_view_controller.suspend_background_indexing).
        return bool(getattr(self._window, "_background_indexing_suspended", False))

    def _tick(self) -> None:
        if not self.enabled():
            return
        may_run = self._gate.may_run(blocked=self._blocked())
        if self._process is not None:
            wanted = "run" if may_run else "pause"
            if self._read_control() != wanted:
                self._write_control(wanted)
                self._state = "indexing" if may_run else "paused"
                self.refresh_status_action()
            return
        if may_run and self._state in {"idle", "paused"}:
            self._begin_reconcile()

    def _begin_reconcile(self, *, only_folders: tuple[str, ...] = ()) -> None:
        window = self._window
        identity = self._resolve_model_identity()
        if not identity:
            self._state = "unavailable"
            self._message = "Install the AI runtime and CLIP model to search the library"
            self.refresh_status_action()
            return
        self._model_identity = identity
        if not only_folders:
            self._folders = window._library_store.catalog_folder_sources()
            self._state = "reconciling"
        current = getattr(window, "_current_folder", "") or ""
        task = _ReconcileTask(
            store_factory=self._store_factory,
            folders=self._folders,
            model_identity=identity,
            skip_folder_key=normalized_path_key(current) if current else "",
            only_folders=only_folders,
        )
        # Queued onto the GUI thread; the controller keeps the signals object alive until it is delivered
        # (the task itself is deleted by the pool as soon as run() returns).
        task.signals.finished.connect(self._handle_reconciled, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self._handle_failed, Qt.ConnectionType.QueuedConnection)
        self._live_signals.append(task.signals)
        self._pool.start(task)
        self.refresh_status_action()

    def _handle_reconciled(self, result: _ReconcileResult) -> None:
        self._release_signals()
        partial = result.partial
        self._coverage = result.coverage
        if result.changed:
            self._index_stale = True
            self.index_changed.emit()
        self.coverage_changed.emit(result.coverage)
        if partial:
            self.refresh_status_action()
            return
        if not result.jobs:
            self._state = "complete" if result.coverage.complete else "idle"
            self.refresh_status_action()
            return
        if not self.enabled():
            self._state = "paused"
            self.refresh_status_action()
            return
        self._launch_worker(result.jobs)

    def _release_signals(self) -> None:
        if self._live_signals:
            self._live_signals.pop(0)

    def _handle_failed(self, message: str) -> None:
        self._release_signals()
        self._state = "error"
        self._message = message
        self.refresh_status_action()

    # -- Worker process --------------------------------------------------------

    def _launch_worker(self, jobs: list[dict]) -> None:
        window = self._window
        try:
            runtime = window._settings_ctl.configured_aiculler_runtime(workers=1)
        except Exception as exc:
            self._handle_failed(str(exc))
            return
        script = Path(runtime.root) / "aiculler" / "library_index_worker.py"
        if not script.exists():
            script = Path(runtime.cli_entrypoint).with_name("library_index_worker.py")
        work_dir = Path(tempfile.mkdtemp(prefix="image_triage_library_index_"))
        self._jobs_path = work_dir / "jobs.json"
        self._control_path = work_dir / "control.txt"
        self._jobs_path.write_text(
            json.dumps({"model_identity": self._model_identity, "embedding_dim": SEMANTIC_EMBEDDING_DIM, "folders": jobs}),
            encoding="utf-8",
        )
        self._write_control("run" if self._gate.may_run(blocked=self._blocked()) else "pause")
        command = [
            str(runtime.python_executable),
            str(script),
            "--jobs", str(self._jobs_path),
            "--control", str(self._control_path),
            "--clip", str(getattr(runtime, "clip_vision_model", "") or ""),
            "--device", str(getattr(runtime, "device", "auto")),
            "--threads", "2",
        ]
        fallback = getattr(runtime, "clip_fallback_vision_model", None)
        if fallback:
            command += ["--clip-fallback", str(fallback)]
        # No console window, and below-normal priority so the OS favours the app and the editor.
        popen_kwargs = _headless_background_popen_kwargs()
        if os.name == "nt":
            popen_kwargs["creationflags"] = int(popen_kwargs.get("creationflags", 0)) | _BELOW_NORMAL_PRIORITY_CLASS
        try:
            process = subprocess.Popen(
                command,
                cwd=str(runtime.root),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=self._worker_environment(runtime),
                **popen_kwargs,
            )
        except OSError as exc:
            self._handle_failed(str(exc))
            return
        self._process = process
        self._worker_log = []
        self._state = "indexing"
        threading.Thread(target=self._pump_worker_output, args=(process,), name="library-index-output", daemon=True).start()
        self.refresh_status_action()

    def _pump_worker_output(self, process: subprocess.Popen) -> None:
        """Reader thread: hands each line to the GUI thread (worker_line is queued there)."""
        assert process.stdout is not None
        for line in process.stdout:
            self.worker_line.emit(line.rstrip("\n"))
        self.worker_exited.emit(process.wait())

    def _worker_environment(self, runtime) -> dict[str, str]:
        from .ai_runtime_packages import resolve_ai_runtime_site_packages
        from .aiculler_workflow import _aiculler_pythonpath

        environment = dict(os.environ)
        environment["PYTHONUNBUFFERED"] = "1"
        environment["PYTHONPATH"] = _aiculler_pythonpath(
            Path(runtime.root),
            os.environ.get("PYTHONPATH", ""),
            runtime_site_packages=resolve_ai_runtime_site_packages(device=str(getattr(runtime, "device", "auto"))),
        )
        # Keep the worker's math libraries off most cores; the app and the editor come first.
        for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
            environment[name] = "2"
        return environment

    def _handle_worker_line(self, line: str) -> None:
        line = line.strip()
        if not line.startswith(WORKER_EVENT_PREFIX):
            if line:
                # Anything else is the worker's own output (a traceback, a runtime warning): keep the tail.
                self._worker_log = [*self._worker_log[-19:], line]
            return
        try:
            payload = json.loads(line[len(WORKER_EVENT_PREFIX):])
        except ValueError:
            return
        self._handle_worker_event(payload)

    def _handle_worker_event(self, payload: dict) -> None:
        event = payload.get("event")
        folder = str(payload.get("folder", "") or "")
        if event in {"folder_progress", "folder_done"} and folder:
            self._queue_folder_sync(folder)
        elif event == "paused":
            self._state = "paused"
            self.refresh_status_action()
        elif event == "resumed":
            self._state = "indexing"
            self.refresh_status_action()

    def _queue_folder_sync(self, folder: str) -> None:
        # Coalesce: a folder reporting several batches quickly is reconciled once.
        if folder in self._pending_folder_syncs:
            return
        self._pending_folder_syncs.add(folder)

        def run() -> None:
            folders = tuple(self._pending_folder_syncs)
            self._pending_folder_syncs.clear()
            if folders:
                self._begin_reconcile(only_folders=folders)

        QTimer.singleShot(1500, run)

    def _handle_worker_finished(self, exit_code: int) -> None:
        self._process = None
        if exit_code != 0:
            # Do not loop relaunching a worker that cannot run; say why and wait for the user.
            self._state = "error"
            self._message = self._worker_log[-1] if self._worker_log else f"indexing worker exited with code {exit_code}"
            self.refresh_status_action()
            return
        self._state = "idle"
        # A full pass recounts coverage and catches anything the batches missed.
        if self.enabled():
            QTimer.singleShot(0, self._begin_reconcile)
        self.refresh_status_action()

    def _write_control(self, state: str) -> None:
        if self._control_path is None:
            return
        try:
            self._control_path.write_text(state, encoding="utf-8")
        except OSError:
            pass

    def _read_control(self) -> str:
        if self._control_path is None:
            return ""
        try:
            return self._control_path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def _resolve_model_identity(self) -> str:
        window = self._window
        try:
            if not (window._ai_setup.ai_runtime_available() and window._ai_setup.aiculler_clip_model_available()):
                return ""
            runtime = window._settings_ctl.configured_aiculler_runtime(workers=1)
            clip = getattr(runtime, "clip_vision_model", None)
            if clip is None or not Path(clip).exists():
                return ""
            return compute_semantic_model_identity(
                clip,
                embedding_dim=SEMANTIC_EMBEDDING_DIM,
                fallback_model=getattr(runtime, "clip_fallback_vision_model", None),
            )
        except Exception:
            return ""

    # -- Status ------------------------------------------------------------------

    def status_text(self) -> str:
        labels = {
            "idle": "",
            "reconciling": "checking",
            "indexing": "indexing",
            "paused": "paused" if self.enabled() else "paused by you",
            "complete": "complete",
            "error": f"error: {self._message}",
        }
        if self._state == "unavailable":
            return f"Library search: {self._message}"
        if self._coverage is None:
            if not self.enabled():
                return "Library search: not indexed yet"
            return "Library search: checking…"
        return describe_coverage(self._coverage, state=labels.get(self._state, ""))

    def show_coverage_details(self, checked: bool = False) -> None:
        """Per-root breakdown, so a partial search result is never mistaken for the whole library."""
        del checked
        from PySide6.QtWidgets import QMessageBox

        lines = [self.status_text()]
        coverage = self._coverage
        if coverage is not None:
            for root in coverage.roots:
                percent = int(round(100 * root.indexed / root.total)) if root.total else 0
                line = f"{root.root}: {root.indexed:,} of {root.total:,} ({percent}%)"
                if root.offline:
                    line += f", {root.offline:,} on a disconnected drive"
                lines.append(line)
        lines.append("")
        lines.append(
            "Semantic search across the library only finds photos that have been indexed. "
            "Indexing runs while Image Triage is idle and pauses as soon as you use it."
        )
        QMessageBox.information(self._window, "Library Search Index", "\n".join(lines))

    def refresh_status_action(self) -> None:
        actions = getattr(self._window, "actions", None)
        if actions is None:
            return
        status = getattr(actions, "library_index_status", None)
        if status is not None:
            status.setText(self.status_text())
        start = getattr(actions, "start_library_index", None)
        pause = getattr(actions, "pause_library_index", None)
        running = self._process is not None or self._state == "reconciling"
        if start is not None:
            start.setEnabled(not (running and self.enabled()))
        if pause is not None:
            pause.setEnabled(self.enabled())
