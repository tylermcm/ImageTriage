from __future__ import annotations

import csv
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


AICULLING_ROOT = Path(__file__).resolve().parents[1] / "AICullingPipeline"
if str(AICULLING_ROOT) not in sys.path:
    sys.path.insert(0, str(AICULLING_ROOT))

from app.decision_harvest import (
    build_image_index,
    fetch_decisions,
    harvest_decisions_for_artifacts,
)


def _create_test_decision_db(db_path: Path) -> None:
    """Build a minimal decisions.sqlite3 mirroring the host's schema."""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY,
                created_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE decisions (
                session_id TEXT NOT NULL,
                path TEXT NOT NULL,
                modified_ns INTEGER NOT NULL,
                file_size INTEGER NOT NULL,
                winner INTEGER NOT NULL,
                reject INTEGER NOT NULL,
                photoshop INTEGER NOT NULL,
                rating INTEGER NOT NULL,
                tags_json TEXT NOT NULL,
                review_round TEXT NOT NULL,
                PRIMARY KEY (session_id, path)
            )
            """
        )
        connection.execute(
            "INSERT INTO sessions(session_id, created_at) VALUES (?, ?)",
            ("Default", "2026-05-24T00:00:00"),
        )
        connection.commit()


def _write_decision(
    db_path: Path,
    *,
    path: str,
    modified_ns: int,
    file_size: int,
    winner: bool = False,
    reject: bool = False,
    rating: int = 0,
    photoshop: bool = False,
    tags: list[str] | None = None,
    review_round: str = "",
    session_id: str = "Default",
) -> None:
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO decisions (session_id, path, modified_ns, file_size, winner, reject, photoshop, rating, tags_json, review_round)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                path,
                modified_ns,
                file_size,
                int(winner),
                int(reject),
                int(photoshop),
                rating,
                json.dumps(list(tags or [])),
                review_round,
            ),
        )
        connection.commit()


def _write_clusters_csv(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


class DecisionHarvestTests(unittest.TestCase):
    def test_build_image_index_orders_rows_and_keeps_required_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            artifacts_dir = Path(temp_dir) / "labeling_artifacts"
            artifacts_dir.mkdir(parents=True)
            _write_clusters_csv(
                artifacts_dir / "clusters.csv",
                [
                    {
                        "image_id": "image_a",
                        "file_path": "/photos/a.jpg",
                        "file_name": "a.jpg",
                        "cluster_id": "cluster_000",
                    },
                    {
                        "image_id": "image_b",
                        "file_path": "/photos/b.jpg",
                        "file_name": "b.jpg",
                        "cluster_id": "cluster_000",
                    },
                ],
            )

            index = build_image_index(artifacts_dir / "clusters.csv")

            self.assertEqual(len(index), 2)
            self.assertEqual(index[0]["image_id"], "image_a")
            self.assertEqual(index[0]["file_path"], "/photos/a.jpg")
            self.assertEqual(index[0]["cluster_id"], "cluster_000")

    def test_fetch_decisions_returns_only_matching_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "decisions.sqlite3"
            _create_test_decision_db(db_path)
            _write_decision(
                db_path, path="/photos/a.jpg", modified_ns=100, file_size=10, winner=True
            )
            _write_decision(
                db_path, path="/photos/b.jpg", modified_ns=200, file_size=20, reject=True
            )

            decisions = fetch_decisions(
                db_path,
                session_id="Default",
                paths=["/photos/a.jpg", "/photos/c.jpg"],
            )

            self.assertIn("/photos/a.jpg", decisions)
            self.assertNotIn("/photos/b.jpg", decisions)
            self.assertTrue(decisions["/photos/a.jpg"]["winner"])

    def test_harvest_emits_jsonl_with_decision_state_per_image(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            artifacts_dir = Path(temp_dir) / "labeling_artifacts"
            artifacts_dir.mkdir(parents=True)
            image_a = artifacts_dir / "a.jpg"
            image_b = artifacts_dir / "b.jpg"
            image_c = artifacts_dir / "c.jpg"
            image_a.write_bytes(b"fake")
            image_b.write_bytes(b"fake")
            image_c.write_bytes(b"fake")
            _write_clusters_csv(
                artifacts_dir / "clusters.csv",
                [
                    {
                        "image_id": "image_a",
                        "file_path": str(image_a),
                        "file_name": image_a.name,
                        "cluster_id": "cluster_000",
                    },
                    {
                        "image_id": "image_b",
                        "file_path": str(image_b),
                        "file_name": image_b.name,
                        "cluster_id": "cluster_000",
                    },
                    {
                        "image_id": "image_c",
                        "file_path": str(image_c),
                        "file_name": image_c.name,
                        "cluster_id": "cluster_001",
                    },
                ],
            )
            db_path = Path(temp_dir) / "decisions.sqlite3"
            _create_test_decision_db(db_path)
            stat_a = image_a.stat()
            stat_b = image_b.stat()
            _write_decision(
                db_path,
                path=str(image_a),
                modified_ns=stat_a.st_mtime_ns,
                file_size=stat_a.st_size,
                winner=True,
            )
            _write_decision(
                db_path,
                path=str(image_b),
                modified_ns=stat_b.st_mtime_ns,
                file_size=stat_b.st_size,
                reject=True,
                rating=2,
            )

            summary = harvest_decisions_for_artifacts(
                artifacts_dir=artifacts_dir,
                output_path=artifacts_dir.parent / "labels" / "decision_labels.jsonl",
                db_path_override=db_path,
                session_id="Default",
            )

            self.assertEqual(summary.total_images, 3)
            self.assertEqual(summary.matched_decisions, 2)
            self.assertEqual(summary.winners, 1)
            self.assertEqual(summary.rejects, 1)

            rows = [json.loads(line) for line in summary.output_path.read_text().splitlines()]
            self.assertEqual(len(rows), 3)
            by_id = {row["image_id"]: row for row in rows}
            self.assertTrue(by_id["image_a"]["winner"])
            self.assertTrue(by_id["image_b"]["reject"])
            self.assertTrue(by_id["image_b"]["has_decision"])
            self.assertFalse(by_id["image_c"]["has_decision"])
            self.assertEqual(by_id["image_c"]["rating"], 0)

    def test_harvest_emits_cluster_labels_with_binary_buckets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            artifacts_dir = Path(temp_dir) / "labeling_artifacts"
            artifacts_dir.mkdir(parents=True)
            image_a = artifacts_dir / "a.jpg"
            image_b = artifacts_dir / "b.jpg"
            image_c = artifacts_dir / "c.jpg"
            image_d = artifacts_dir / "d.jpg"
            for image in (image_a, image_b, image_c, image_d):
                image.write_bytes(b"fake")
            _write_clusters_csv(
                artifacts_dir / "clusters.csv",
                [
                    {"image_id": "image_a", "file_path": str(image_a), "file_name": image_a.name, "cluster_id": "cluster_001"},
                    {"image_id": "image_b", "file_path": str(image_b), "file_name": image_b.name, "cluster_id": "cluster_001"},
                    {"image_id": "image_c", "file_path": str(image_c), "file_name": image_c.name, "cluster_id": "cluster_001"},
                    # Cluster 2 has only an accept, so no pairs derivable from it.
                    {"image_id": "image_d", "file_path": str(image_d), "file_name": image_d.name, "cluster_id": "cluster_002"},
                ],
            )
            db_path = Path(temp_dir) / "decisions.sqlite3"
            _create_test_decision_db(db_path)
            for image, kwargs in [
                (image_a, {"winner": True}),
                (image_b, {"reject": True}),
                (image_c, {"reject": True}),
                (image_d, {"winner": True}),
            ]:
                stat = image.stat()
                _write_decision(
                    db_path,
                    path=str(image),
                    modified_ns=stat.st_mtime_ns,
                    file_size=stat.st_size,
                    **kwargs,
                )

            summary = harvest_decisions_for_artifacts(
                artifacts_dir=artifacts_dir,
                output_path=artifacts_dir.parent / "labels" / "decision_labels.jsonl",
                db_path_override=db_path,
                session_id="Default",
            )

            self.assertIsNotNone(summary.cluster_labels_path)
            self.assertTrue(summary.cluster_labels_path.exists())
            # cluster_001 has 1 best × 2 rejects = 2 derivable pairs;
            # cluster_002 has only a best, so 0 derivable pairs.
            self.assertEqual(summary.clusters_with_labels, 2)
            self.assertEqual(summary.derivable_pairs, 2)

            records = [
                json.loads(line)
                for line in summary.cluster_labels_path.read_text().splitlines()
            ]
            by_cluster = {row["cluster_id"]: row for row in records}
            self.assertEqual(by_cluster["cluster_001"]["best_image_ids"], ["image_a"])
            self.assertEqual(
                by_cluster["cluster_001"]["reject_image_ids"],
                ["image_b", "image_c"],
            )
            # Speed Cull is binary; acceptable bucket is always empty.
            self.assertEqual(by_cluster["cluster_001"]["acceptable_image_ids"], [])
            self.assertEqual(by_cluster["cluster_002"]["best_image_ids"], ["image_d"])
            self.assertEqual(by_cluster["cluster_002"]["reject_image_ids"], [])

    def test_harvest_can_skip_cluster_labels_emission(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            artifacts_dir = Path(temp_dir) / "labeling_artifacts"
            artifacts_dir.mkdir(parents=True)
            image_a = artifacts_dir / "a.jpg"
            image_a.write_bytes(b"fake")
            _write_clusters_csv(
                artifacts_dir / "clusters.csv",
                [
                    {"image_id": "image_a", "file_path": str(image_a), "file_name": image_a.name, "cluster_id": "cluster_001"},
                ],
            )
            db_path = Path(temp_dir) / "decisions.sqlite3"
            _create_test_decision_db(db_path)
            stat = image_a.stat()
            _write_decision(
                db_path,
                path=str(image_a),
                modified_ns=stat.st_mtime_ns,
                file_size=stat.st_size,
                winner=True,
            )

            summary = harvest_decisions_for_artifacts(
                artifacts_dir=artifacts_dir,
                output_path=artifacts_dir.parent / "labels" / "decision_labels.jsonl",
                db_path_override=db_path,
                session_id="Default",
                emit_cluster_labels=False,
            )

            self.assertIsNone(summary.cluster_labels_path)
            self.assertFalse((artifacts_dir.parent / "labels" / "cluster_labels.jsonl").exists())

    def test_harvest_with_no_decisions_leaves_existing_cluster_labels_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            artifacts_dir = Path(temp_dir) / "labeling_artifacts"
            artifacts_dir.mkdir(parents=True)
            image_a = artifacts_dir / "a.jpg"
            image_a.write_bytes(b"fake")
            _write_clusters_csv(
                artifacts_dir / "clusters.csv",
                [
                    {
                        "image_id": "image_a",
                        "file_path": str(image_a),
                        "file_name": image_a.name,
                        "cluster_id": "cluster_001",
                    },
                ],
            )
            db_path = Path(temp_dir) / "decisions.sqlite3"
            _create_test_decision_db(db_path)
            # No rows added — DecisionStore is empty for these paths.

            labels_dir = artifacts_dir.parent / "labels"
            labels_dir.mkdir(parents=True)
            existing_cluster_labels = labels_dir / "cluster_labels.jsonl"
            previous_payload = (
                '{"cluster_id": "cluster_legacy", "best_image_ids": ["legacy_a"], '
                '"acceptable_image_ids": [], "reject_image_ids": [], '
                '"timestamp": "2024-01-01T00:00:00+00:00", "annotator_id": null}\n'
            )
            existing_cluster_labels.write_text(previous_payload, encoding="utf-8")

            summary = harvest_decisions_for_artifacts(
                artifacts_dir=artifacts_dir,
                output_path=labels_dir / "decision_labels.jsonl",
                db_path_override=db_path,
                session_id="Default",
            )

            self.assertEqual(summary.matched_decisions, 0)
            self.assertEqual(summary.clusters_with_labels, 0)
            # The existing cluster_labels.jsonl must NOT have been truncated.
            self.assertEqual(existing_cluster_labels.read_text(encoding="utf-8"), previous_payload)

    def test_harvest_marks_decision_stale_when_file_changed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            artifacts_dir = Path(temp_dir) / "labeling_artifacts"
            artifacts_dir.mkdir(parents=True)
            image_a = artifacts_dir / "a.jpg"
            image_a.write_bytes(b"fake-original")
            _write_clusters_csv(
                artifacts_dir / "clusters.csv",
                [
                    {
                        "image_id": "image_a",
                        "file_path": str(image_a),
                        "file_name": image_a.name,
                        "cluster_id": "cluster_000",
                    }
                ],
            )
            db_path = Path(temp_dir) / "decisions.sqlite3"
            _create_test_decision_db(db_path)
            # Persist a decision with bogus (older) mtime/size so the harvester sees a mismatch.
            _write_decision(
                db_path,
                path=str(image_a),
                modified_ns=1,
                file_size=999,
                winner=True,
            )

            summary = harvest_decisions_for_artifacts(
                artifacts_dir=artifacts_dir,
                output_path=artifacts_dir.parent / "labels" / "decision_labels.jsonl",
                db_path_override=db_path,
                session_id="Default",
                require_unchanged_file=True,
            )

            self.assertEqual(summary.matched_decisions, 0)
            self.assertEqual(summary.skipped_modified, 1)
            row = json.loads(summary.output_path.read_text().splitlines()[0])
            self.assertTrue(row["decision_stale"])
            self.assertFalse(row["winner"])


if __name__ == "__main__":
    unittest.main()
