from __future__ import annotations

import csv
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from uuid import uuid4

import numpy as np

from image_triage.ai_training import (
    LaunchLabelingAppTask,
    ai_training_evaluation_issues,
    ai_training_source_needs_prepare,
    build_ai_training_paths,
    build_labeling_command,
    build_general_ai_training_paths,
    diagnose_ranker_fit,
    list_ranker_runs,
    list_registered_training_sources,
    normalize_ranker_profile,
    prepare_hidden_ai_training_workspace,
    prepare_general_training_pool,
    preview_general_training_pool,
    set_active_ranker_selection,
    set_registered_training_source_enabled,
    suggest_training_profile,
    _write_labeled_training_clusters,
    _write_labeled_training_include_file,
)
from image_triage.ai_workflow import AIWorkflowRuntime
from image_triage.metadata import CaptureMetadata
from image_triage.models import ImageRecord
from image_triage.ai_training import RankerFitDiagnosis, RankerRunInfo


class AITrainingTests(unittest.TestCase):
    def _record(self, path: str) -> ImageRecord:
        return ImageRecord(path=path, name=Path(path).name, size=1000, modified_ns=1)

    def _write_training_source(self, folder: Path, *, seed: float, pairwise_repetitions: int = 1) -> None:
        paths = build_ai_training_paths(folder)
        paths.artifacts_dir.mkdir(parents=True, exist_ok=True)
        paths.labels_dir.mkdir(parents=True, exist_ok=True)
        image_ids = ["shared_a", "shared_b"]
        metadata_rows = [
            {
                "image_id": image_ids[0],
                "file_path": str((folder / "a.jpg").resolve()),
                "relative_path": "a.jpg",
                "file_name": "a.jpg",
                "embedding_index": "0",
                "capture_timestamp": "",
                "capture_time_source": "missing",
            },
            {
                "image_id": image_ids[1],
                "file_path": str((folder / "b.jpg").resolve()),
                "relative_path": "b.jpg",
                "file_name": "b.jpg",
                "embedding_index": "1",
                "capture_timestamp": "",
                "capture_time_source": "missing",
            },
        ]
        with (paths.artifacts_dir / "images.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(metadata_rows[0].keys()))
            writer.writeheader()
            writer.writerows(metadata_rows)
        np.save(paths.artifacts_dir / "embeddings.npy", np.array([[seed, seed + 1.0], [seed + 2.0, seed + 3.0]], dtype=np.float32))
        (paths.artifacts_dir / "image_ids.json").write_text(json.dumps(image_ids, indent=2), encoding="utf-8")
        cluster_rows = [
            {
                "image_id": image_ids[0],
                "cluster_id": "cluster_0000",
                "cluster_size": "2",
                "cluster_position": "0",
                "time_window_id": "cluster_0000",
                "window_kind": "burst",
                "cluster_reason": "unit_test",
                "capture_timestamp": "",
                "capture_time_source": "missing",
                "file_path": str((folder / "a.jpg").resolve()),
                "relative_path": "a.jpg",
                "file_name": "a.jpg",
            },
            {
                "image_id": image_ids[1],
                "cluster_id": "cluster_0000",
                "cluster_size": "2",
                "cluster_position": "1",
                "time_window_id": "cluster_0000",
                "window_kind": "burst",
                "cluster_reason": "unit_test",
                "capture_timestamp": "",
                "capture_time_source": "missing",
                "file_path": str((folder / "b.jpg").resolve()),
                "relative_path": "b.jpg",
                "file_name": "b.jpg",
            },
        ]
        with (paths.artifacts_dir / "clusters.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(cluster_rows[0].keys()))
            writer.writeheader()
            writer.writerows(cluster_rows)
        with paths.pairwise_labels_path.open("w", encoding="utf-8") as handle:
            for _index in range(pairwise_repetitions):
                handle.write(
                    json.dumps(
                        {
                            "label_id": str(uuid4()),
                            "image_a_id": image_ids[0],
                            "image_b_id": image_ids[1],
                            "preferred_image_id": image_ids[0],
                            "decision": "left_better",
                            "source_mode": "manual",
                            "cluster_id": "cluster_0000",
                        }
                    )
                    + "\n"
                )
        with paths.cluster_labels_path.open("w", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "cluster_id": "cluster_0000",
                        "best_image_ids": [image_ids[0]],
                        "acceptable_image_ids": [],
                        "reject_image_ids": [image_ids[1]],
                    }
                )
                + "\n"
            )

    def test_normalize_ranker_profile_accepts_labels_and_defaults(self) -> None:
        self.assertEqual(("portrait", "Portrait"), normalize_ranker_profile("Portrait"))
        self.assertEqual(("general", "General Use"), normalize_ranker_profile("unknown-profile"))

    def test_set_active_ranker_selection_persists_profile_metadata(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_training_") as temp_dir:
            folder = Path(temp_dir) / "shots"
            folder.mkdir(parents=True, exist_ok=True)
            paths = build_ai_training_paths(folder)
            checkpoint_path = paths.training_runs_dir / "portrait-run" / "best_ranker.pt"
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            checkpoint_path.write_bytes(b"checkpoint")

            set_active_ranker_selection(
                paths,
                checkpoint_path=checkpoint_path,
                run_id="portrait-run",
                display_name="Portrait Run",
                profile_key="portrait",
            )

            payload = json.loads(paths.active_ranker_path.read_text(encoding="utf-8"))
            self.assertEqual("portrait-run", payload["run_id"])
            self.assertEqual("Portrait Run", payload["display_name"])
            self.assertEqual("portrait", payload["profile_key"])
            self.assertEqual("Portrait", payload["profile_label"])

    def test_diagnose_ranker_fit_detects_overfit(self) -> None:
        diagnosis = diagnose_ranker_fit(
            metrics={
                "best_epoch": 2,
                "best_validation_pairwise_accuracy": 0.91,
                "best_validation_loss": 0.34,
                "final_train_loss": 0.11,
                "final_validation_loss": 0.51,
                "final_validation_pairwise_accuracy": 0.84,
            },
            num_epochs=10,
        )

        self.assertEqual("overfit", diagnosis.code)
        self.assertEqual("May Be Overfit", diagnosis.label)

    def test_diagnose_ranker_fit_detects_underfit(self) -> None:
        diagnosis = diagnose_ranker_fit(
            metrics={
                "best_epoch": 20,
                "best_validation_pairwise_accuracy": 0.64,
                "best_validation_loss": 0.48,
                "final_train_loss": 0.43,
                "final_validation_loss": 0.49,
                "final_validation_pairwise_accuracy": 0.63,
            },
            num_epochs=20,
        )

        self.assertEqual("underfit", diagnosis.code)
        self.assertEqual("May Be Underfit", diagnosis.label)

    def test_list_ranker_runs_loads_profile_and_fit_diagnosis(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_training_") as temp_dir:
            folder = Path(temp_dir) / "shots"
            folder.mkdir(parents=True, exist_ok=True)
            paths = build_ai_training_paths(folder)
            run_dir = paths.training_runs_dir / "portrait-run"
            run_dir.mkdir(parents=True, exist_ok=True)
            (run_dir / "best_ranker.pt").write_bytes(b"checkpoint")
            (run_dir / "ranker_run.json").write_text(
                json.dumps(
                    {
                        "run_id": "portrait-run",
                        "display_name": "Portrait Run",
                        "created_at": "2026-04-24T12:00:00-06:00",
                        "pairwise_labels": 120,
                        "cluster_labels": 18,
                        "profile_key": "portrait",
                        "profile_label": "Portrait",
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            (run_dir / "training_metrics.json").write_text(
                json.dumps(
                    {
                        "best_epoch": 2,
                        "best_validation_pairwise_accuracy": 0.91,
                        "best_validation_loss": 0.34,
                        "final_train_loss": 0.11,
                        "final_validation_loss": 0.51,
                        "final_validation_pairwise_accuracy": 0.84,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            (run_dir / "resolved_config.json").write_text(
                json.dumps({"num_epochs": 10}, indent=2),
                encoding="utf-8",
            )

            runs = list_ranker_runs(paths)

            self.assertEqual(1, len(runs))
            self.assertEqual("Portrait", runs[0].profile_label)
            self.assertEqual("overfit", runs[0].fit_diagnosis.code)

    def test_suggest_training_profile_prefers_astro_for_fits(self) -> None:
        records = [self._record(f"C:/astro/frame_{index:04d}.fits") for index in range(12)]

        suggestion = suggest_training_profile(records)

        self.assertEqual("astro", suggestion.profile_key)

    def test_suggest_training_profile_prefers_portrait_when_metadata_is_clear(self) -> None:
        records = [self._record(f"C:/portraits/img_{index:02d}.jpg") for index in range(10)]
        metadata_map = {
            record.path: CaptureMetadata(
                path=record.path,
                width=3000,
                height=4500,
                focal_length_value=85.0,
                aperture_value=2.0,
                orientation="Portrait",
            )
            for record in records
        }

        suggestion = suggest_training_profile(records, metadata_loader=lambda path: metadata_map[path])

        self.assertEqual("portrait", suggestion.profile_key)
        self.assertEqual("High", suggestion.confidence_label)

    def test_suggest_training_profile_keeps_general_when_unclear(self) -> None:
        records = [self._record(f"C:/mixed/img_{index:02d}.jpg") for index in range(8)]
        metadata_rows = [
            CaptureMetadata(path=records[0].path, width=4000, height=3000, focal_length_value=24.0, orientation="Landscape"),
            CaptureMetadata(path=records[1].path, width=3000, height=4500, focal_length_value=85.0, orientation="Portrait"),
            CaptureMetadata(path=records[2].path, width=4000, height=3000, focal_length_value=300.0, exposure_seconds=1 / 2000.0, orientation="Landscape"),
            CaptureMetadata(path=records[3].path, width=4000, height=3000, focal_length_value=50.0, iso_value=3200.0, orientation="Landscape"),
            CaptureMetadata(path=records[4].path, width=3000, height=4500, focal_length_value=35.0, orientation="Portrait"),
            CaptureMetadata(path=records[5].path, width=4000, height=3000, focal_length_value=105.0, aperture_value=5.6, orientation="Landscape"),
            CaptureMetadata(path=records[6].path, width=4000, height=3000, focal_length_value=16.0, orientation="Landscape"),
            CaptureMetadata(path=records[7].path, width=3000, height=4500, focal_length_value=135.0, aperture_value=4.0, orientation="Portrait"),
        ]
        metadata_map = {record.path: metadata for record, metadata in zip(records, metadata_rows)}

        suggestion = suggest_training_profile(records, metadata_loader=lambda path: metadata_map[path])

        self.assertEqual("general", suggestion.profile_key)
        self.assertTrue(suggestion.is_mixed)

    def test_suggest_training_profile_uses_keywords_for_wildlife(self) -> None:
        records = [self._record(f"C:/Trips/Yellowstone/Wildlife/eagle_bird_{index:02d}.jpg") for index in range(6)]
        metadata_map = {
            record.path: CaptureMetadata(
                path=record.path,
                width=5000,
                height=3333,
                focal_length_value=400.0,
                exposure_seconds=1 / 2500.0,
                orientation="Landscape",
            )
            for record in records
        }

        suggestion = suggest_training_profile(records, metadata_loader=lambda path: metadata_map[path])

        self.assertEqual("wildlife", suggestion.profile_key)
        self.assertIn("confidence", suggestion.reason.casefold())

    def test_prepare_general_training_pool_merges_folder_local_sources(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_general_training_") as temp_dir:
            with mock.patch.dict(os.environ, {"IMAGE_TRIAGE_APPDATA": temp_dir}, clear=False):
                folder_a = Path(temp_dir) / "folder_a"
                folder_b = Path(temp_dir) / "folder_b"
                folder_a.mkdir(parents=True, exist_ok=True)
                folder_b.mkdir(parents=True, exist_ok=True)
                self._write_training_source(folder_a, seed=1.0)
                self._write_training_source(folder_b, seed=10.0)

                status = prepare_general_training_pool((str(folder_a), str(folder_b)))
                general_paths = build_general_ai_training_paths()

                self.assertEqual(2, status.source_folders)
                self.assertEqual(2, status.pairwise_labels)
                self.assertEqual(2, status.cluster_labels)
                self.assertTrue((general_paths.artifacts_dir / "embeddings.npy").exists())

                image_ids = json.loads((general_paths.artifacts_dir / "image_ids.json").read_text(encoding="utf-8"))
                self.assertEqual(4, len(image_ids))
                self.assertEqual(4, len(set(image_ids)))

                with (general_paths.labels_dir / "pairwise_labels.jsonl").open("r", encoding="utf-8") as handle:
                    pairwise_records = [json.loads(line) for line in handle if line.strip()]
                self.assertEqual(2, len(pairwise_records))
                for record in pairwise_records:
                    self.assertIn(record["image_a_id"], image_ids)
                    self.assertIn(record["image_b_id"], image_ids)
                    self.assertIn(record["preferred_image_id"], image_ids)
                    self.assertNotEqual("cluster_0000", record["cluster_id"])

    def test_labeled_training_include_file_uses_only_labeled_images(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_labeled_include_") as temp_dir:
            with mock.patch.dict(os.environ, {"IMAGE_TRIAGE_APPDATA": temp_dir}, clear=False):
                folder = Path(temp_dir) / "shots"
                folder.mkdir(parents=True, exist_ok=True)
                paths = build_ai_training_paths(folder)
                paths.labeling_artifacts_dir.mkdir(parents=True, exist_ok=True)
                paths.labels_dir.mkdir(parents=True, exist_ok=True)
                metadata_rows = [
                    {"image_id": "img_a", "relative_path": "a.jpg"},
                    {"image_id": "img_b", "relative_path": "nested/b.jpg"},
                    {"image_id": "img_unused", "relative_path": "unused.jpg"},
                ]
                with paths.labeling_metadata_path.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=["image_id", "relative_path"])
                    writer.writeheader()
                    writer.writerows(metadata_rows)
                with paths.pairwise_labels_path.open("w", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(
                            {
                                "image_a_id": "img_a",
                                "image_b_id": "img_b",
                                "preferred_image_id": "img_a",
                            }
                        )
                        + "\n"
                    )

                include_file, image_ids = _write_labeled_training_include_file(paths)

                self.assertIsNotNone(include_file)
                assert include_file is not None
                self.assertEqual({"img_a", "img_b"}, image_ids)
                self.assertEqual(["a.jpg", "nested/b.jpg"], include_file.read_text(encoding="utf-8").splitlines())

    def test_labeled_training_clusters_preserve_label_cluster_ids(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_labeled_clusters_") as temp_dir:
            with mock.patch.dict(os.environ, {"IMAGE_TRIAGE_APPDATA": temp_dir}, clear=False):
                folder = Path(temp_dir) / "shots"
                folder.mkdir(parents=True, exist_ok=True)
                paths = build_ai_training_paths(folder)
                paths.artifacts_dir.mkdir(parents=True, exist_ok=True)
                paths.labeling_artifacts_dir.mkdir(parents=True, exist_ok=True)
                metadata_rows = [
                    {
                        "image_id": "img_a",
                        "file_path": str(folder / "a.jpg"),
                        "relative_path": "a.jpg",
                        "file_name": "a.jpg",
                        "embedding_index": "0",
                    },
                    {
                        "image_id": "img_b",
                        "file_path": str(folder / "b.jpg"),
                        "relative_path": "b.jpg",
                        "file_name": "b.jpg",
                        "embedding_index": "1",
                    },
                ]
                with (paths.artifacts_dir / "images.csv").open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=list(metadata_rows[0].keys()))
                    writer.writeheader()
                    writer.writerows(metadata_rows)
                cluster_rows = [
                    {
                        "image_id": "img_a",
                        "cluster_id": "label_cluster_0007",
                        "cluster_size": "4",
                        "cluster_position": "2",
                        "cluster_reason": "label_candidates_burst",
                        "file_path": str(folder / "a.jpg"),
                        "relative_path": "a.jpg",
                        "file_name": "a.jpg",
                    },
                    {
                        "image_id": "img_b",
                        "cluster_id": "label_cluster_0007",
                        "cluster_size": "4",
                        "cluster_position": "3",
                        "cluster_reason": "label_candidates_burst",
                        "file_path": str(folder / "b.jpg"),
                        "relative_path": "b.jpg",
                        "file_name": "b.jpg",
                    },
                ]
                with paths.labeling_clusters_path.open("w", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=list(cluster_rows[0].keys()))
                    writer.writeheader()
                    writer.writerows(cluster_rows)

                _write_labeled_training_clusters(paths, {"img_a", "img_b"})

                with (paths.artifacts_dir / "clusters.csv").open("r", encoding="utf-8", newline="") as handle:
                    output_rows = list(csv.DictReader(handle))
                self.assertEqual(["label_cluster_0007", "label_cluster_0007"], [row["cluster_id"] for row in output_rows])
                self.assertEqual(["2", "2"], [row["cluster_size"] for row in output_rows])
                self.assertEqual(["0", "1"], [row["cluster_position"] for row in output_rows])

    def test_stale_labeled_cluster_artifacts_block_evaluation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_stale_cluster_eval_") as temp_dir:
            with mock.patch.dict(os.environ, {"IMAGE_TRIAGE_APPDATA": temp_dir}, clear=False):
                folder = Path(temp_dir) / "shots"
                folder.mkdir(parents=True, exist_ok=True)
                self._write_training_source(folder, seed=1.0)
                paths = build_ai_training_paths(folder)
                paths.cluster_labels_path.write_text(
                    json.dumps(
                        {
                            "cluster_id": "label_cluster_0009",
                            "best_image_ids": ["shared_a"],
                            "acceptable_image_ids": [],
                            "reject_image_ids": ["shared_b"],
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )

                issues = ai_training_evaluation_issues(paths)

                self.assertTrue(ai_training_source_needs_prepare(folder))
                self.assertTrue(any("labeled cluster" in issue for issue in issues))
                self.assertTrue(any("Run Prepare Training Data" in issue for issue in issues))

    def test_registered_training_source_enabled_state_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_sources_") as temp_dir:
            with mock.patch.dict(os.environ, {"IMAGE_TRIAGE_APPDATA": temp_dir}, clear=False):
                folder = Path(temp_dir) / "shots"
                folder.mkdir(parents=True, exist_ok=True)

                prepare_hidden_ai_training_workspace(folder)
                sources = list_registered_training_sources(enabled_only=False)
                self.assertEqual(1, len(sources))
                self.assertTrue(sources[0].enabled)

                set_registered_training_source_enabled(sources[0].namespace, False)

                self.assertEqual((), list_registered_training_sources(enabled_only=True))
                all_sources = list_registered_training_sources(enabled_only=False)
                self.assertEqual(1, len(all_sources))
                self.assertFalse(all_sources[0].enabled)

    def test_preview_general_training_pool_reports_retrain_guidance(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_general_preview_") as temp_dir:
            with mock.patch.dict(os.environ, {"IMAGE_TRIAGE_APPDATA": temp_dir}, clear=False):
                folder = Path(temp_dir) / "folder_a"
                folder.mkdir(parents=True, exist_ok=True)
                self._write_training_source(folder, seed=2.0, pairwise_repetitions=30)

                reference_run = RankerRunInfo(
                    run_id="general-run",
                    display_name="General Run",
                    run_dir=Path(temp_dir) / "run",
                    checkpoint_path=None,
                    last_checkpoint_path=None,
                    metrics_path=None,
                    history_path=None,
                    resolved_config_path=None,
                    evaluation_metrics_path=None,
                    train_log_path=None,
                    evaluation_log_path=None,
                    created_at="2026-04-25T12:00:00-06:00",
                    pairwise_labels=1,
                    cluster_labels=0,
                    num_epochs=None,
                    best_epoch=None,
                    best_validation_accuracy=None,
                    best_validation_loss=None,
                    cluster_top1_hit_rate=None,
                    reference_bank_path="",
                    profile_key="general",
                    profile_label="General Use",
                    fit_diagnosis=RankerFitDiagnosis(
                        code="healthy",
                        label="Looks Healthy",
                        summary="Balanced.",
                        remedy="Keep going.",
                    ),
                    is_active=True,
                    is_legacy=False,
                )

                status = preview_general_training_pool((str(folder),), reference_run=reference_run)

            self.assertEqual(30, status.pairwise_labels)
            self.assertEqual(1, status.cluster_labels)
            self.assertGreaterEqual(status.labels_added_since_train, 30)
            self.assertTrue(status.needs_retrain)
            self.assertIn("Retraining is recommended", status.guidance_text)

    def test_build_labeling_command_passes_near_identical_threshold(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_label_command_") as temp_dir:
            root = Path(temp_dir)
            previous_appdata = os.environ.get("IMAGE_TRIAGE_APPDATA")
            os.environ["IMAGE_TRIAGE_APPDATA"] = str(root / "appdata")
            engine_root = root / "engine"
            (engine_root / "configs").mkdir(parents=True)
            (engine_root / "configs" / "labeling_app.json").write_text("{}", encoding="utf-8")
            python_executable = root / "python.exe"
            python_executable.write_text("", encoding="utf-8")
            folder = root / "photos"
            folder.mkdir()
            runtime = AIWorkflowRuntime(
                engine_root=engine_root,
                python_executable=python_executable,
                model_name="model",
                checkpoint_path=root / "checkpoint.pt",
                extraction_config_path=root / "extract.json",
                clustering_config_path=root / "cluster.json",
                report_config_path=root / "report.json",
            )

            try:
                command = build_labeling_command(runtime, folder=folder, near_identical_threshold=0.94)
            finally:
                if previous_appdata is None:
                    os.environ.pop("IMAGE_TRIAGE_APPDATA", None)
                else:
                    os.environ["IMAGE_TRIAGE_APPDATA"] = previous_appdata

            self.assertIn("--near-identical-threshold", command)
            flag_index = command.index("--near-identical-threshold")
            self.assertEqual("0.940", command[flag_index + 1])

    def test_launch_labeling_app_task_waits_for_ready_signal(self) -> None:
        class _FakeProcess:
            def __init__(self) -> None:
                self.pid = 4321

            def poll(self):
                return None

        with tempfile.TemporaryDirectory(prefix="image_triage_label_launch_") as temp_dir:
            task = LaunchLabelingAppTask(
                folder=Path(temp_dir),
                runtime=object(),  # type: ignore[arg-type]
                annotator_id="LinkFlow",
                artifacts_dir=Path(temp_dir) / "artifacts",
            )
            finished_payloads: list[object] = []
            failures: list[str] = []
            task.signals.finished.connect(lambda payload: finished_payloads.append(payload))
            task.signals.failed.connect(lambda message: failures.append(message))

            def _fake_launch(*_args, ready_file_path=None, **_kwargs):
                assert ready_file_path is not None
                Path(ready_file_path).write_text("ready", encoding="utf-8")
                return _FakeProcess()

            with mock.patch("image_triage.ai_training.launch_labeling_app", side_effect=_fake_launch):
                task.run()

            self.assertEqual([], failures)
            self.assertEqual(1, len(finished_payloads))
            payload = finished_payloads[0]
            self.assertIsInstance(payload, dict)
            assert isinstance(payload, dict)
            self.assertTrue(payload["ready_acknowledged"])
            self.assertEqual(4321, payload["pid"])

    def test_launch_labeling_app_task_reports_background_start_when_ready_signal_times_out(self) -> None:
        class _FakeProcess:
            def __init__(self) -> None:
                self.pid = 9876

            def poll(self):
                return None

        with tempfile.TemporaryDirectory(prefix="image_triage_label_launch_") as temp_dir:
            task = LaunchLabelingAppTask(
                folder=Path(temp_dir),
                runtime=object(),  # type: ignore[arg-type]
                annotator_id="LinkFlow",
                artifacts_dir=Path(temp_dir) / "artifacts",
            )
            finished_payloads: list[object] = []
            failures: list[str] = []
            task.signals.finished.connect(lambda payload: finished_payloads.append(payload))
            task.signals.failed.connect(lambda message: failures.append(message))

            with mock.patch("image_triage.ai_training.launch_labeling_app", return_value=_FakeProcess()), mock.patch(
                "image_triage.ai_training.LABELING_READY_WAIT_TIMEOUT_SECONDS",
                0.0,
            ):
                task.run()

            self.assertEqual([], failures)
            self.assertEqual(1, len(finished_payloads))
            payload = finished_payloads[0]
            self.assertIsInstance(payload, dict)
            assert isinstance(payload, dict)
            self.assertFalse(payload["ready_acknowledged"])
            self.assertEqual(9876, payload["pid"])

    def test_launch_labeling_app_task_reports_startup_error_details(self) -> None:
        class _FakeProcess:
            def __init__(self) -> None:
                self.pid = 2468

            def poll(self):
                return None

        with tempfile.TemporaryDirectory(prefix="image_triage_label_launch_") as temp_dir:
            task = LaunchLabelingAppTask(
                folder=Path(temp_dir),
                runtime=object(),  # type: ignore[arg-type]
                annotator_id="LinkFlow",
                artifacts_dir=Path(temp_dir) / "artifacts",
            )
            finished_payloads: list[object] = []
            failures: list[str] = []
            task.signals.finished.connect(lambda payload: finished_payloads.append(payload))
            task.signals.failed.connect(lambda message: failures.append(message))

            def _fake_launch(*_args, ready_file_path=None, **_kwargs):
                assert ready_file_path is not None
                Path(ready_file_path).write_text(
                    json.dumps(
                        {
                            "state": "error",
                            "message": "Metadata file not found",
                            "details": "Traceback\\nFileNotFoundError: Metadata file not found",
                        }
                    ),
                    encoding="utf-8",
                )
                return _FakeProcess()

            with mock.patch("image_triage.ai_training.launch_labeling_app", side_effect=_fake_launch):
                task.run()

            self.assertEqual([], finished_payloads)
            self.assertEqual(1, len(failures))
            self.assertIn("Metadata file not found", failures[0])

    def test_labeling_ui_ready_handshake_writes_ready_state(self) -> None:
        module_path = Path("AICullingPipeline/app/labeling/ui.py").resolve()
        app_root = str(Path("AICullingPipeline").resolve())
        module_name = "image_triage_labeling_ui_ready_test"
        ready_path = None
        sys_path_inserted = False
        try:
            if app_root not in sys.path:
                sys.path.insert(0, app_root)
                sys_path_inserted = True
            spec = importlib.util.spec_from_file_location(module_name, module_path)
            self.assertIsNotNone(spec)
            assert spec is not None
            module = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(module)

            with tempfile.TemporaryDirectory(prefix="image_triage_label_ready_") as temp_dir:
                ready_path = Path(temp_dir) / "labeling_ready.json"
                with mock.patch.dict(os.environ, {"IMAGE_TRIAGE_LABELING_READY_FILE": str(ready_path)}, clear=False):
                    module._notify_host_ready()

                payload = json.loads(ready_path.read_text(encoding="utf-8"))
                self.assertEqual("ready", payload["state"])
        finally:
            sys.modules.pop(module_name, None)
            if sys_path_inserted:
                sys.path.remove(app_root)


if __name__ == "__main__":
    unittest.main()
