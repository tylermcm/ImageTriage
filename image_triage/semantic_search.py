from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Protocol, Sequence

import numpy as np

from .metadata import CaptureMetadata
from .people_search import image_ids_matching_people


class TextEncoder(Protocol):
    def encode(self, prompt: str) -> np.ndarray:
        ...


@dataclass(frozen=True, slots=True)
class SearchFilters:
    min_confidence: float = 0.0
    captured_after: date | None = None
    captured_before: date | None = None
    camera_text: str = ""
    lens_text: str = ""
    min_rating: int = 0
    folder_text: str = ""


@dataclass(frozen=True, slots=True)
class ParsedSearchQuery:
    semantic_text: str
    people: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SemanticSearchHit:
    image_id: int
    source_path: str
    confidence: float
    people: tuple[str, ...] = ()
    matched_by: tuple[str, ...] = ()


class SemanticVectorIndex:
    def __init__(self, image_ids: Sequence[int], paths: Sequence[str], embeddings: Sequence[Sequence[float] | np.ndarray]):
        if len(image_ids) != len(paths) or len(image_ids) != len(embeddings):
            raise ValueError("image_ids, paths, and embeddings must have the same length")
        self.image_ids = [int(image_id) for image_id in image_ids]
        self.paths = [str(path) for path in paths]
        if not embeddings:
            self.matrix = np.empty((0, 0), dtype=np.float32)
            return
        self.matrix = _normalize_matrix(np.vstack([np.asarray(item, dtype=np.float32).reshape(-1) for item in embeddings]))

    def search(
        self,
        query_embedding: Sequence[float] | np.ndarray,
        *,
        limit: int | None = None,
        min_confidence: float = 0.0,
        allowed_image_ids: set[int] | None = None,
    ) -> list[SemanticSearchHit]:
        if self.matrix.size == 0:
            return []
        query = _normalize_vector(np.asarray(query_embedding, dtype=np.float32).reshape(-1))
        if query.size != self.matrix.shape[1]:
            raise ValueError(f"Embedding dimensions differ: {query.size} vs {self.matrix.shape[1]}")
        scores = self.matrix @ query
        order = np.argsort(-scores)
        hits: list[SemanticSearchHit] = []
        for index in order:
            image_id = self.image_ids[int(index)]
            score = float(scores[int(index)])
            if allowed_image_ids is not None and image_id not in allowed_image_ids:
                continue
            if score < min_confidence:
                continue
            hits.append(SemanticSearchHit(image_id=image_id, source_path=self.paths[int(index)], confidence=score))
            if limit is not None and len(hits) >= limit:
                break
        return hits


class FeatureStoreSemanticSearch:
    AUTO_CONFIDENCE_BAND = 0.04

    def __init__(self, store, text_encoder: TextEncoder | None):
        self.store = store
        self.text_encoder = text_encoder

    def search(
        self,
        query_text: str,
        *,
        known_people: Sequence[str] = (),
        self_name: str = "",
        filters: SearchFilters | None = None,
        metadata_by_path: dict[str, CaptureMetadata] | None = None,
        rating_by_path: dict[str, int] | None = None,
        limit: int | None = 200,
        include_filename_matches: bool = True,
    ) -> list[SemanticSearchHit]:
        filters = filters or SearchFilters()
        parsed = parse_search_query(query_text, known_people=known_people, self_name=self_name)
        allowed_ids: set[int] | None = None
        if parsed.people:
            allowed_ids = image_ids_matching_people(self.store.connection, parsed.people, match_all=True)
            if not allowed_ids:
                return []

        rows = self.store.list_images(require_embedding=True)
        image_ids: list[int] = []
        paths: list[str] = []
        embeddings: list[np.ndarray] = []
        for row in rows:
            image_id = int(row["id"])
            if allowed_ids is not None and image_id not in allowed_ids:
                continue
            image_ids.append(image_id)
            paths.append(str(row["source_path"]))
            embeddings.append(self.store.get_embedding(image_id))

        hits_by_id: dict[int, SemanticSearchHit] = {}
        if include_filename_matches and parsed.semantic_text:
            for image_id, path in zip(image_ids, paths):
                score = filename_match_score(path, parsed.semantic_text)
                if score <= 0.0:
                    continue
                hits_by_id[image_id] = SemanticSearchHit(
                    image_id=image_id,
                    source_path=path,
                    confidence=score,
                    matched_by=("filename",),
                )

        if parsed.semantic_text:
            if self.text_encoder is None:
                raise ValueError("CLIP text encoder is required for semantic search terms")
            index = SemanticVectorIndex(image_ids, paths, embeddings)
            confidence_floor = filters.min_confidence if filters.min_confidence > 0.0 else -1.0
            semantic_hits = index.search(
                self.text_encoder.encode(parsed.semantic_text),
                limit=None,
                min_confidence=confidence_floor,
            )
            for hit in semantic_hits:
                existing = hits_by_id.get(hit.image_id)
                if existing is None:
                    hits_by_id[hit.image_id] = SemanticSearchHit(
                        image_id=hit.image_id,
                        source_path=hit.source_path,
                        confidence=hit.confidence,
                        matched_by=("semantic",),
                    )
                else:
                    hits_by_id[hit.image_id] = SemanticSearchHit(
                        image_id=existing.image_id,
                        source_path=existing.source_path,
                        confidence=max(existing.confidence, hit.confidence),
                        matched_by=tuple(sorted({*existing.matched_by, "semantic"})),
                    )
            hits = sorted(
                hits_by_id.values(),
                key=lambda hit: (-hit.confidence, hit.source_path.casefold()),
            )
            if filters.min_confidence <= 0.0:
                hits = self._apply_auto_confidence_floor(hits)
        else:
            hits = [
                SemanticSearchHit(image_id=image_id, source_path=path, confidence=1.0, matched_by=("people",))
                for image_id, path in zip(image_ids, paths)
            ]

        people_by_id = _people_by_image_id(self.store.connection)
        filtered = [
            SemanticSearchHit(
                image_id=hit.image_id,
                source_path=hit.source_path,
                confidence=hit.confidence,
                people=tuple(sorted(people_by_id.get(hit.image_id, ()))),
                matched_by=_match_kinds_with_people(hit.matched_by, parsed.people),
            )
            for hit in hits
            if _matches_filters(hit, filters, metadata_by_path or {}, rating_by_path or {})
        ]
        return filtered[:limit] if limit is not None else filtered

    @classmethod
    def _apply_auto_confidence_floor(cls, hits: Sequence[SemanticSearchHit]) -> list[SemanticSearchHit]:
        semantic_scores = [
            hit.confidence
            for hit in hits
            if "semantic" in hit.matched_by and "filename" not in hit.matched_by
        ]
        if not semantic_scores:
            return list(hits)
        floor = max(semantic_scores) - cls.AUTO_CONFIDENCE_BAND
        return [
            hit
            for hit in hits
            if "semantic" not in hit.matched_by
            or "filename" in hit.matched_by
            or hit.confidence >= floor
        ]


