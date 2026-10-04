"""Background tasks that read and enrich the records of the open folder (annotations, scope enrichment, Inspector stats)."""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, QRunnable, Signal
from dataclasses import dataclass
from pathlib import Path
from queue import SimpleQueue

from ..ai_results import AIBundle
from ..catalog import CatalogRepository
from ..decision_store import DecisionStore
from ..models import ImageRecord, SessionAnnotation
from ..perf import perf_logger
from ..review_intelligence import ReviewIntelligenceBundle
from ..review_tools import build_inspection_stats
from ..review_workflows import build_review_scoring_cache_key, build_burst_recommendations, review_scoring_provider_id
from ..scanner import normalized_path_key
from ..xmp import load_sidecar_annotation


@dataclass(slots=True, frozen=True)
class InspectorStatsRequest:
    """Background request for lightweight Inspector quality statistics."""
    cache_key: tuple[str, int, int, int, int]
    image: object


class InspectorStatsTask(QRunnable):
    def __init__(self, request: InspectorStatsRequest, result_queue: SimpleQueue) -> None:
        super().__init__()
        self.request = request
        self.result_queue = result_queue
        self.setAutoDelete(True)

    def run(self) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        try:
            stats = build_inspection_stats(self.request.image)
        except Exception as exc:  # pragma: no cover - defensive worker boundary
            if logger.enabled:
                logger.duration(
                    "inspector.stats.failed",
                    (time.perf_counter() - start) * 1000.0,
                    error=str(exc),
                )
            self.result_queue.put(("failed", self.request.cache_key, str(exc)))
            return
        if logger.enabled:
            logger.duration(
                "inspector.stats",
                (time.perf_counter() - start) * 1000.0,
                width=self.request.image.width() if hasattr(self.request.image, "width") else 0,
                height=self.request.image.height() if hasattr(self.request.image, "height") else 0,
            )
        self.result_queue.put(("ready", self.request.cache_key, stats))


class AnnotationHydrationSignals(QObject):
    """Signals emitted while annotation state is loaded in batches for a scope."""
    chunk = Signal(str, int, object)
    finished = Signal(str, int)
    failed = Signal(str, int, str)


class AnnotationHydrationTask(QRunnable):
    """Loads persisted and sidecar annotations without blocking the UI thread."""
    PRIORITY_BATCH_SIZE = 96
    BACKGROUND_BATCH_SIZE = 240

    def __init__(
        self,
        *,
        scope_key: str,
        token: int,
        session_id: str,
        records: tuple[ImageRecord, ...],
        prioritized_paths: tuple[str, ...] = (),
    ) -> None:
        super().__init__()
        self.scope_key = scope_key
        self.token = token
        self.session_id = session_id
        self.records = records
        self.prioritized_paths = prioritized_paths
        self.signals = AnnotationHydrationSignals()
        self._cancelled = False
        self.setAutoDelete(True)

    def cancel(self) -> None:
        self._cancelled = True

    @staticmethod
    def _record_batches(records: list[ImageRecord], batch_size: int) -> list[list[ImageRecord]]:
        return [records[index : index + batch_size] for index in range(0, len(records), batch_size)]

    def _partition_records(self) -> tuple[list[ImageRecord], list[ImageRecord]]:
        if not self.records:
            return [], []
        record_by_key = {normalized_path_key(record.path): record for record in self.records}
        prioritized_records: list[ImageRecord] = []
        seen_keys: set[str] = set()
        for path in self.prioritized_paths:
            if not path:
                continue
            key = normalized_path_key(path)
            if key in seen_keys:
                continue
            record = record_by_key.get(key)
            if record is None:
                continue
            prioritized_records.append(record)
            seen_keys.add(key)
        remaining_records = [record for record in self.records if normalized_path_key(record.path) not in seen_keys]
        return prioritized_records, remaining_records

    def _hydrate_records_batch(
        self,
        store: DecisionStore,
        records: list[ImageRecord],
    ) -> dict[str, SessionAnnotation]:
        if not records or self._cancelled:
            return {}
        records_by_path = {record.path: record for record in records}
        persisted = store.load_annotations_for_paths(
            self.session_id,
            records_by_path,
            list(records_by_path),
        )
        hydrated: dict[str, SessionAnnotation] = {}
        for record in records:
            if self._cancelled:
                return {}
            sidecar = load_sidecar_annotation(record.path)
            if not sidecar.is_empty:
                hydrated[record.path] = sidecar
            persisted_annotation = persisted.get(record.path)
            if persisted_annotation is not None and not persisted_annotation.is_empty:
                hydrated[record.path] = persisted_annotation
        return hydrated

    def run(self) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        hydrated_count = 0
        try:
            if self._cancelled:
                return
            store = DecisionStore()
            prioritized_records, remaining_records = self._partition_records()

            for batch in self._record_batches(prioritized_records, self.PRIORITY_BATCH_SIZE):
                if self._cancelled:
                    return
                chunk = self._hydrate_records_batch(store, batch)
                if chunk:
                    hydrated_count += len(chunk)
                    self.signals.chunk.emit(self.scope_key, self.token, dict(chunk))

            for batch in self._record_batches(remaining_records, self.BACKGROUND_BATCH_SIZE):
                if self._cancelled:
                    return
                chunk = self._hydrate_records_batch(store, batch)
                if chunk:
                    hydrated_count += len(chunk)
                    self.signals.chunk.emit(self.scope_key, self.token, dict(chunk))

            if self._cancelled:
                return
            if logger.enabled:
                logger.duration(
                    "annotation.hydration",
                    (time.perf_counter() - start) * 1000.0,
                    scope=self.scope_key,
                    token=self.token,
                    records=len(self.records),
                    hydrated=hydrated_count,
                    prioritized=len(self.prioritized_paths),
                )
            self.signals.finished.emit(self.scope_key, self.token)
        except Exception as exc:  # pragma: no cover - desktop/runtime path
            if logger.enabled:
                logger.duration(
                    "annotation.hydration.failed",
                    (time.perf_counter() - start) * 1000.0,
                    scope=self.scope_key,
                    token=self.token,
                    records=len(self.records),
                    hydrated=hydrated_count,
                    error=str(exc),
                )
            self.signals.failed.emit(self.scope_key, self.token, str(exc))


