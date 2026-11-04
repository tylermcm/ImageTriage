"""Lossless native engine documents, independent of legacy score enrichment."""
from __future__ import annotations

import copy
import json
from pathlib import Path

SCHEMA = "aiculler-decisions/1"
_BUCKETS = {"keep": "keeper", "reject": "reject", "review": "needs_review"}


class NativeCullResults:
    """Own a JSON document without collapsing capture, group, scene or set IDs.

    Copies at the boundary keep host annotations from mutating engine evidence.
    Unknown document and file fields are deliberately retained.
    """

    def __init__(self, document: dict):
        if not isinstance(document, dict) or document.get("schema") != SCHEMA:
            raise ValueError(f"Expected native culler schema {SCHEMA}")
        files = document.get("files")
        if not isinstance(files, dict):
            raise ValueError("Native culler results must contain a files object")
        for path, row in files.items():
            if not isinstance(path, str) or not isinstance(row, dict):
                raise ValueError("Native culler file entries must be path/object pairs")
            if not isinstance(row.get("decision"), str) or row["decision"] not in _BUCKETS:
                raise ValueError(f"Invalid native decision for {path!r}: {row.get('decision')!r}")
        self._document = copy.deepcopy(document)

    @classmethod
    def load(cls, path: str | Path) -> NativeCullResults:
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def to_document(self) -> dict:
        return copy.deepcopy(self._document)

    def bucket(self, path: str) -> str:
        # A rank-1 keeper is still a keeper, never an automatic Winner.
        return _BUCKETS[self._document["files"][path]["decision"]]

    def file(self, path: str) -> dict:
        return copy.deepcopy(self._document["files"][path])


def native_ai_bundle(document: dict, source: str | Path):
    """Adapt native data for review without calling legacy score enrichment."""
    from .ai_results import AIBundle, AIConfidenceBucket, AIImageResult, _fast_path_key
    from .scanner import normalized_path_key

    native = NativeCullResults(document)
    document = native.to_document()
    results, fast, groups, exact = {}, {}, {}, {}
    for path, row in document["files"].items():
        bucket = {"keep": AIConfidenceBucket.LIKELY_KEEPER,
                  "reject": AIConfidenceBucket.LIKELY_REJECT,
                  "review": AIConfidenceBucket.NEEDS_REVIEW}[row["decision"]]
        result = AIImageResult(
            image_id=str(row.get("capture", path)), file_path=path,
            file_name=Path(path.replace("\\", "/")).name, group_id=str(row.get("group", "")),
            group_size=row.get("set_size") or 0, rank_in_group=row.get("rank_in_set"),
            score=row.get("score"), confidence_bucket=bucket,
            confidence_summary="; ".join(str(r) for r in row.get("reasons", [])),
            native_payload=copy.deepcopy(row))
        results[normalized_path_key(path)] = result
        exact[path] = result
        fast[_fast_path_key(path.replace("\\", "/"))] = result
        groups.setdefault(result.group_id, []).append(result)
    return AIBundle(
        source_path=str(source), export_csv_path=str(source),
        results_by_path=results, results_by_fast_path=fast,
        results_by_group={key: tuple(rows) for key, rows in groups.items()},
        normalized_scores_by_path={}, summary=copy.deepcopy(document.get("summary", {})),
        native_document=document, native_results_by_path=exact)
