from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .models import ImageRecord


_INVALID_FOLDER_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_WHITESPACE = re.compile(r"\s+")
_UNDERSCORES = re.compile(r"_+")
_RESERVED_WINDOWS_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}


@dataclass(slots=True, frozen=True)
class SemanticClassification:
    file_path: str
    primary_label: str
    primary_score: float
    status: str = "ready"

    @property
    def is_ready(self) -> bool:
        return self.status == "ready" and bool(self.primary_label)


def load_semantic_classifications(path: str | Path) -> dict[str, SemanticClassification]:
    csv_path = Path(path)
    if not csv_path.exists():
        raise FileNotFoundError(csv_path)

    classifications: dict[str, SemanticClassification] = {}
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            file_path = str(row.get("file_path") or "").strip()
            if not file_path:
                continue
            key = normalized_semantic_path_key(file_path)
            if not key:
                continue
            classifications[key] = SemanticClassification(
                file_path=file_path,
                primary_label=str(row.get("primary_label") or "").strip(),
                primary_score=_parse_score(row.get("primary_score")),
                status=str(row.get("status") or "ready").strip().casefold(),
            )
    return classifications


def normalized_semantic_path_key(path: str | Path) -> str:
    return os.path.normpath(str(Path(path).expanduser())).casefold()


def semantic_classification_for_record(
    record: ImageRecord,
    classifications: dict[str, SemanticClassification],
) -> SemanticClassification | None:
    for path in record.stack_paths:
        classification = classifications.get(normalized_semantic_path_key(path))
        if classification is not None and classification.is_ready:
            return classification
    return None


def semantic_folder_name(label: str) -> str:
    cleaned = _INVALID_FOLDER_CHARS.sub("_", label.strip())
    cleaned = _WHITESPACE.sub("_", cleaned)
    cleaned = _UNDERSCORES.sub("_", cleaned)
    cleaned = cleaned.strip(" ._")
    if not cleaned:
        return "unclassified"
    if cleaned.casefold() in _RESERVED_WINDOWS_NAMES:
        cleaned = f"{cleaned}_images"
    return cleaned[:80].rstrip(" ._") or "unclassified"


def _parse_score(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


__all__ = [
    "SemanticClassification",
    "load_semantic_classifications",
    "normalized_semantic_path_key",
    "semantic_classification_for_record",
    "semantic_folder_name",
]