def parse_search_query(
    text: str,
    *,
    known_people: Sequence[str],
    self_name: str = "",
) -> ParsedSearchQuery:
    remaining = f" {text or ''} "
    people: list[str] = []
    aliases: dict[str, str] = {name.casefold(): name for name in known_people if name.strip()}
    if self_name.strip():
        aliases["me"] = self_name.strip()
        aliases["myself"] = self_name.strip()
    for alias, canonical in sorted(aliases.items(), key=lambda item: len(item[0]), reverse=True):
        pattern = re.compile(rf"(?<!\w){re.escape(alias)}(?!\w)", re.IGNORECASE)
        if pattern.search(remaining):
            if canonical not in people:
                people.append(canonical)
            remaining = pattern.sub(" ", remaining)
    semantic_text = _clean_semantic_remainder(remaining)
    return ParsedSearchQuery(semantic_text=semantic_text, people=tuple(people))


def filename_match_score(path: str, query_text: str) -> float:
    terms = tuple(term for term in re.split(r"\s+", query_text.strip().casefold()) if term)
    if not terms:
        return 0.0
    filename = Path(path).name.casefold()
    stem = Path(path).stem.casefold()
    normalized_stem = re.sub(r"[_\-.]+", " ", stem)
    if all(term in filename for term in terms):
        return 1.0
    if all(term in normalized_stem for term in terms):
        return 0.98
    matched = sum(1 for term in terms if term in filename or term in normalized_stem)
    if matched == 0:
        return 0.0
    return 0.65 + 0.25 * (matched / len(terms))


def metadata_from_store_row(row) -> CaptureMetadata | None:
    payload_text = row["metadata_json"] if "metadata_json" in row.keys() else ""
    if not payload_text:
        return None
    try:
        payload = json.loads(payload_text)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    captured_at = _parse_datetime(payload.get("captured_at") or payload.get("date"))
    return CaptureMetadata(
        path=str(row["source_path"]),
        camera=str(payload.get("camera") or payload.get("camera_model") or ""),
        lens=str(payload.get("lens") or ""),
        captured_at=str(payload.get("captured_at") or ""),
        captured_at_value=captured_at,
    )


def _matches_filters(
    hit: SemanticSearchHit,
    filters: SearchFilters,
    metadata_by_path: dict[str, CaptureMetadata],
    rating_by_path: dict[str, int],
) -> bool:
    if "semantic" in hit.matched_by and hit.confidence < filters.min_confidence:
        return False
    if filters.folder_text.strip():
        folder = str(Path(hit.source_path).parent).casefold()
        if filters.folder_text.strip().casefold() not in folder:
            return False
    if filters.min_rating > 0 and int(rating_by_path.get(hit.source_path, 0)) < filters.min_rating:
        return False
    metadata = metadata_by_path.get(hit.source_path)
    if filters.camera_text.strip():
        if metadata is None or filters.camera_text.strip().casefold() not in metadata.camera.casefold():
            return False
    if filters.lens_text.strip():
        if metadata is None or filters.lens_text.strip().casefold() not in metadata.lens.casefold():
            return False
    captured_at = None if metadata is None else metadata.captured_at_value
    if filters.captured_after is not None:
        if captured_at is None or captured_at.date() < filters.captured_after:
            return False
    if filters.captured_before is not None:
        if captured_at is None or captured_at.date() > filters.captured_before:
            return False
    return True


def _people_by_image_id(connection) -> dict[int, set[str]]:
    from .people_search import named_people_by_image_id

    return named_people_by_image_id(connection)


def _match_kinds_with_people(match_kinds: tuple[str, ...], people: tuple[str, ...]) -> tuple[str, ...]:
    values = set(match_kinds)
    if people:
        values.add("people")
    return tuple(sorted(values))


def _clean_semantic_remainder(text: str) -> str:
    cleaned = re.sub(r"\b(photos?|pictures?|images?)\b", " ", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(a|an|the|of|with|and|at|on|in|from|show|find|search|for)\b", " ", cleaned, flags=re.IGNORECASE)
    return " ".join(cleaned.split())


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _normalize_matrix(matrix: np.ndarray) -> np.ndarray:
    values = np.asarray(matrix, dtype=np.float32)
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return values / norms


def _normalize_vector(vector: np.ndarray) -> np.ndarray:
    values = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(values))
    if norm == 0.0:
        return values
    return values / norm
