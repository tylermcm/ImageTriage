from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

import numpy as np
from PIL import Image
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QImage, QMouseEvent, QPainter
from PySide6.QtWidgets import QApplication

from image_triage.ai_model import resolve_birefnet_model_installation
from image_triage.birefnet_worker import _birefnet_rearrange, _emit_metric
import image_triage.subject_masks as subject_masks
from image_triage.semantic_masks import SemanticCategoryPresence, SemanticMaskResult
from image_triage.subject_masks import (
    SubjectMaskComponent,
    SubjectMaskResult,
    combine_subject_components,
    ensure_subject_masks,
)
from image_triage.ui.photo_editor_panel import PhotoEditorPanel


class SubjectMaskCacheTests(unittest.TestCase):
    def test_checkpoint_hash_cache_reuses_and_invalidates_by_file_identity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_subject_hash_") as temp_dir:
            checkpoint = Path(temp_dir) / "model.safetensors"
            checkpoint.write_bytes(b"first")
            subject_masks._WEIGHTS_HASH_CACHE.clear()

            first, first_hit = subject_masks._cached_sha256_file(checkpoint)
            second, second_hit = subject_masks._cached_sha256_file(checkpoint)
            checkpoint.write_bytes(b"second-version")
            third, third_hit = subject_masks._cached_sha256_file(checkpoint)

            self.assertFalse(first_hit)
            self.assertTrue(second_hit)
            self.assertEqual(first, second)
            self.assertFalse(third_hit)
            self.assertNotEqual(first, third)

    def test_persistent_worker_reuses_imports_model_and_process(self) -> None:
        commands: list[str] = []

        class FakeStdout:
            def __init__(self) -> None:
                self.lines: list[str] = []

            def readline(self) -> str:
                return self.lines.pop(0) if self.lines else ""

            def close(self) -> None:
                pass

        class FakeProcess:
            pid = 4242

            def __init__(self) -> None:
                self.stdout = FakeStdout()
                self.stopped = False
                self.stdin = FakeStdin(self)

            def poll(self):
                return 0 if self.stopped else None

            def wait(self, timeout=None):
                self.stopped = True
                return 0

            def terminate(self) -> None:
                self.stopped = True

            def kill(self) -> None:
                self.stopped = True

        class FakeStdin:
            def __init__(self, process: FakeProcess) -> None:
                self.process = process

            def write(self, raw_request: str) -> int:
                request = json.loads(raw_request)
                command = str(request["command"])
                commands.append(command)
                result: dict[str, object] = {"device": "cuda", "stage": command}
                if command == "infer":
                    result["components"] = [{"id": "subject-01"}]
                response = {
                    "id": request["id"],
                    "ok": True,
                    "result": result,
                    "error": "",
                }
                self.process.stdout.lines.append("RESPONSE " + json.dumps(response) + "\n")
                return len(raw_request)

            def flush(self) -> None:
                pass

            def close(self) -> None:
                pass

        service = subject_masks.BiRefNetWorkerService(idle_timeout_seconds=3600)
        fake_process = FakeProcess()
        service._process = fake_process  # type: ignore[assignment]
        model_dir = Path("model").resolve()
        service.warm_imports()
        service.warm_imports()
        service.warm_model(model_dir)
        service.warm_model(model_dir)
        for index in range(2):
            device, components = service.infer(
                model_dir=model_dir,
                input_path=Path(f"input-{index}.png"),
                output_path=Path(f"output-{index}.png"),
                components_dir=Path(f"components-{index}"),
                progress_callback=None,
            )
            self.assertEqual("cuda", device)
            self.assertEqual([{"id": "subject-01"}], components)
        service.shutdown()

        self.assertEqual(
            ["warm-imports", "load-model", "infer", "infer", "shutdown"],
            commands,
        )

    def test_worker_metric_is_structured_and_host_records_its_duration(self) -> None:
        previous = os.environ.get("IMAGE_TRIAGE_AI_METRICS")
        os.environ["IMAGE_TRIAGE_AI_METRICS"] = "1"
        output = io.StringIO()
        try:
            with redirect_stdout(output):
                _emit_metric(
                    "ai.mask.birefnet.worker.inference",
                    time.perf_counter() - 0.01,
                    device="cpu",
                )
        finally:
            if previous is None:
                os.environ.pop("IMAGE_TRIAGE_AI_METRICS", None)
            else:
                os.environ["IMAGE_TRIAGE_AI_METRICS"] = previous

        line = output.getvalue().strip()
        self.assertTrue(line.startswith("AI_METRIC "))
        payload = json.loads(line.removeprefix("AI_METRIC "))
        self.assertEqual("ai.mask.birefnet.worker.inference", payload["event"])
        self.assertGreater(payload["duration_ms"], 0)

        class RecordingLogger:
            enabled = True

            def __init__(self) -> None:
                self.events: list[tuple[str, float, dict[str, object]]] = []

            def duration(self, event: str, duration_ms: float, **fields: object) -> None:
                self.events.append((event, duration_ms, fields))

        logger = RecordingLogger()
        original_perf_logger = subject_masks.perf_logger
        subject_masks.perf_logger = lambda: logger
        try:
            parsed = subject_masks._parse_worker_metric(line)
            self.assertIsNotNone(parsed)
            subject_masks._record_worker_metric(parsed or {})
        finally:
            subject_masks.perf_logger = original_perf_logger

        self.assertEqual("ai.mask.birefnet.worker.inference", logger.events[0][0])
        self.assertEqual("worker", logger.events[0][2]["source"])
        self.assertEqual("cpu", logger.events[0][2]["device"])

    def test_birefnet_rearrange_compatibility_covers_checkpoint_patterns(self) -> None:
        class ArrayTensor:
            def __init__(self, values: np.ndarray) -> None:
                self.values = values

            @property
            def shape(self):
                return self.values.shape

            def reshape(self, *shape):
                return ArrayTensor(self.values.reshape(*shape))

            def permute(self, *axes):
                return ArrayTensor(self.values.transpose(*axes))

        source = ArrayTensor(np.arange(16).reshape(1, 1, 4, 4))
        flat = _birefnet_rearrange(
            source,
            "b c (hg h) (wg w) -> (b hg wg) c h w",
            hg=2,
            wg=2,
        )
        channel_packed = _birefnet_rearrange(
            source,
            "b c (hg h) (wg w) -> b (c hg wg) h w",
            hg=2,
            wg=2,
        )
        restored = _birefnet_rearrange(
            flat,
            "(b hg wg) c h w -> b c (hg h) (wg w)",
            hg=2,
            wg=2,
        )

        self.assertEqual((4, 1, 2, 2), flat.shape)
        self.assertEqual((1, 4, 2, 2), channel_packed.shape)
        np.testing.assert_array_equal(source.values, restored.values)

    def test_missing_runtime_stops_before_model_download(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_subject_runtime_") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.jpg"
            Image.new("RGB", (12, 8), (20, 80, 140)).save(source_path)
            installation = resolve_birefnet_model_installation(
                install_dir=root / "missing-model"
            )
            calls = {"download": 0}

            def reject_runtime() -> None:
                raise RuntimeError("runtime unavailable")

            def fake_download(*_args, **_kwargs) -> None:
                calls["download"] += 1

            original_validate_runtime = subject_masks._validate_subject_runtime
            original_download = subject_masks.download_birefnet_model
            subject_masks._validate_subject_runtime = reject_runtime
            subject_masks.download_birefnet_model = fake_download
            try:
                with self.assertRaisesRegex(RuntimeError, "runtime unavailable"):
                    ensure_subject_masks(source_path, installation=installation)
            finally:
                subject_masks._validate_subject_runtime = original_validate_runtime
                subject_masks.download_birefnet_model = original_download

            self.assertEqual(0, calls["download"])

    def test_one_inference_populates_subject_and_background_then_hits_cache(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_subject_cache_") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.jpg"
            Image.new("RGB", (12, 8), (20, 80, 140)).save(source_path)
            installation = resolve_birefnet_model_installation(
                install_dir=root / "model"
            )
            installation.install_dir.mkdir(parents=True)
            for filename in installation.required_filenames:
                path = installation.install_dir / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"weights" if filename == "model.safetensors" else b"stub")

            calls = {"worker": 0}

            def fake_worker(
                *, output_path: Path, components_dir: Path, **_kwargs
            ) -> tuple[str, list[dict[str, object]]]:
                calls["worker"] += 1
                values = np.arange(96, dtype=np.uint8).reshape(8, 12)
                Image.fromarray(values, mode="L").save(output_path)
                components_dir.mkdir(parents=True, exist_ok=True)
                component_path = components_dir / "subject-01.png"
                Image.fromarray(values, mode="L").save(component_path)
                return "cuda", [
                    {
                        "id": "subject-01",
                        "path": component_path.name,
                        "bbox": [0, 0, 12, 8],
                        "centroid": [5.5, 3.5],
                        "areaFraction": 1.0,
                    }
                ]

            original_decode = subject_masks._decode_rgb_preview
            original_worker = subject_masks._run_subject_worker
            original_validate_runtime = subject_masks._validate_subject_runtime
            subject_masks._decode_rgb_preview = lambda *_args, **_kwargs: np.zeros(
                (8, 12, 3), dtype=np.uint8
            )
            subject_masks._run_subject_worker = fake_worker
            subject_masks._validate_subject_runtime = lambda: None
            try:
                first = ensure_subject_masks(
                    source_path,
                    installation=installation,
                    cache_root=root / "cache",
                )
                second = ensure_subject_masks(
                    source_path,
                    installation=installation,
                    cache_root=root / "cache",
                )
            finally:
                subject_masks._decode_rgb_preview = original_decode
                subject_masks._run_subject_worker = original_worker
                subject_masks._validate_subject_runtime = original_validate_runtime

            self.assertFalse(first.cache_hit)
            self.assertTrue(second.cache_hit)
            self.assertEqual(1, calls["worker"])
            self.assertEqual("cuda", first.runtime_device)
            self.assertEqual(1, len(first.components))
            self.assertEqual(first.components, second.components)
            with Image.open(first.mask_paths["subject"]) as subject:
                subject_values = np.asarray(subject.convert("L"), dtype=np.uint8)
            with Image.open(first.mask_paths["background"]) as background:
                background_values = np.asarray(background.convert("L"), dtype=np.uint8)
            np.testing.assert_array_equal(255 - subject_values, background_values)

    def test_selected_subject_components_combine_with_maximum_strength(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_subject_merge_") as temp_dir:
            root = Path(temp_dir)
            left_path = root / "left.png"
            right_path = root / "right.png"
            center_path = root / "center.png"
            Image.fromarray(
                np.asarray([[200, 0], [100, 0]], dtype=np.uint8), mode="L"
            ).save(left_path)
            Image.fromarray(
                np.asarray([[0, 220], [0, 80]], dtype=np.uint8), mode="L"
            ).save(right_path)
            Image.fromarray(
                np.asarray([[0, 0], [40, 0]], dtype=np.uint8), mode="L"
            ).save(center_path)
            source_path = root / "source.jpg"
            subject_path = root / "subject.png"
            background_path = root / "background.png"
            Image.new("RGB", (2, 2)).save(source_path)
            Image.new("L", (2, 2), 255).save(subject_path)
            Image.new("L", (2, 2), 0).save(background_path)
            components = (
                SubjectMaskComponent("subject-01", left_path, (0, 0, 1, 2), (0.0, 0.5), 0.5),
                SubjectMaskComponent("subject-02", right_path, (1, 0, 1, 2), (1.0, 0.5), 0.5),
                SubjectMaskComponent("subject-03", center_path, (0, 1, 1, 1), (0.0, 1.0), 0.1),
            )
            result = SubjectMaskResult(
                source_path=source_path.resolve(),
                source_size=(2, 2),
                mask_paths={"subject": subject_path, "background": background_path},
                model_id="model",
                model_version="version",
                weights_hash="sha256:abc",
                runtime_device="cpu",
                cache_hit=False,
                components=components,
            )

            merged_path = combine_subject_components(
                result, ("subject-01", "subject-02")
            )

            self.assertNotEqual(subject_path, merged_path)
            with Image.open(merged_path) as merged:
                np.testing.assert_array_equal(
                    np.asarray([[200, 220], [100, 80]], dtype=np.uint8),
                    np.asarray(merged.convert("L"), dtype=np.uint8),
                )
            self.assertEqual(left_path, combine_subject_components(result, ("subject-01",)))


class SubjectMaskPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_birefnet_result_registers_as_an_editable_provenance_mask(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_subject_panel_") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.jpg"
            Image.new("RGB", (40, 30), (30, 90, 150)).save(source_path)
            subject_path = root / "subject.png"
            background_path = root / "background.png"
            Image.new("L", (20, 15), 220).save(subject_path)
            Image.new("L", (20, 15), 35).save(background_path)
            result = SubjectMaskResult(
                source_path=source_path.resolve(),
                source_size=(20, 15),
                mask_paths={"subject": subject_path, "background": background_path},
                model_id="ZhengPeng7/BiRefNet",
                model_version="pinned-revision",
                weights_hash="sha256:abc123",
                runtime_device="cuda",
                cache_hit=False,
            )

            panel = PhotoEditorPanel()
            panel.set_image(source_path)
            mask_id = panel._register_subject_mask("subject", result)
            mask = panel._mask_by_id(mask_id)

            self.assertEqual("subject-select", mask["type"])
            self.assertEqual("subject", mask["semanticCategory"])
            self.assertEqual("subject-background", mask["uiStyle"])
            self.assertEqual("ZhengPeng7/BiRefNet", mask["model"]["id"])
            self.assertEqual("cuda", mask["model"]["runtimeDevice"])
            self.assertTrue(panel._bitmap_asset_path(mask).is_file())
            self.assertEqual("Select subject", panel.select_subject_button.accessibleName())
            self.assertEqual("Select background", panel.select_background_button.accessibleName())
            panel.close()

    def test_subject_inventory_promotes_birefnet_subject_and_background_actions(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_subject_roles_") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.jpg"
            Image.new("RGB", (40, 30), (30, 90, 150)).save(source_path)
            panel = PhotoEditorPanel()
            panel.set_image(source_path)
            panel._semantic_mask_result = SemanticMaskResult(
                source_path=source_path.resolve(),
                source_size=(40, 30),
                mask_paths={},
                model_id="scene-model",
                model_version="version",
                weights_hash="sha256:scene",
                cache_hit=True,
                presence={
                    "animals": SemanticCategoryPresence(True, 0.2, 0.2, 0.9, 0.8),
                    "people": SemanticCategoryPresence(True, 0.4, 0.4, 0.9, 0.8),
                    "sky": SemanticCategoryPresence(True, 0.3, 0.3, 0.9, 0.8),
                },
            )

            panel._populate_semantic_mask_buttons(
                panel._semantic_mask_result.detected_categories
            )

            self.assertFalse(panel.subject_mask_options.isHidden())
            self.assertTrue(panel._semantic_mask_buttons["animals"].isHidden())
            self.assertTrue(panel._semantic_mask_buttons["people"].isHidden())
            self.assertFalse(panel._semantic_mask_buttons["sky"].isHidden())
            panel.close()

    def test_subject_semantic_categories_route_to_birefnet(self) -> None:
        panel = PhotoEditorPanel()
        requested: list[str] = []
        panel.request_subject_mask = requested.append  # type: ignore[assignment]

        panel.handle_overlay_scene_picked("people")
        panel.handle_overlay_scene_picked("animals")
        panel.handle_overlay_scene_picked("sky")

        self.assertEqual(["subject", "subject"], requested)
        panel.close()

    def test_masks_and_new_mask_views_request_birefnet_model_warmup(self) -> None:
        panel = PhotoEditorPanel()
        requested: list[str] = []
        panel.subject_warm_requested.connect(requested.append)

        panel._set_editor_page(1)
        panel._open_mask_create_pane()

        self.assertEqual(["model", "model"], requested)
        panel.close()

    def test_popout_open_reset_returns_editor_to_adjustments(self) -> None:
        panel = PhotoEditorPanel()
        panel._set_editor_page(1)

        panel.show_adjustments_page()

        self.assertEqual(0, panel.editor_stack.currentIndex())
        self.assertTrue(panel._mode_buttons[0].isChecked())
        self.assertFalse(panel._mode_buttons[1].isChecked())
        panel.close()

    def test_multiple_subjects_require_a_choice_before_mask_registration(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_subject_choice_") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.jpg"
            subject_path = root / "subject.png"
            background_path = root / "background.png"
            left_path = root / "subject-01.png"
            right_path = root / "subject-02.png"
            Image.new("RGB", (40, 30), (30, 90, 150)).save(source_path)
            Image.new("L", (40, 30), 255).save(subject_path)
            Image.new("L", (40, 30), 0).save(background_path)
            Image.new("L", (40, 30), 180).save(left_path)
            Image.new("L", (40, 30), 220).save(right_path)
            result = SubjectMaskResult(
                source_path=source_path.resolve(),
                source_size=(40, 30),
                mask_paths={"subject": subject_path, "background": background_path},
                model_id="ZhengPeng7/BiRefNet",
                model_version="pinned-revision",
                weights_hash="sha256:abc123",
                runtime_device="cuda",
                cache_hit=False,
                components=(
                    SubjectMaskComponent(
                        "subject-01", left_path, (0, 0, 18, 30), (9.0, 15.0), 0.45
                    ),
                    SubjectMaskComponent(
                        "subject-02", right_path, (22, 0, 18, 30), (31.0, 15.0), 0.45
                    ),
                ),
            )
            panel = PhotoEditorPanel()
            panel.set_image(source_path)
            panel.editor_stack.setCurrentIndex(1)

            panel._show_subject_choice(result, (None, "add"))

            self.assertFalse(panel.subject_choice_panel.isHidden())
            self.assertFalse(panel.subject_choice_apply.isEnabled())
            overlay = panel.mask_overlay_state()
            self.assertIsNone(overlay["components"])
            self.assertEqual(2, len(overlay["subject_candidates"]))
            panel.handle_overlay_subject_candidate_toggled("subject-01")
            self.assertTrue(panel.subject_choice_apply.isEnabled())
            overlay = panel.mask_overlay_state()
            self.assertTrue(overlay["subject_candidates"][0]["selected"])
            self.assertFalse(overlay["subject_candidates"][1]["selected"])

            panel._apply_subject_choice()

            mask = panel._selected_mask_dict()
            self.assertEqual("subject_01", mask["semanticCategory"])
            self.assertFalse(panel.subject_choice_panel.isVisible())
            panel.close()


class SubjectCandidateOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_hover_and_click_target_the_subject_bitmap_not_its_bounding_box(self) -> None:
        from image_triage.ui.mask_overlay import MaskOverlay

        with tempfile.TemporaryDirectory(prefix="image_triage_subject_overlay_") as temp_dir:
            root = Path(temp_dir)
            mask_path = root / "subject.png"
            values = np.zeros((20, 40), dtype=np.uint8)
            values[4:17, 3:10] = 255
            Image.fromarray(values, mode="L").save(mask_path)
            overlay = MaskOverlay()
            overlay.resize(400, 200)
            overlay.set_state(
                interactive=True,
                show_overlay=True,
                create_mode=None,
                mask_type=None,
                params=None,
                source_size=(40, 20),
                subject_candidates=[
                    {
                        "id": "subject-01",
                        "assetPath": str(mask_path),
                        "bbox": (3, 4, 13, 13),
                        "coordinateSize": (40, 20),
                        "selected": False,
                    }
                ],
            )
            picked: list[str] = []
            overlay.subject_candidate_toggled.connect(picked.append)

            inside = QMouseEvent(
                QMouseEvent.Type.MouseMove,
                QPointF(80, 100),
                Qt.MouseButton.NoButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            )
            overlay.mouseMoveEvent(inside)
            self.assertEqual("subject-01", overlay._subject_hover)
            self.assertFalse(
                overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            )
            canvas = QImage(400, 200, QImage.Format.Format_ARGB32_Premultiplied)
            canvas.fill(0)
            painter = QPainter(canvas)
            overlay.render(painter, QPoint())
            painter.end()

            click = QMouseEvent(
                QMouseEvent.Type.MouseButtonPress,
                QPointF(80, 100),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            overlay.mousePressEvent(click)
            self.assertEqual(["subject-01"], picked)

            marker = overlay._subject_candidate_marker_rect(
                overlay._subject_candidates[0]
            )
            marker_click = QMouseEvent(
                QMouseEvent.Type.MouseButtonPress,
                marker.center(),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            overlay.mousePressEvent(marker_click)
            self.assertEqual(["subject-01", "subject-01"], picked)

            # Inside the broad bbox but outside the actual person silhouette.
            outside = QMouseEvent(
                QMouseEvent.Type.MouseMove,
                QPointF(145, 100),
                Qt.MouseButton.NoButton,
                Qt.MouseButton.NoButton,
                Qt.KeyboardModifier.NoModifier,
            )
            overlay.mouseMoveEvent(outside)
            self.assertIsNone(overlay._subject_hover)
            overlay.close()


if __name__ == "__main__":
    unittest.main()
