"""Append-safe JSONL storage for the legacy cluster-label fallback.

The Speed Cull surface writes to the host DecisionStore via DecisionBridge in
production. ClusterLabelStore is kept as an offline fallback so tests and
standalone runs without the host present still have a place to persist
decisions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Dict, List, Optional


def pair_key(image_a_id: str, image_b_id: str) -> tuple[str, str]:
    """Return an order-independent key identifying a pair of images."""

    return tuple(sorted((image_a_id, image_b_id)))


@dataclass(frozen=True)
class ClusterLabelRecord:
    """One cluster annotation record written to JSONL."""

    cluster_id: str
    best_image_ids: List[str]
    acceptable_image_ids: List[str]
    reject_image_ids: List[str]
    timestamp: str
    annotator_id: Optional[str]


class ClusterLabelStore:
    """Append-only storage and resume tracking for cluster labels."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.records = _load_jsonl(path)
        self.records_by_cluster_id: Dict[str, Dict[str, object]] = {}
        for record in self.records:
            self.records_by_cluster_id[str(record["cluster_id"])] = record

    def has_cluster(self, cluster_id: str) -> bool:
        """Return whether the cluster already has a saved label."""

        return cluster_id in self.records_by_cluster_id

    def count(self) -> int:
        """Return the number of labeled clusters."""

        return len(self.records_by_cluster_id)

    def clear(self) -> None:
        """Delete saved cluster labels and reset in-memory resume state."""

        self.records.clear()
        self.records_by_cluster_id.clear()
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            self.path.write_text("", encoding="utf-8")

    def get_latest(self, cluster_id: str) -> Optional[Dict[str, object]]:
        """Return the most recent saved label for a cluster, if present."""

        return self.records_by_cluster_id.get(cluster_id)

    def append(
        self,
        *,
        cluster_id: str,
        best_image_ids: List[str],
        acceptable_image_ids: List[str],
        reject_image_ids: List[str],
        annotator_id: Optional[str],
    ) -> ClusterLabelRecord:
        """Append a new cluster label record and update resume state."""

        record = ClusterLabelRecord(
            cluster_id=cluster_id,
            best_image_ids=best_image_ids,
            acceptable_image_ids=acceptable_image_ids,
            reject_image_ids=reject_image_ids,
            timestamp=current_timestamp(),
            annotator_id=annotator_id,
        )
        _append_jsonl(self.path, asdict(record))
        self.records_by_cluster_id[cluster_id] = asdict(record)
        return record


def current_timestamp() -> str:
    """Return an ISO 8601 UTC timestamp for label records."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load_jsonl(path: Path) -> List[Dict[str, object]]:
    """Load JSONL records if the file exists."""

    if not path.exists():
        return []

    records: List[Dict[str, object]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            records.append(json.loads(text))
    return records


def _append_jsonl(path: Path, payload: Dict[str, object]) -> None:
    """Append a JSON object as one line to a JSONL file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload))
        handle.write("\n")
