"""Shared background semantic and people search, independent of desktop windows."""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from .path_keys import path_key
from .perf import perf_logger
from .semantic_index import ensure_semantic_onnx_runtime
from .semantic_search import SearchFilters


def _search_match_path_key(path: str | Path) -> str:
    text = str(path or "").strip()
    # The grid looks a hit up with the same shared key (photos._path_key): folding case regardless of the file system
    # dropped every hit on a case-sensitive one.
    return path_key(os.path.abspath(text)) if text else ""


class UnifiedSearchSignals(QObject):
    finished = Signal(str, int, object)
    failed = Signal(str, int, str)


class UnifiedSearchTask(QRunnable):
    """Runs CLIP/person search off the UI thread for the current search box."""

    _text_encoder_cache: dict[tuple[str, str, str], object] = {}
    _text_encoder_cache_lock = threading.Lock()

    def __init__(
        self,
        *,
        folder: str,
        token: int,
        db_path: Path,
        runtime: object,
        query_text: str,
        min_confidence: float,
        limit: int = 500,
    ) -> None:
        super().__init__()
        self.folder = folder
        self.token = token
        self.db_path = Path(db_path)
        self.runtime = runtime
        self.query_text = query_text
        self.min_confidence = float(min_confidence)
        self.limit = int(limit)
        self.signals = UnifiedSearchSignals()
        self._cancelled = False
        self.setAutoDelete(True)

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        if self._cancelled:
            return
        try:
            query_text = self.query_text.strip()
            if not query_text or not self.db_path.exists():
                self.signals.finished.emit(self.folder, self.token, self._empty_result(query_text))
                return

            from aiculler.storage import SQLiteFeatureStore
            from .people_search import list_person_clusters
            from .semantic_search import (
                SEARCH_QUERY_TEMPLATES,
                FeatureStoreSemanticSearch,
                parse_search_query,
            )

            store = SQLiteFeatureStore(self.db_path)
            try:
                known_people = tuple(
                    cluster.name
                    for cluster in list_person_clusters(store.connection)
                    if cluster.name.strip()
                )
                if self._cancelled:
                    return
                parsed = parse_search_query(query_text, known_people=known_people)
                indexed_count = len(store.list_images(require_embedding=True))
                if parsed.semantic_text and not indexed_count:
                    self.signals.finished.emit(self.folder, self.token, self._empty_result(query_text))
                    return
                text_encoder = None
                if parsed.semantic_text:
                    text_model = Path(getattr(self.runtime, "clip_text_model", ""))
                    tokenizer = Path(getattr(self.runtime, "tokenizer", ""))
                    fallback_text_model = getattr(self.runtime, "clip_fallback_text_model", None)
                    for label, path in (("CLIP text model", text_model), ("CLIP tokenizer", tokenizer)):
                        if not path.exists():
                            raise FileNotFoundError(f"{label} is missing: {path}")
                    text_encoder = self._cached_text_encoder(
                        text_model,
                        tokenizer,
                        fallback_text_model,
                        device=str(getattr(self.runtime, "device", "auto")),
                    )
                service = FeatureStoreSemanticSearch(
                    store,
                    text_encoder,
                    query_templates=SEARCH_QUERY_TEMPLATES,
                )
                hits = service.search(
                    query_text,
                    known_people=known_people,
                    filters=SearchFilters(min_confidence=self.min_confidence),
                    limit=self.limit,
                )
            finally:
                store.close()

            if self._cancelled:
                return
            path_keys: list[str] = []
            rank_by_path: dict[str, float] = {}
            for hit in hits:
                key = _search_match_path_key(hit.source_path)
                if not key:
                    continue
                path_keys.append(key)
                rank_by_path[key] = max(rank_by_path.get(key, 0.0), float(hit.confidence))
            result = {
                "query": query_text,
                "path_keys": tuple(path_keys),
                "rank_by_path": rank_by_path,
                "hit_count": len(path_keys),
                "known_people": known_people,
                "indexed_count": indexed_count,
            }
            if logger.enabled:
                logger.duration(
                    "unified_search",
                    (time.perf_counter() - start) * 1000.0,
                    folder=self.folder,
                    hits=len(path_keys),
                    people=len(known_people),
                )
            self.signals.finished.emit(self.folder, self.token, result)
        except Exception as exc:
            if logger.enabled:
                logger.duration(
                    "unified_search.failed",
                    (time.perf_counter() - start) * 1000.0,
                    folder=self.folder,
                    error=str(exc),
                )
            self.signals.failed.emit(self.folder, self.token, str(exc))

    @classmethod
    def _cached_text_encoder(
        cls,
        text_model: Path,
        tokenizer: Path,
        fallback_text_model: object,
        *,
        device: str = "auto",
    ):
        cls._ensure_text_search_runtime(device=device)
        from aiculler.text_scoring import CLIPTextEncoder

        fallback_path = Path(fallback_text_model) if fallback_text_model else None
        key = (
            str(text_model.resolve()),
            str(tokenizer.resolve()),
            str(fallback_path.resolve()) if fallback_path is not None else "",
        )
        with cls._text_encoder_cache_lock:
            cached = cls._text_encoder_cache.get(key)
            if cached is not None:
                return cached
            encoder = CLIPTextEncoder(
                text_model,
                tokenizer,
                fallback_text_onnx_path=fallback_path,
            )
            cls._text_encoder_cache.clear()
            cls._text_encoder_cache[key] = encoder
            return encoder

    @staticmethod
    def _ensure_text_search_runtime(*, device: str) -> None:
        ensure_semantic_onnx_runtime(device=device)

    @staticmethod
    def _empty_result(query_text: str) -> dict[str, object]:
        return {
            "query": query_text,
            "path_keys": (),
            "rank_by_path": {},
            "hit_count": 0,
            "known_people": (),
            "indexed_count": 0,
        }
