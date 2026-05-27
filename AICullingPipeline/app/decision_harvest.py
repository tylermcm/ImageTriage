"""Harvest per-image decisions from the Image Triage host DecisionStore.

This module reads the host's manual-cull annotations (winner / reject / rating /
photoshop / tags / review_round) and emits a JSONL labels file the AI training
pipeline can consume directly. It is intentionally standalone — no PySide6 or
image_triage imports — so it runs in any environment where sqlite3 is available.

Usage as a library:

    from app.decision_harvest import harvest_decisions_for_artifacts
    summary = harvest_decisions_for_artifacts(
        artifacts_dir=Path(".../labeling_artifacts"),
        output_path=Path(".../labels/decision_labels.jsonl"),
    )

Usage as a CLI: see AICullingPipeline/scripts/harvest_decisions.py.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class HarvestSummary:
    """Quantitative report of one harvest pass."""

    artifacts_dir: Path
    output_path: Path
    db_path: Path
    session_id: str
    total_images: int
    matched_decisions: int
    winners: int
    rejects: int
    rated: int
    photoshop_marks: int
    skipped_no_file: int = 0
    skipped_modified: int = 0
    cluster_labels_path: Optional[Path] = None
    clusters_with_labels: int = 0
    derivable_pairs: int = 0

    def to_log_payload(self) -> Dict[str, Any]:
        return {
            "artifacts_dir": str(self.artifacts_dir),
            "output_path": str(self.output_path),
            "db_path": str(self.db_path),
            "session_id": self.session_id,
            "total_images": self.total_images,
            "matched_decisions": self.matched_decisions,
            "winners": self.winners,
            "rejects": self.rejects,
            "rated": self.rated,
            "photoshop_marks": self.photoshop_marks,
            "skipped_no_file": self.skipped_no_file,
            "skipped_modified": self.skipped_modified,
            "cluster_labels_path": str(self.cluster_labels_path) if self.cluster_labels_path else None,
            "clusters_with_labels": self.clusters_with_labels,
            "derivable_pairs": self.derivable_pairs,
        }


def default_decision_store_paths() -> List[Path]:
    """Common locations where the host writes decisions.sqlite3.

    Qt's QStandardPaths.AppDataLocation resolves to
    %APPDATA%\\<OrgName>\\<AppName>\\ on Windows when the host sets organization
    and application names; the actual Image Triage host uses "Image Triage" as
    the application name under the "Codex" organization, so the live path on
    Windows is %APPDATA%\\Codex\\Image Triage\\decisions.sqlite3. Other
    candidates are checked as fallbacks for dev installs.
    """

    appdata = os.environ.get("APPDATA", "")
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    home = Path.home()
    candidates = [
        Path(appdata) / "Codex" / "Image Triage" / "decisions.sqlite3" if appdata else None,
        Path(appdata) / "ImageTriage" / "decisions.sqlite3" if appdata else None,
        Path(appdata) / "ImageTriage" / "ImageTriage" / "decisions.sqlite3" if appdata else None,
        Path(local_appdata) / "Image Triage" / "decisions.sqlite3" if local_appdata else None,
        home / ".image-triage" / "decisions.sqlite3",
    ]
    return [path for path in candidates if path is not None]


def find_decision_store_db(*, override: Optional[Path] = None) -> Optional[Path]:
    """Resolve the path to the DecisionStore DB, preferring the override if provided."""

    if override is not None:
        candidate = Path(override).expanduser().resolve()
        return candidate if candidate.exists() else None
    for candidate in default_decision_store_paths():
        if candidate.exists():
            return candidate
    return None


def load_clusters_csv_rows(clusters_path: Path) -> List[Dict[str, str]]:
    """Load the cluster manifest produced by the labeling artifacts pipeline."""

    with clusters_path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def build_image_index(clusters_path: Path) -> List[Dict[str, str]]:
    """Return a list of {image_id, file_path, file_name} dicts in source order."""

    rows = load_clusters_csv_rows(clusters_path)
    index: List[Dict[str, str]] = []
    for row in rows:
        image_id = (row.get("image_id") or "").strip()
        file_path = (row.get("file_path") or "").strip()
        if not image_id or not file_path:
            continue
        index.append(
            {
                "image_id": image_id,
                "file_path": file_path,
                "file_name": (row.get("file_name") or Path(file_path).name).strip(),
                "cluster_id": (row.get("cluster_id") or "").strip(),
            }
        )
    return index


def fetch_decisions(
    db_path: Path,
    *,
    session_id: str,
    paths: Iterable[str],
) -> Dict[str, Dict[str, Any]]:
    """Return {path: decision_dict} for every path with a stored annotation."""

    path_list = [p for p in paths if p]
    if not path_list:
        return {}
    decisions: Dict[str, Dict[str, Any]] = {}
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        for chunk in _chunked(path_list, 400):
            placeholders = ",".join("?" for _ in chunk)
            rows = connection.execute(
                f"""
                SELECT path, modified_ns, file_size, winner, reject, photoshop, rating, tags_json, review_round
                FROM decisions
                WHERE session_id = ?
                  AND path IN ({placeholders})
                """,
                [session_id, *chunk],
            ).fetchall()
            for row in rows:
                decisions[str(row["path"])] = {
                    "modified_ns": int(row["modified_ns"] or 0),
                    "file_size": int(row["file_size"] or 0),
                    "winner": bool(row["winner"]),
                    "reject": bool(row["reject"]),
                    "photoshop": bool(row["photoshop"]),
                    "rating": int(row["rating"] or 0),
                    "tags": tuple(json.loads(row["tags_json"] or "[]")),
                    "review_round": str(row["review_round"] or ""),
                }
    return decisions


def harvest_decisions_for_artifacts(
    *,
    artifacts_dir: Path,
    output_path: Optional[Path] = None,
    db_path_override: Optional[Path] = None,
    session_id: str = "Default",
    clusters_filename: str = "clusters.csv",
    require_unchanged_file: bool = False,
    emit_cluster_labels: bool = True,
    cluster_labels_path: Optional[Path] = None,
    cluster_labels_annotator_id: Optional[str] = None,
) -> HarvestSummary:
    """Emit a JSONL labels file for every image in the labeling artifacts.

    Each emitted row contains: image_id, file_path, file_name, cluster_id,
    plus the host annotation (winner / reject / rating / photoshop / tags /
    review_round). Images with no stored decision are still emitted, with all
    decision fields zero/empty, so downstream tooling can distinguish
    "annotated as 0" from "never seen."
    """

    artifacts_dir = Path(artifacts_dir).expanduser().resolve()
    clusters_path = artifacts_dir / clusters_filename
    if not clusters_path.exists():
        raise FileNotFoundError(f"clusters.csv not found in {artifacts_dir}")

    if output_path is None:
        labels_dir = artifacts_dir.parent / "labels"
        output_path = labels_dir / "decision_labels.jsonl"
    output_path = Path(output_path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    db_path = find_decision_store_db(override=db_path_override)
    if db_path is None:
        raise FileNotFoundError(
            "decisions.sqlite3 not found in any known location. "
            "Pass --db-path explicitly if the host stores it elsewhere."
        )

    image_index = build_image_index(clusters_path)
    decisions = fetch_decisions(
        db_path,
        session_id=session_id,
        paths=[entry["file_path"] for entry in image_index],
    )

    counters = {
        "matched": 0,
        "winners": 0,
        "rejects": 0,
        "rated": 0,
        "photoshop": 0,
        "skipped_no_file": 0,
        "skipped_modified": 0,
    }

    with output_path.open("w", encoding="utf-8") as handle:
        for entry in image_index:
            file_path = entry["file_path"]
            file_stat = _safe_stat(file_path)
            decision = decisions.get(file_path)
            row: Dict[str, Any] = {
                "image_id": entry["image_id"],
                "file_path": file_path,
                "file_name": entry["file_name"],
                "cluster_id": entry["cluster_id"],
                "winner": False,
                "reject": False,
                "rating": 0,
                "photoshop": False,
                "tags": [],
                "review_round": "",
                "has_decision": False,
                "file_missing": file_stat is None,
                "decision_stale": False,
            }
            if file_stat is None:
                counters["skipped_no_file"] += 1
            if decision is not None:
                if require_unchanged_file and file_stat is not None:
                    if (
                        decision["modified_ns"] != file_stat.st_mtime_ns
                        or decision["file_size"] != file_stat.st_size
                    ):
                        row["decision_stale"] = True
                        counters["skipped_modified"] += 1
                        handle.write(json.dumps(row) + "\n")
                        continue
                row.update(
                    {
                        "winner": decision["winner"],
                        "reject": decision["reject"],
                        "rating": decision["rating"],
                        "photoshop": decision["photoshop"],
                        "tags": list(decision["tags"]),
                        "review_round": decision["review_round"],
                        "has_decision": True,
                    }
                )
                counters["matched"] += 1
                if decision["winner"]:
                    counters["winners"] += 1
                if decision["reject"]:
                    counters["rejects"] += 1
                if decision["rating"] > 0:
                    counters["rated"] += 1
                if decision["photoshop"]:
                    counters["photoshop"] += 1
            handle.write(json.dumps(row) + "\n")

    cluster_labels_written: Optional[Path] = None
    clusters_with_labels = 0
    derivable_pairs = 0
    if emit_cluster_labels:
        cluster_labels_target = (
            Path(cluster_labels_path).expanduser().resolve()
            if cluster_labels_path is not None
            else output_path.parent / "cluster_labels.jsonl"
        )
        cluster_summary = _emit_cluster_labels(
            cluster_labels_target,
            image_index=image_index,
            decisions=decisions,
            session_id=session_id,
            annotator_id=cluster_labels_annotator_id,
            require_unchanged_file=require_unchanged_file,
        )
        cluster_labels_written = cluster_labels_target
        clusters_with_labels = cluster_summary["clusters_with_labels"]
        derivable_pairs = cluster_summary["derivable_pairs"]

    summary = HarvestSummary(
        artifacts_dir=artifacts_dir,
        output_path=output_path,
        db_path=db_path,
        session_id=session_id,
        total_images=len(image_index),
        matched_decisions=counters["matched"],
        winners=counters["winners"],
        rejects=counters["rejects"],
        rated=counters["rated"],
        photoshop_marks=counters["photoshop"],
        skipped_no_file=counters["skipped_no_file"],
        skipped_modified=counters["skipped_modified"],
        cluster_labels_path=cluster_labels_written,
        clusters_with_labels=clusters_with_labels,
        derivable_pairs=derivable_pairs,
    )
    LOGGER.info("Harvest complete: %s", summary.to_log_payload())
    return summary


def _emit_cluster_labels(
    output_path: Path,
    *,
    image_index: List[Dict[str, str]],
    decisions: Dict[str, Dict[str, Any]],
    session_id: str,
    annotator_id: Optional[str],
    require_unchanged_file: bool,
) -> Dict[str, int]:
    """Translate per-image decisions into the trainer's cluster_labels.jsonl schema.

    Speed Cull is binary, so we always emit acceptable_image_ids=[] and put
    Accepts into best_image_ids. The existing transparent combiner trainer
    derives preference pairs from these records as ``best > reject`` within
    each cluster.
    """

    from datetime import datetime, timezone

    output_path = Path(output_path).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    grouped: Dict[str, Dict[str, List[str]]] = {}
    for entry in image_index:
        cluster_id = entry.get("cluster_id") or ""
        if not cluster_id:
            continue
        file_path = entry["file_path"]
        decision = decisions.get(file_path)
        if decision is None:
            continue
        if require_unchanged_file:
            file_stat = _safe_stat(file_path)
            if file_stat is None:
                continue
            if (
                decision["modified_ns"] != file_stat.st_mtime_ns
                or decision["file_size"] != file_stat.st_size
            ):
                continue

        bucket = grouped.setdefault(
            cluster_id,
            {"best": [], "reject": []},
        )
        if decision["reject"]:
            bucket["reject"].append(entry["image_id"])
        elif decision["winner"]:
            bucket["best"].append(entry["image_id"])
        # rating-only entries (no winner/reject) deliberately excluded so the
        # trainer is not confused by the host's separate star-rating axis.

    populated = []
    for cluster_id, bucket in sorted(grouped.items()):
        best = sorted(set(bucket["best"]))
        reject = sorted(set(bucket["reject"]))
        if not best and not reject:
            continue
        populated.append((cluster_id, best, reject))

    if not populated:
        # No DecisionStore-backed labels for this artifacts set. Leave any
        # existing cluster_labels.jsonl untouched so we don't destroy data
        # that was produced by another path.
        return {"clusters_with_labels": 0, "derivable_pairs": 0}

    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    derivable_pairs = 0
    with output_path.open("w", encoding="utf-8") as handle:
        for cluster_id, best, reject in populated:
            derivable_pairs += len(best) * len(reject)
            record = {
                "cluster_id": cluster_id,
                "best_image_ids": best,
                "acceptable_image_ids": [],
                "reject_image_ids": reject,
                "timestamp": timestamp,
                "annotator_id": annotator_id,
            }
            handle.write(json.dumps(record) + "\n")

    return {
        "clusters_with_labels": len(populated),
        "derivable_pairs": derivable_pairs,
    }


def _safe_stat(path: str) -> Optional[os.stat_result]:
    try:
        return os.stat(path)
    except OSError:
        return None


def _chunked(items: List[str], size: int) -> Iterable[List[str]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]
