"""Qt-free planning half of semantic indexing.

Shared by the in-app per-folder indexer (``semantic_index``) and the library
indexing worker process (``aiculler/library_index_worker.py``), which runs in
the AI runtime's Python and must not import PySide6.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from aiculler.features import IMAGE_EXTENSIONS, _file_signature
from .ai_model import DEFAULT_AICULLER_CLIP_REVISION


# Bump when the cache identity or embedding contract changes so previously
# indexed folders are transparently re-embedded.
SEMANTIC_INDEX_SCHEMA_VERSION = 1
# TinyCLIP (onnx-community/TinyCLIP-ViT-8M-16-Text-3M-YFCC15M) image_embeds dim.
SEMANTIC_EMBEDDING_DIM = 512
SEMANTIC_INDEX_COMMIT_BATCH = 32

def _source_signature(path: Path) -> dict[str, object]:
    """Identity of a source file, matching the full workflow's cache format."""
    return _file_signature(path)


def _signature_key(signature: dict[str, object]) -> str:
    return json.dumps(signature, sort_keys=True)


def compute_semantic_model_identity(
    clip_vision_model: str | Path,
    *,
    revision: str | None = None,
    embedding_dim: int = SEMANTIC_EMBEDDING_DIM,
    fallback_model: str | Path | None = None,
) -> str:
    """A stable identity string for the active TinyCLIP model.

    Combines the pinned model revision, the on-disk model file signature
    (path/size/mtime), and the expected embedding dimension so that a
    re-download, a swapped model, or a dimensionality change all invalidate the
    stored embeddings.
    """
    payload: dict[str, object] = {
        "schema_version": SEMANTIC_INDEX_SCHEMA_VERSION,
        "revision": revision or DEFAULT_AICULLER_CLIP_REVISION,
        "embedding_dim": int(embedding_dim),
        "model": _source_signature(Path(clip_vision_model)),
    }
    if fallback_model:
        payload["fallback"] = _source_signature(Path(fallback_model))
    return json.dumps(payload, sort_keys=True)


@dataclass(frozen=True, slots=True)
class SemanticIndexItem:
    source_path: str
    image_id: int | None
    signature_key: str


@dataclass(frozen=True, slots=True)
class SemanticIndexPlan:
    to_embed: tuple[SemanticIndexItem, ...] = ()
    to_adopt: tuple[SemanticIndexItem, ...] = ()
    valid: tuple[SemanticIndexItem, ...] = ()

    @property
    def ready_after(self) -> int:
        return len(self.to_embed) + len(self.to_adopt) + len(self.valid)


def collect_indexable_sources(records: Iterable[object]) -> list[str]:
    """Resolve records/paths to supported, non-hidden image source paths."""
    resolved: list[str] = []
    seen: set[str] = set()
    for record in records:
        raw = getattr(record, "path", record)
        if raw is None:
            continue
        path = Path(str(raw))
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        if _is_hidden_artifact(path):
            continue
        key = str(path).casefold()
        if key in seen:
            continue
        seen.add(key)
        resolved.append(str(path))
    return resolved


def _is_hidden_artifact(path: Path) -> bool:
    # Skip dotted/hidden app directories (e.g. .image_triage_ai/aiculler_cache)
    # and Python caches so cached preview JPGs are never treated as sources.
    for part in path.parts:
        if part in ("", "\\", "/"):
            continue
        name = part.rstrip(":\\/")
        if name.startswith(".") or name == "__pycache__":
            return True
    return False


def plan_semantic_index(
    snapshot: dict[str, dict[str, object]],
    source_paths: Sequence[str],
    *,
    model_identity: str,
    expected_dim: int = SEMANTIC_EMBEDDING_DIM,
) -> SemanticIndexPlan:
    """Decide which sources are already valid, reusable, or need embedding.

    ``snapshot`` is :meth:`SQLiteFeatureStore.semantic_index_snapshot`. The plan
    reuses a full-workflow embedding (``to_adopt``) only when the recorded source
    signature still matches the current file, so changed files are re-embedded.
    """
    to_embed: list[SemanticIndexItem] = []
    to_adopt: list[SemanticIndexItem] = []
    valid: list[SemanticIndexItem] = []
    for source in source_paths:
        signature = _source_signature(Path(source))
        signature_key = _signature_key(signature)
        entry = snapshot.get(source)
        image_id = None if entry is None else int(entry["image_id"])
        item = SemanticIndexItem(source_path=source, image_id=image_id, signature_key=signature_key)

        if entry is None or entry.get("embedding_dim") is None:
            to_embed.append(item)
            continue

        embedding_dim = entry.get("embedding_dim")
        if embedding_dim != expected_dim:
            # Includes legacy 768-dim ViT-L/14 embeddings, which must be replaced.
            to_embed.append(item)
            continue

        state_matches = (
            entry.get("state_model_identity") == model_identity
            and entry.get("state_source_signature") == signature_key
            and entry.get("state_embedding_dim") == expected_dim
            and entry.get("state_schema_version") == SEMANTIC_INDEX_SCHEMA_VERSION
        )
        if state_matches:
            valid.append(item)
            continue

        if _full_workflow_signature_matches(entry.get("metadata_json"), signature):
            to_adopt.append(item)
            continue

        to_embed.append(item)
    return SemanticIndexPlan(
        to_embed=tuple(to_embed),
        to_adopt=tuple(to_adopt),
        valid=tuple(valid),
    )


def _full_workflow_signature_matches(metadata_json: object, signature: dict[str, object]) -> bool:
    if not metadata_json:
        return False
    try:
        metadata = json.loads(str(metadata_json))
    except (TypeError, ValueError):
        return False
    if not isinstance(metadata, dict):
        return False
    cache = metadata.get("aiculler_feature_cache")
    if not isinstance(cache, dict):
        return False
    return cache.get("source_signature") == signature
