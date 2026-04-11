from __future__ import annotations

from dataclasses import dataclass
from queue import Empty, SimpleQueue

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

from .decision_store import DecisionStore
from .models import ImageRecord, SessionAnnotation
from .xmp import sync_sidecar_annotation


@dataclass(slots=True, frozen=True)
class AnnotationQueueEntry:
    record_path: str
    record: ImageRecord
    session_id: str
    annotation: SessionAnnotation | None
    previous_annotation: SessionAnnotation | None = None


class _AnnotationPersistTask(QRunnable):
    def __init__(self, entries: tuple[AnnotationQueueEntry, ...], result_queue: SimpleQueue) -> None:
        super().__init__()
        self.entries = entries
        self.result_queue = result_queue
        self.setAutoDelete(True)

    def run(self) -> None:
        store = DecisionStore()
        by_session: dict[str, list[AnnotationQueueEntry]] = {}
        for entry in self.entries:
            by_session.setdefault(entry.session_id, []).append(entry)

        persisted_paths: set[str] = set()
        for session_id, session_entries in by_session.items():
            try:
                store.save_annotations(
                    session_id,
                    [(entry.record, entry.annotation) for entry in session_entries],
                )
            except Exception:
                for entry in session_entries:
                    try:
                        if entry.annotation is None or entry.annotation.is_empty:
                            store.delete_annotation(entry.session_id, entry.record.path)
                        else:
                            store.save_annotation(entry.session_id, entry.record, entry.annotation)
                    except Exception as exc:  # pragma: no cover - worker/runtime path
                        self.result_queue.put(("failed", entry.record_path, str(exc)))
                        continue
                    persisted_paths.add(entry.record_path)
                continue

            for entry in session_entries:
                persisted_paths.add(entry.record_path)

        for entry in self.entries:
            if entry.record_path not in persisted_paths:
                continue
            try:
                sync_sidecar_annotation(entry.record, entry.annotation)
            except Exception as exc:  # pragma: no cover - worker/runtime path
                self.result_queue.put(("warning", entry.record_path, str(exc)))
            self.result_queue.put(("ok", entry.record_path))
        self.result_queue.put(("done", len(self.entries)))


class AnnotationPersistenceQueue(QObject):
    failed = Signal(str, str)
    warning = Signal(str, str)
    flushed = Signal(int)

    def __init__(
        self,
        *,
        flush_interval_ms: int = 140,
        max_workers: int = 1,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._pending: dict[str, AnnotationQueueEntry] = {}
        self._rollback_by_path: dict[str, SessionAnnotation | None] = {}
        self._result_queue: SimpleQueue = SimpleQueue()
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(max(1, int(max_workers)))
        self._active = False
        self._flush_requested = False
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(max(20, int(flush_interval_ms)))
        self._flush_timer.timeout.connect(self.flush)
        self._drain_timer = QTimer(self)
        self._drain_timer.setInterval(12)
        self._drain_timer.timeout.connect(self._drain_results)

    def enqueue(
        self,
        record_path: str,
        annotation_delta: SessionAnnotation | None,
        *,
        record: ImageRecord,
        session_id: str,
        previous_annotation: SessionAnnotation | None = None,
    ) -> None:
        normalized_path = record_path or record.path
        current = _clone_annotation(annotation_delta)
        previous = _clone_annotation(previous_annotation)
        self._pending[normalized_path] = AnnotationQueueEntry(
            record_path=normalized_path,
            record=record,
            session_id=session_id,
            annotation=current,
            previous_annotation=previous,
        )
        if previous_annotation is not None:
            self._rollback_by_path[normalized_path] = previous
        self._flush_timer.start()

    def flush(self) -> None:
        if self._active:
            self._flush_requested = True
            return
        if not self._pending:
            return
        entries = tuple(self._pending.values())
        self._pending.clear()
        self._active = True
        self._flush_requested = False
        self._pool.start(_AnnotationPersistTask(entries, self._result_queue))
        if not self._drain_timer.isActive():
            self._drain_timer.start()

    def rollback(self, record_path: str) -> SessionAnnotation | None:
        return _clone_annotation(self._rollback_by_path.pop(record_path, None))

    def flush_blocking(self, timeout_ms: int = 4_000) -> None:
        self.flush()
        self._pool.waitForDone(max(1, int(timeout_ms)))
        self._drain_results()

    def _drain_results(self) -> None:
        processed = 0
        while processed < 128:
            try:
                state, *payload = self._result_queue.get_nowait()
            except Empty:
                break
            processed += 1
            if state == "ok":
                path = str(payload[0]) if payload else ""
                if path:
                    self._rollback_by_path.pop(path, None)
                continue
            if state == "failed":
                path = str(payload[0]) if payload else ""
                message = str(payload[1]) if len(payload) > 1 else "Could not persist annotation."
                self.failed.emit(path, message)
                continue
            if state == "warning":
                path = str(payload[0]) if payload else ""
                message = str(payload[1]) if len(payload) > 1 else "Could not sync XMP sidecar."
                self.warning.emit(path, message)
                continue
            if state == "done":
                flushed_count = int(payload[0]) if payload else 0
                self._active = False
                self.flushed.emit(flushed_count)
                if self._flush_requested or self._pending:
                    self.flush()

        if processed == 0 and not self._active:
            self._drain_timer.stop()


def _clone_annotation(annotation: SessionAnnotation | None) -> SessionAnnotation | None:
    if annotation is None:
        return None
    return SessionAnnotation(
        winner=annotation.winner,
        reject=annotation.reject,
        photoshop=annotation.photoshop,
        rating=annotation.rating,
        tags=annotation.tags,
        review_round=annotation.review_round,
    )
