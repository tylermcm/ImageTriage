"""Automatic, semantic-only folder indexing for natural-language search.

This is intentionally decoupled from the full AI *Index & Score* workflow. It
generates (and incrementally maintains) TinyCLIP image embeddings so semantic
search can become available in the background when a folder is opened, without
running technical scoring, TOPIQ, face detection, clustering, or ranking.

The heavy lifting is a pure planner (:func:`plan_semantic_index`) plus a thin
:class:`SemanticFolderIndexTask` ``QRunnable`` wrapper, so the planning and
incremental-embedding logic can be unit tested without Qt or a real model.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Callable, Sequence

import numpy as np

from PySide6.QtCore import QObject, QRunnable, Signal

from aiculler.features import SemanticEmbeddingExtractor
from aiculler.storage import SQLiteFeatureStore
from .ai_runtime_packages import resolve_ai_runtime_site_packages
from .perf import write_execution_log
# The planner lives in a Qt-free module so the library indexing worker can share it; re-exported here.
from .semantic_plan import (  # noqa: F401
    SEMANTIC_EMBEDDING_DIM,
    SEMANTIC_INDEX_COMMIT_BATCH,
    SEMANTIC_INDEX_SCHEMA_VERSION,
    SemanticIndexItem,
    SemanticIndexPlan,
    collect_indexable_sources,
    compute_semantic_model_identity,
    plan_semantic_index,
)

_RUNTIME_DLL_HANDLES: list[object] = []
_RUNTIME_DLL_PATHS: set[str] = set()


def ensure_semantic_onnx_runtime(*, device: str = "auto") -> None:
    """Expose the selected managed runtime before importing ONNX Runtime.

    The frozen GUI bundles a CPU ONNX Runtime for baseline features, while the
    managed GPU profile contains CUDA ONNX Runtime plus optional packages such
    as InsightFace. Importing the bundled copy first permanently pins the
    process to CPU and also leaves InsightFace undiscoverable. Prefer the
    managed profile on frozen builds before the first import; source builds keep
    their active environment authoritative and use the managed profile only as
    a fallback.
    """
    frozen = bool(getattr(sys, "frozen", False))
    already_loaded = "onnxruntime" in sys.modules
    site_directories = tuple(resolve_ai_runtime_site_packages(device=device))
    for site_packages in site_directories:
        path_text = str(site_packages)
        if frozen and not already_loaded:
            if path_text in sys.path:
                sys.path.remove(path_text)
            sys.path.insert(0, path_text)
        elif path_text not in sys.path:
            sys.path.append(path_text)
        if os.name == "nt":
            add_dll_directory = getattr(os, "add_dll_directory", None)
            if add_dll_directory is not None:
                for directory in (site_packages / "torch" / "lib", *site_packages.glob("*.libs")):
                    if not directory.is_dir():
                        continue
                    directory_text = str(directory)
                    if directory_text in _RUNTIME_DLL_PATHS:
                        continue
                    try:
                        _RUNTIME_DLL_HANDLES.append(add_dll_directory(directory_text))
                        _RUNTIME_DLL_PATHS.add(directory_text)
                    except OSError:
                        pass
    try:
        import onnxruntime
    except ImportError as exc:
        raise RuntimeError(
            "ONNX Runtime is unavailable. Run AI > Runtime And Cache > "
            "Install AI Runtime to enable semantic search."
        ) from exc
    providers = tuple(onnxruntime.get_available_providers())
    write_execution_log(
        f"onnx-runtime: version={onnxruntime.__version__}, providers={providers}, "
        f"requested_device={device}, loaded_before_activation={already_loaded}"
    )


class SemanticIndexSignals(QObject):
    progress = Signal(str, int, int, int)  # folder, token, completed, total
    batch_ready = Signal(str, int)  # folder, token — new embeddings queryable
    finished = Signal(str, int, int, int)  # folder, token, indexed, ready_total
    failed = Signal(str, int, str)  # folder, token, message


class SemanticFolderIndexTask(QRunnable):
    """Background, semantic-only embedding indexer for one folder."""

    def __init__(
        self,
        *,
        folder: str,
        token: int,
        records: Sequence[object],
        db_path: str | Path,
        clip_vision_model: str | Path | None = None,
        clip_fallback_model: str | Path | None = None,
        model_identity: str | None = None,
        device: str = "auto",
        expected_dim: int = SEMANTIC_EMBEDDING_DIM,
        intra_op_num_threads: int | None = 4,
        commit_batch: int = SEMANTIC_INDEX_COMMIT_BATCH,
        extractor: object | None = None,
        extractor_factory: Callable[[], object] | None = None,
    ) -> None:
        super().__init__()
        self.folder = str(folder)
        self.token = int(token)
        self.records = tuple(records)
        self.db_path = Path(db_path)
        self.clip_vision_model = Path(clip_vision_model) if clip_vision_model else None
        self.clip_fallback_model = Path(clip_fallback_model) if clip_fallback_model else None
        self.device = str(device or "auto")
        self.expected_dim = int(expected_dim)
        self.intra_op_num_threads = intra_op_num_threads
        self.commit_batch = max(1, int(commit_batch))
        self._extractor = extractor
        self._extractor_factory = extractor_factory
        self.model_identity = model_identity or (
            compute_semantic_model_identity(
                self.clip_vision_model,
                embedding_dim=self.expected_dim,
                fallback_model=self.clip_fallback_model,
            )
            if self.clip_vision_model is not None
            else ""
        )
        self.signals = SemanticIndexSignals()
        self.setAutoDelete(True)
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def _build_extractor(self):
        if self._extractor is not None:
            return self._extractor
        if self._extractor_factory is not None:
            return self._extractor_factory()
        if self.clip_vision_model is None:
            raise RuntimeError("No TinyCLIP model configured for semantic indexing")
        ensure_semantic_onnx_runtime(device=self.device)
        # Only an explicit "cpu" device pins CPU providers; "auto"/"cuda" pass
        # None so the extractor keeps its GPU-preferred default. (Without an
        # explicit list it defaults to preferred_onnx_providers() = CUDA-if-
        # available, so a bare device string alone would not force CPU.)
        providers = ["CPUExecutionProvider"] if self.device.startswith("cpu") else None
        return SemanticEmbeddingExtractor(
            self.clip_vision_model,
            clip_fallback_onnx_path=self.clip_fallback_model,
            providers=providers,
            intra_op_num_threads=self.intra_op_num_threads,
        )

    def run(self) -> None:
        if self._cancelled:
            return
        try:
            sources = collect_indexable_sources(self.records)
            store = SQLiteFeatureStore(self.db_path)
            try:
                snapshot = store.semantic_index_snapshot()
                plan = plan_semantic_index(
                    snapshot,
                    sources,
                    model_identity=self.model_identity,
                    expected_dim=self.expected_dim,
                )

                adopted = 0
                for item in plan.to_adopt:
                    if self._cancelled:
                        return
                    image_id = item.image_id if item.image_id is not None else store.ensure_image(item.source_path)
                    store.set_semantic_index_state(
                        image_id,
                        model_identity=self.model_identity,
                        source_signature=item.signature_key,
                        embedding_dim=self.expected_dim,
                        schema_version=SEMANTIC_INDEX_SCHEMA_VERSION,
                        commit=False,
                    )
                    adopted += 1
                if adopted:
                    store.connection.commit()
                    self.signals.batch_ready.emit(self.folder, self.token)

                total = len(plan.to_embed)
                if not total:
                    self.signals.finished.emit(self.folder, self.token, adopted, plan.ready_after)
                    return

                extractor = self._build_extractor()
                completed = 0
                pending_commit = 0
                for item in plan.to_embed:
                    if self._cancelled:
                        store.connection.commit()
                        return
                    image_id = item.image_id if item.image_id is not None else store.ensure_image(item.source_path)
                    try:
                        vector = np.asarray(extractor.encode_image(item.source_path), dtype=np.float32).reshape(-1)
                    except Exception:
                        # A single unreadable/corrupt image must not abort the folder.
                        continue
                    store.save_semantic_embedding(image_id, vector, commit=False)
                    store.set_semantic_index_state(
                        image_id,
                        model_identity=self.model_identity,
                        source_signature=item.signature_key,
                        embedding_dim=int(vector.size),
                        schema_version=SEMANTIC_INDEX_SCHEMA_VERSION,
                        commit=False,
                    )
                    completed += 1
                    pending_commit += 1
                    if pending_commit >= self.commit_batch:
                        store.connection.commit()
                        pending_commit = 0
                        self.signals.progress.emit(self.folder, self.token, completed, total)
                        self.signals.batch_ready.emit(self.folder, self.token)
                if pending_commit:
                    store.connection.commit()
                self.signals.progress.emit(self.folder, self.token, completed, total)
                if completed:
                    self.signals.batch_ready.emit(self.folder, self.token)
                self.signals.finished.emit(self.folder, self.token, adopted + completed, plan.ready_after)
            finally:
                store.close()
        except Exception as exc:  # pragma: no cover - defensive; surfaced to UI
            if self._cancelled:
                return
            self.signals.failed.emit(self.folder, self.token, str(exc))