class ScopeEnrichmentSignals(QObject):
    """Signals for workflow-scoring and taste-profile enrichment work."""
    cache_status = Signal(str, int, object)
    finished = Signal(str, int, object, object, object)
    failed = Signal(str, int, str)


class ScopeEnrichmentTask(QRunnable):
    """Builds workflow recommendations for the current scope, with catalog reuse."""
    def __init__(
        self,
        *,
        scope_key: str,
        token: int,
        session_id: str,
        folder_path: str,
        catalog_db_path: str | Path | None,
        include_all_scope_events: bool,
        records: tuple[ImageRecord, ...],
        ai_bundle: AIBundle | None,
        review_bundle: ReviewIntelligenceBundle | None,
    ) -> None:
        super().__init__()
        self.scope_key = scope_key
        self.token = token
        self.session_id = session_id
        self.folder_path = folder_path
        self.catalog_db_path = Path(catalog_db_path) if catalog_db_path else None
        self.include_all_scope_events = include_all_scope_events
        self.records = records
        self.ai_bundle = ai_bundle
        self.review_bundle = review_bundle
        self.signals = ScopeEnrichmentSignals()
        self._cancelled = False
        self.setAutoDelete(True)

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        try:
            if self._cancelled:
                return
            store = DecisionStore()
            if self.folder_path:
                correction_events = store.load_correction_events(self.session_id, self.folder_path)
            elif self.include_all_scope_events and self.records:
                correction_events = store.load_correction_events(self.session_id)
            else:
                correction_events = []
            if self._cancelled:
                return
            catalog_repository: CatalogRepository | None = None
            cache_key = ""
            if self.folder_path:
                cache_key = build_review_scoring_cache_key(
                    self.records,
                    ai_bundle=self.ai_bundle,
                    review_bundle=self.review_bundle,
                    correction_events=correction_events,
                )
                catalog_repository = CatalogRepository(self.catalog_db_path)
                cached_entry = catalog_repository.load_review_scoring(
                    self.folder_path,
                    session_id=self.session_id,
                    cache_key=cache_key,
                )
                if cached_entry is not None:
                    self.signals.cache_status.emit(
                        self.scope_key,
                        self.token,
                        {
                            "source": "catalog",
                            "record_count": len(self.records),
                        },
                    )
                    self.signals.finished.emit(
                        self.scope_key,
                        self.token,
                        correction_events,
                        cached_entry.taste_profile,
                        cached_entry.recommendations,
                    )
                    if logger.enabled:
                        logger.duration(
                            "workflow.enrichment",
                            (time.perf_counter() - start) * 1000.0,
                            scope=self.scope_key,
                            token=self.token,
                            records=len(self.records),
                            source="catalog",
                            corrections=len(correction_events),
                            recommendations=len(cached_entry.recommendations),
                        )
                    return
            taste_profile, recommendations = build_burst_recommendations(
                list(self.records),
                ai_bundle=self.ai_bundle,
                review_bundle=self.review_bundle,
                correction_events=correction_events,
                should_cancel=lambda: self._cancelled,
                )
            if self._cancelled:
                return
            if self.folder_path:
                if catalog_repository is None:
                    catalog_repository = CatalogRepository(self.catalog_db_path)
                if not cache_key:
                    cache_key = build_review_scoring_cache_key(
                        self.records,
                        ai_bundle=self.ai_bundle,
                        review_bundle=self.review_bundle,
                        correction_events=correction_events,
                    )
                catalog_repository.save_review_scoring(
                    self.folder_path,
                    session_id=self.session_id,
                    cache_key=cache_key,
                    provider_id=review_scoring_provider_id(
                        self.records,
                        ai_bundle=self.ai_bundle,
                        review_bundle=self.review_bundle,
                        correction_events=correction_events,
                    ),
                    records=self.records,
                    taste_profile=taste_profile,
                    recommendations=recommendations,
                )
            self.signals.cache_status.emit(
                self.scope_key,
                self.token,
                {
                    "source": "live",
                    "record_count": len(self.records),
                },
            )
            self.signals.finished.emit(
                self.scope_key,
                self.token,
                correction_events,
                taste_profile,
                recommendations,
            )
            if logger.enabled:
                logger.duration(
                    "workflow.enrichment",
                    (time.perf_counter() - start) * 1000.0,
                    scope=self.scope_key,
                    token=self.token,
                    records=len(self.records),
                    source="live",
                    corrections=len(correction_events),
                    recommendations=len(recommendations),
                )
        except Exception as exc:  # pragma: no cover - desktop/runtime path
            if logger.enabled:
                logger.duration(
                    "workflow.enrichment.failed",
                    (time.perf_counter() - start) * 1000.0,
                    scope=self.scope_key,
                    token=self.token,
                    records=len(self.records),
                    error=str(exc),
                )
            self.signals.failed.emit(self.scope_key, self.token, str(exc))
