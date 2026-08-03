from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from image_triage.dino_prefilter import (
    DINOPrefilterSignals,
    DINOPrefilterSettings,
    build_dino_prefilter_paths,
    decide_dino_prefilter_action,
    load_dino_prefilter_decisions,
    run_dino_prefilter_from_signal_rows,
    write_dino_prefilter_audit,
)
from image_triage.perceptual_hash import (
    find_perceptual_duplicate_groups,
    find_perceptual_duplicate_groups_with_stats,
)
from image_triage.phash_prefilter import (
    PHashPrefilterSettings,
    build_phash_prefilter_paths,
    run_phash_prefilter_from_signal_rows,
)


class DINOPrefilterTests(unittest.TestCase):
    def test_default_settings_are_disabled_and_base_model_only(self) -> None:
        settings = DINOPrefilterSettings()

        self.assertFalse(settings.enabled)
        self.assertEqual("base_model_only", settings.to_cache_payload()["model_policy"])

    def test_cache_key_changes_when_behavioral_settings_change(self) -> None:
        base = DINOPrefilterSettings()
        changed = DINOPrefilterSettings(aggressiveness_percent=95)

        self.assertNotEqual(base.cache_key(), changed.cache_key())

    def test_audit_writer_creates_independent_prefilter_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = build_dino_prefilter_paths(temp_dir)
            payload = write_dino_prefilter_audit(
                paths,
                settings=DINOPrefilterSettings(enabled=True),
                rows=(
                    {
                        "path": str(Path(temp_dir) / "bad.jpg"),
                        "action": "remove_from_pool",
                        "reason": "technical_trash",
                        "score": 0.98,
                    },
                ),
                scanned_count=10,
                removed_from_pool_count=1,
                reason_counts={"technical_trash": 1},
            )

            self.assertTrue(paths.report_path.exists())
            self.assertTrue(paths.rows_path.exists())
            self.assertEqual(paths.artifact_dir, Path(temp_dir) / ".image_triage_ai" / "dino_prefilter")
            self.assertEqual(10, payload["counts"]["scanned"])
            self.assertEqual(1, payload["counts"]["removed_from_pool"])
            rows = paths.rows_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(1, len(rows))
            self.assertEqual("technical_trash", json.loads(rows[0])["reason"])
            loaded = load_dino_prefilter_decisions(paths)
            self.assertEqual("remove_from_pool", loaded[str(Path(temp_dir) / "bad.jpg")].action)

    def test_decision_passes_when_disabled(self) -> None:
        decision = decide_dino_prefilter_action(
            DINOPrefilterSignals(path="bad.jpg", technical_trash_score=1.0),
            DINOPrefilterSettings(enabled=False),
        )

        self.assertEqual("pass", decision.action)

    def test_decision_removes_enabled_reason_above_threshold(self) -> None:
        decision = decide_dino_prefilter_action(
            DINOPrefilterSignals(path="bad.jpg", technical_trash_score=0.91),
            DINOPrefilterSettings(enabled=True, aggressiveness_percent=85),
        )

        self.assertEqual("remove_from_pool", decision.action)
        self.assertEqual("technical_trash", decision.reason)

    def test_decision_pool_removes_when_mode_enabled(self) -> None:
        decision = decide_dino_prefilter_action(
            DINOPrefilterSignals(path="dupe.jpg", duplicate_trash_score=0.96),
            DINOPrefilterSettings(
                enabled=True,
                aggressiveness_percent=90,
            ),
        )

        self.assertEqual("remove_from_pool", decision.action)

    def test_manual_keep_unconditionally_protects_dino_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = build_dino_prefilter_paths(temp_dir)
            image_path = str(Path(temp_dir) / "keeper.jpg")

            decisions = run_dino_prefilter_from_signal_rows(
                (
                    {
                        "file_path": image_path,
                        "group_size": "4",
                        "dino_rank": "4",
                    },
                ),
                settings=DINOPrefilterSettings(
                    enabled=True,
                    aggressiveness_percent=85,
                ),
                paths=paths,
                protected_paths=(image_path,),
            )

        self.assertEqual("rescued", decisions[image_path].action)
        self.assertEqual(("manual_keep",), decisions[image_path].rescue_reasons)

    def test_dino_rank_one_does_not_override_technical_trash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = build_dino_prefilter_paths(temp_dir)
            image_path = str(Path(temp_dir) / "blurry-central-frame.jpg")

            decisions = run_dino_prefilter_from_signal_rows(
                (
                    {
                        "file_path": image_path,
                        "group_size": "4",
                        "dino_rank": "1",
                        "detail": "0.0",
                    },
                ),
                settings=DINOPrefilterSettings(enabled=True, aggressiveness_percent=85),
                paths=paths,
            )

        self.assertEqual("remove_from_pool", decisions[image_path].action)
        self.assertEqual("technical_trash", decisions[image_path].reason)

    def test_burst_rescues_best_duplicate_only_member_when_rank_one_is_technical_trash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = build_dino_prefilter_paths(temp_dir)
            rows = []
            for rank in range(1, 5):
                rows.append(
                    {
                        "file_path": str(Path(temp_dir) / f"burst-{rank}.jpg"),
                        "cluster_id": "cluster-1",
                        "group_size": "4",
                        "dino_rank": str(rank),
                        "detail": "0.0" if rank == 1 else "0.95",
                    }
                )

            decisions = run_dino_prefilter_from_signal_rows(
                rows,
                settings=DINOPrefilterSettings(enabled=True, aggressiveness_percent=70),
                paths=paths,
            )

        rank_one = decisions[str(Path(temp_dir) / "burst-1.jpg")]
        rescued = decisions[str(Path(temp_dir) / "burst-2.jpg")]
        self.assertEqual("remove_from_pool", rank_one.action)
        self.assertEqual("technical_trash", rank_one.reason)
        self.assertEqual("rescued", rescued.action)
        self.assertEqual("duplicate_trash", rescued.reason)
        self.assertIn("duplicate_group_representative", rescued.rescue_reasons)

    def test_burst_can_be_fully_removed_when_every_member_is_independently_trash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = build_dino_prefilter_paths(temp_dir)
            rows = [
                {
                    "file_path": str(Path(temp_dir) / f"bad-{rank}.jpg"),
                    "cluster_id": "cluster-1",
                    "group_size": "3",
                    "dino_rank": str(rank),
                    "detail": "0.0",
                }
                for rank in range(1, 4)
            ]

            decisions = run_dino_prefilter_from_signal_rows(
                rows,
                settings=DINOPrefilterSettings(enabled=True, aggressiveness_percent=70),
                paths=paths,
            )

        self.assertTrue(all(decision.action == "remove_from_pool" for decision in decisions.values()))
        self.assertTrue(all(decision.reason == "technical_trash" for decision in decisions.values()))

    def test_duplicate_primary_reason_does_not_hide_independent_technical_trash(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = build_dino_prefilter_paths(temp_dir)
            rows = [
                {
                    "file_path": str(Path(temp_dir) / f"bad-{rank}.jpg"),
                    "cluster_id": "cluster-1",
                    "group_size": "3",
                    "dino_rank": str(rank),
                    "detail": "0.1",
                }
                for rank in range(1, 4)
            ]

            decisions = run_dino_prefilter_from_signal_rows(
                rows,
                settings=DINOPrefilterSettings(enabled=True, aggressiveness_percent=70),
                paths=paths,
            )

        self.assertEqual("duplicate_trash", decisions[str(Path(temp_dir) / "bad-3.jpg")].reason)
        self.assertTrue(all(decision.action == "remove_from_pool" for decision in decisions.values()))

    def test_phash_group_preserves_one_member_even_if_input_has_no_representative(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = build_phash_prefilter_paths(temp_dir)
            rows = [
                {
                    "file_path": str(Path(temp_dir) / f"duplicate-{rank}.jpg"),
                    "phash_group": "phash-1",
                    "phash_rank": str(rank),
                    "phash_duplicate_score": "1.0",
                    "best_representative": "0",
                }
                for rank in range(1, 4)
            ]

            decisions = run_phash_prefilter_from_signal_rows(
                rows,
                settings=PHashPrefilterSettings(enabled=True, hamming_threshold=0),
                paths=paths,
            )

        rescued = decisions[str(Path(temp_dir) / "duplicate-1.jpg")]
        self.assertEqual("rescued", rescued.action)
        self.assertIn("duplicate_group_representative", rescued.rescue_reasons)
        self.assertEqual(
            2,
            sum(decision.action == "remove_from_pool" for decision in decisions.values()),
        )

    def test_perceptual_hash_groups_identical_images(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            base = np.tile(np.arange(64, dtype=np.uint8), (64, 1))
            rgb = np.dstack((base, base, base))
            path_a = root / "a.jpg"
            path_b = root / "b.jpg"
            path_c = root / "c.jpg"
            Image.fromarray(rgb).save(path_a)
            Image.fromarray(rgb).save(path_b)
            Image.fromarray(255 - rgb).save(path_c)

            groups = find_perceptual_duplicate_groups(
                [str(path_a), str(path_b), str(path_c)],
                hamming_threshold=6,
            )

        grouped = [set(group.members) for group in groups]
        self.assertIn({str(path_a), str(path_b)}, grouped)

    def test_perceptual_hash_reports_foreground_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths: list[str] = []
            for index in range(5):
                path = root / f"{index}.jpg"
                Image.fromarray(np.full((32, 32, 3), index * 30, dtype=np.uint8)).save(path)
                paths.append(str(path))
            observations: list[tuple[int, int]] = []

            find_perceptual_duplicate_groups_with_stats(
                paths,
                hamming_threshold=0,
                progress_callback=lambda current, total: observations.append((current, total)),
            )

        self.assertEqual((5, 5), observations[-1])
        self.assertEqual([1, 2, 3, 4, 5], [current for current, _total in observations])

    def test_runner_writes_report_from_signal_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = build_dino_prefilter_paths(temp_dir)
            decisions = run_dino_prefilter_from_signal_rows(
                (
                    {
                        "file_path": str(Path(temp_dir) / "tail.jpg"),
                        "group_size": "4",
                        "dino_rank": "4",
                        "detail": "0.90",
                        "exposure_status": "properly_exposed",
                        "exposure_score": "1.0",
                    },
                    {
                        "file_path": str(Path(temp_dir) / "good.jpg"),
                        "group_size": "1",
                        "dino_rank": "1",
                        "detail": "0.95",
                        "exposure_status": "properly_exposed",
                        "exposure_score": "1.0",
                    },
                ),
                settings=DINOPrefilterSettings(enabled=True, aggressiveness_percent=85),
                paths=paths,
            )

            self.assertEqual("remove_from_pool", decisions[str(Path(temp_dir) / "tail.jpg")].action)
            self.assertEqual("pass", decisions[str(Path(temp_dir) / "good.jpg")].action)
            report = json.loads(paths.report_path.read_text(encoding="utf-8"))
            self.assertEqual(2, report["counts"]["scanned"])
            self.assertEqual(1, report["counts"]["removed_from_pool"])
            self.assertTrue(paths.log_path.exists())


if __name__ == "__main__":
    unittest.main()
