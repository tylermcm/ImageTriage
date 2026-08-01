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
