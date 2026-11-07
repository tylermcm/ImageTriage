"""Backend idle gating and library semantic-index reconciliation."""
from __future__ import annotations
import os
import threading
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable
from PySide6.QtCore import QObject, QRunnable, Signal
from .library_semantic import Coverage, LibrarySemanticStore, describe_coverage, folder_feature_db_path
from .scanner import normalized_path_key
from .semantic_plan import collect_indexable_sources
IDLE_SECONDS = 20.0

@dataclass
class IdleGate:
    """Pure decision of whether background work may run now (clock injectable for tests).

    ``input_idle`` (seconds since the last key or mouse input, from the OS) replaces the
    app's own activity stamps when given."""
    idle_seconds: float = IDLE_SECONDS
    clock: Callable[[], float] = time.monotonic
    last_activity: float = field(default_factory=time.monotonic)
    input_idle: Callable[[], float] | None = None

    def touch(self) -> None:
        self.last_activity = self.clock()

    def may_run(self, *, blocked: bool) -> bool:
        if blocked:
            return False
        idle = self.input_idle() if self.input_idle is not None else self.clock() - self.last_activity
        return idle >= self.idle_seconds


def _windows_input_idle_seconds() -> Callable[[], float] | None:
    """Seconds since the last keyboard or mouse input anywhere (GetLastInputInfo), or None off Windows."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class _LastInputInfo(ctypes.Structure):
        _fields_ = (("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD))

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    kernel32.GetTickCount.restype = wintypes.DWORD

    def idle_seconds() -> float:
        info = _LastInputInfo()
        info.cbSize = ctypes.sizeof(_LastInputInfo)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0  # unknown: treat as busy
        return ((kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000.0

    return idle_seconds


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
