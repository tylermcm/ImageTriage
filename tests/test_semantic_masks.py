from __future__ import annotations

import json
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel

from image_triage.ai_model import AIModelInstallation
import image_triage.semantic_masks as semantic_masks
from image_triage.semantic_masks import (
    SEMANTIC_MASK_CATEGORIES,
    SEMANTIC_MASK_INVENTORY_REQUEST,
    SemanticCategoryPresence,
    SemanticMaskResult,
    _box_mean,
    _measure_semantic_presence,
    _refine_water_mask_topology,
    _repair_sky_mask_boundaries,
    _resolve_semantic_presence_conflicts,
    _tighten_mask_confidence,
    ensure_semantic_masks,
)
from image_triage.ui.photo_editor_panel import (
    EditRecipe,
    PhotoEditorPanel,
    replace_mask_operations,
)
from photo_terminal.session import save_session


class SemanticMaskCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        # ensure_semantic_masks now writes an always-on execution.log line; keep
        # test runs from touching the user's real log dir by redirecting it.
        self._log_tmp = tempfile.TemporaryDirectory(prefix="image_triage_execlog_iso_")
        self._prev_log_dir = os.environ.get("IMAGE_TRIAGE_LOG_DIR")
        os.environ["IMAGE_TRIAGE_LOG_DIR"] = self._log_tmp.name

    def tearDown(self) -> None:
        if self._prev_log_dir is None:
            os.environ.pop("IMAGE_TRIAGE_LOG_DIR", None)
        else:
            os.environ["IMAGE_TRIAGE_LOG_DIR"] = self._prev_log_dir
        self._log_tmp.cleanup()

    def test_presence_requires_a_confident_connected_region(self) -> None:
        mountains = np.zeros((128, 128), dtype=np.float32)
        mountains[20:80, 15:95] = 0.9
        mountain_stats = _measure_semantic_presence("mountains", mountains)
        self.assertTrue(mountain_stats.present)
        self.assertGreater(mountain_stats.coverage, 0.25)

        people_noise = np.zeros((128, 128), dtype=np.float32)
        people_noise[10:12, 10:12] = 0.4
        self.assertFalse(_measure_semantic_presence("people", people_noise).present)

        person = np.zeros((128, 128), dtype=np.float32)
        person[10:12, 10:12] = 0.9
        self.assertTrue(_measure_semantic_presence("people", person).present)

    def test_stronger_animal_region_suppresses_overlapping_person_false_positive(self) -> None:
        animal_mask = np.zeros((128, 128), dtype=np.float32)
        animal_mask[20:100, 25:105] = 0.9
        person_mask = np.zeros((128, 128), dtype=np.float32)
        person_mask[35:80, 45:75] = 0.9
        masks = {"animals": animal_mask, "people": person_mask}
        presence = {
            category: _measure_semantic_presence(category, mask)
            for category, mask in masks.items()
        }
        resolved = _resolve_semantic_presence_conflicts(masks, presence)
        self.assertTrue(resolved["animals"].present)
        self.assertFalse(resolved["people"].present)

        person_mask = np.zeros((128, 128), dtype=np.float32)
        person_mask[2:20, 2:12] = 0.9
        masks["people"] = person_mask
        presence["people"] = _measure_semantic_presence("people", person_mask)
        separated = _resolve_semantic_presence_conflicts(masks, presence)
        self.assertTrue(separated["animals"].present)
        self.assertTrue(separated["people"].present)

    def test_opencv_falls_back_to_the_installed_ai_runtime(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_cv2_runtime_") as temp_dir:
            site_packages = Path(temp_dir) / "site-packages"
            (site_packages / "cv2").mkdir(parents=True)
            marker = object()
            calls: list[str] = []
            original_cv2 = semantic_masks.cv2
            original_import = semantic_masks.importlib.import_module
            original_candidates = semantic_masks._candidate_ai_runtime_site_packages

            def fake_import(name: str):
                calls.append(name)
                if str(site_packages) not in os.sys.path:
                    raise ModuleNotFoundError(name)
                return marker

            semantic_masks.cv2 = None
            semantic_masks.importlib.import_module = fake_import
            semantic_masks._candidate_ai_runtime_site_packages = lambda: (site_packages,)
            try:
                self.assertIs(marker, semantic_masks._load_opencv())
            finally:
                semantic_masks.cv2 = original_cv2
                semantic_masks.importlib.import_module = original_import
                semantic_masks._candidate_ai_runtime_site_packages = original_candidates
                if str(site_packages) in os.sys.path:
                    os.sys.path.remove(str(site_packages))

            self.assertEqual(["cv2", "cv2"], calls)

    def test_box_mean_preserves_constant_images(self) -> None:
        source = np.full((17, 23), 0.375, dtype=np.float32)
        filtered = _box_mean(source, 4)
        self.assertEqual(source.shape, filtered.shape)
        np.testing.assert_allclose(filtered, source, atol=1e-5)

    def test_execution_summary_line_is_human_readable(self) -> None:
        miss = semantic_masks._format_execution_summary(
            cache_hit=False, total_ms=1109.7, decode_ms=190.0, worker_ms=210.0,
            worker_infer_ms=56.0, refine_ms=567.0, detected=("sky", "water"), device="cuda",
        )
        self.assertIn("Scene masks ready in 1110 ms", miss)
        self.assertIn("decode 190", miss)
        self.assertIn("infer 56", miss)
        self.assertIn("refine 567", miss)
        self.assertIn("2 regions", miss)
        self.assertIn("cuda", miss)
        hit = semantic_masks._format_execution_summary(
            cache_hit=True, total_ms=148.0, decode_ms=0.0, worker_ms=0.0,
            worker_infer_ms=0.0, refine_ms=0.0, detected=("sky",), device="cache",
        )
        self.assertIn("from cache in 148 ms", hit)
        self.assertIn("1 region", hit)
        self.assertNotIn("1 regions", hit)  # singular

    def test_execution_summary_is_logged_surfaced_and_written_always_on(self) -> None:
        events: list[tuple[str, dict]] = []
        messages: list[str] = []

        class _Logger:
            def log(self, event: str, **fields: object) -> None:
                events.append((event, fields))

        from image_triage.perf import execution_log_path

        with tempfile.TemporaryDirectory(prefix="image_triage_execlog_") as temp_dir:
            with unittest.mock.patch.dict(
                os.environ, {"IMAGE_TRIAGE_LOG_DIR": temp_dir}, clear=False
            ):
                semantic_masks._emit_execution_summary(
                    _Logger(), messages.append, cache_hit=False, total_ms=1000.0,
                    detected=("sky", "trees"), device="cpu",
                    decode_ms=1.0, worker_ms=2.0, worker_infer_ms=3.0, refine_ms=4.0,
                    label="_DSC1363.JPG",
                )
                # (1) structured perf event, (2) live status message, (3) always-on file.
                self.assertEqual("ai.mask.oneformer.summary", events[0][0])
                self.assertIn("message", events[0][1])
                self.assertEqual(1, len(messages))
                self.assertEqual(events[0][1]["message"], messages[0])
                written = execution_log_path().read_text(encoding="utf-8")
                self.assertIn("_DSC1363.JPG: Scene masks ready in 1000 ms", written)
                self.assertIn("pid=", written)

    def test_guided_filter_precomputed_stats_match_standalone(self) -> None:
        rng = np.random.default_rng(7)
        rgb = (rng.random((40, 60, 3)) * 255).astype(np.uint8)
        mask = np.zeros((40, 60), dtype=np.float32)
        mask[8:30, 12:44] = 1.0
        standalone = semantic_masks._guided_filter(rgb, mask)
        shared = semantic_masks._guided_filter(
            rgb, mask, stats=semantic_masks._guide_stats(rgb)
        )
        np.testing.assert_allclose(standalone, shared, atol=1e-6)

    def test_confidence_tightening_reduces_edge_tails_without_moving_midpoint(self) -> None:
        source = np.asarray(
            [0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0],
            dtype=np.float32,
        )
        tightened = _tighten_mask_confidence(source)
        self.assertEqual(0.0, float(tightened[0]))
        self.assertEqual(1.0, float(tightened[-1]))
        self.assertAlmostEqual(0.5, float(tightened[3]), places=6)
        np.testing.assert_array_less(tightened[1:3], source[1:3])
        np.testing.assert_array_less(source[4:6], tightened[4:6])

    def test_sky_boundary_repair_fills_an_image_supported_notch(self) -> None:
        height, width = 120, 180
        y, x = np.mgrid[:height, :width]
        ridge = 58 + (np.abs(x - width / 2) * 0.10).astype(int)
        sky = y < ridge
        rgb = np.empty((height, width, 3), dtype=np.uint8)
        rgb[sky] = (95, 155, 220)
        rgb[~sky] = (100, 75, 45)
        mask = np.where(sky, 0.95, 0.01).astype(np.float32)
        notch = (x >= 78) & (x <= 102) & (y >= 28) & sky
        mask[notch] = 0.10

        repaired = _repair_sky_mask_boundaries(rgb, mask)

        self.assertGreater(float(np.mean(repaired[notch])), 0.40)
        self.assertLess(float(np.mean(repaired[~sky])), 0.02)

    def test_sky_boundary_repair_skips_weak_sky_detections(self) -> None:
        rgb = np.full((80, 120, 3), (95, 155, 220), dtype=np.uint8)
        mask = np.full((80, 120), 0.04, dtype=np.float32)
        mask[:5, :10] = 0.95

        repaired = _repair_sky_mask_boundaries(rgb, mask)

        np.testing.assert_array_equal(mask, repaired)

    def test_water_topology_removes_disconnected_upper_false_positives(self) -> None:
        mask = np.zeros((120, 180), dtype=np.float32)
        mask[70:, 20:160] = 0.95
        mask[67:70, 20:160] = 0.20
        mask[20:30, 25:45] = 0.90
        mask[92:112, 2:12] = 0.85

        refined = _refine_water_mask_topology(mask)

        self.assertEqual(0.0, float(np.max(refined[20:30, 25:45])))
        self.assertGreater(float(np.mean(refined[75:, 30:150])), 0.90)
        self.assertGreater(float(np.mean(refined[67:70, 30:150])), 0.15)
        self.assertGreater(float(np.mean(refined[92:112, 2:12])), 0.80)

    @staticmethod
    def _install_oneformer_model(root: Path, *, revision: str = "revision-1") -> "AIModelInstallation":
        model_root = root / "model"
        model_root.mkdir(parents=True, exist_ok=True)
        for filename in semantic_masks.resolve_segmentation_model_installation().required_filenames:
            content = b"weights" if filename == "pytorch_model.bin" else b"{}"
            (model_root / filename).write_bytes(content)
        return AIModelInstallation(
            repo_id="shi-labs/oneformer_ade20k_swin_tiny",
            revision=revision,
            install_dir=model_root,
            required_filenames=semantic_masks.resolve_segmentation_model_installation().required_filenames,
        )

    def _patched_worker(self, present: set[str]):
        """Patch the worker + refinement path; return a call-counter dict."""
        calls = {"decode": 0, "worker": 0}
        rgb = np.full((8, 12, 3), 96, dtype=np.uint8)

        def fake_decode(*_args, **_kwargs):
            calls["decode"] += 1
            return rgb

        def fake_worker(*, model_dir, input_path, output_dir, progress_callback):
            calls["worker"] += 1
            output_dir.mkdir(parents=True, exist_ok=True)
            for category in SEMANTIC_MASK_CATEGORIES:
                fill = 255 if category in present else 0
                Image.new("L", (12, 8), fill).save(output_dir / f"{category}.png")
            return SimpleNamespace(device="cpu", source_size=(12, 8), category_stats={}, timings_ms={})

        self._restore = {
            name: getattr(semantic_masks, name)
            for name in (
                "_decode_rgb_preview",
                "_run_semantic_worker",
                "validate_semantic_runtime",
                "_guided_filter",
                "_repair_sky_mask_boundaries",
                "_refine_water_mask_topology",
            )
        }
        semantic_masks._decode_rgb_preview = fake_decode
        semantic_masks._run_semantic_worker = fake_worker
        semantic_masks.validate_semantic_runtime = lambda: None
        semantic_masks._guided_filter = lambda _rgb, mask, **_kw: mask
        semantic_masks._repair_sky_mask_boundaries = lambda _rgb, mask: mask
        semantic_masks._refine_water_mask_topology = lambda mask: mask
        return calls

    def _unpatch_worker(self) -> None:
        for name, original in getattr(self, "_restore", {}).items():
            setattr(semantic_masks, name, original)

    def test_one_inference_populates_all_categories_and_then_hits_cache(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_semantic_cache_") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.jpg"
            Image.new("RGB", (12, 8), (20, 80, 140)).save(source_path)
            installation = self._install_oneformer_model(root)
            cache_root = root / "cache"
            calls = self._patched_worker({"sky", "water", "mountains", "buildings"})
            try:
                first = ensure_semantic_masks(
                    source_path, installation=installation, cache_root=cache_root
                )
                second = ensure_semantic_masks(
                    source_path, installation=installation, cache_root=cache_root
                )
            finally:
                self._unpatch_worker()

            self.assertFalse(first.cache_hit)
            self.assertTrue(second.cache_hit)
            self.assertEqual(set(first.mask_paths), set(SEMANTIC_MASK_CATEGORIES))
            self.assertTrue(all(path.is_file() for path in first.mask_paths.values()))
            self.assertEqual(
                ("sky", "water", "mountains", "buildings"),
                first.detected_categories,
            )
            self.assertEqual(first.presence, second.presence)
            self.assertEqual(1, calls["decode"])
            self.assertEqual(1, calls["worker"])
            metadata = json.loads(
                (first.mask_paths["sky"].parent / "metadata.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                semantic_masks.SEMANTIC_MASK_REFINEMENT_VERSION,
                metadata["refinementVersion"],
            )
            self.assertEqual(
                semantic_masks.SEMANTIC_MASK_MAPPING_VERSION,
                metadata["mappingVersion"],
            )

    def _run_twice_counting_worker(self, mutate) -> tuple[int, int]:
        """Run ensure_semantic_masks, apply ``mutate``, run again; return worker call counts."""
        with tempfile.TemporaryDirectory(prefix="image_triage_semantic_inval_") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.jpg"
            Image.new("RGB", (12, 8), (20, 80, 140)).save(source_path)
            installation = self._install_oneformer_model(root)
            cache_root = root / "cache"
            calls = self._patched_worker({"sky", "water"})
            try:
                ensure_semantic_masks(source_path, installation=installation, cache_root=cache_root)
                first_count = calls["worker"]
                installation = mutate(root, source_path, installation) or installation
                ensure_semantic_masks(source_path, installation=installation, cache_root=cache_root)
                second_count = calls["worker"]
            finally:
                self._unpatch_worker()
        return first_count, second_count

    def test_cache_invalidates_when_source_mtime_changes(self) -> None:
        def mutate(_root, source_path, _installation):
            future = source_path.stat().st_mtime_ns + 5_000_000_000
            os.utime(source_path, ns=(future, future))

        first, second = self._run_twice_counting_worker(mutate)
        self.assertEqual((1, 2), (first, second))

    def test_cache_invalidates_when_weights_hash_changes(self) -> None:
        def mutate(_root, _source_path, installation):
            (installation.install_dir / "pytorch_model.bin").write_bytes(b"different-weights")

        first, second = self._run_twice_counting_worker(mutate)
        self.assertEqual((1, 2), (first, second))

    def test_cache_invalidates_when_model_revision_changes(self) -> None:
        def mutate(root, _source_path, installation):
            return self._install_oneformer_model(root, revision="revision-2")

        first, second = self._run_twice_counting_worker(mutate)
        self.assertEqual((1, 2), (first, second))

    def test_cache_invalidates_when_mapping_version_changes(self) -> None:
        def mutate(_root, _source_path, _installation):
            semantic_masks.SEMANTIC_MASK_MAPPING_VERSION = "ade20k-app-categories-test-2"

        original = semantic_masks.SEMANTIC_MASK_MAPPING_VERSION
        try:
            first, second = self._run_twice_counting_worker(mutate)
        finally:
            semantic_masks.SEMANTIC_MASK_MAPPING_VERSION = original
        self.assertEqual((1, 2), (first, second))

    def test_cache_invalidates_when_refinement_version_changes(self) -> None:
        def mutate(_root, _source_path, _installation):
            semantic_masks.SEMANTIC_MASK_REFINEMENT_VERSION = "oneformer-ade-guided-test-2"

        original = semantic_masks.SEMANTIC_MASK_REFINEMENT_VERSION
        try:
            first, second = self._run_twice_counting_worker(mutate)
        finally:
            semantic_masks.SEMANTIC_MASK_REFINEMENT_VERSION = original
        self.assertEqual((1, 2), (first, second))


class SemanticMaskPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_generated_mask_is_pinned_and_participates_in_live_compositing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_semantic_panel_") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.jpg"
            Image.new("RGB", (40, 30), (30, 90, 150)).save(source_path)
            cached_mask = root / "sky.png"
            Image.new("L", (20, 15), 220).save(cached_mask)
            result = SemanticMaskResult(
                source_path=source_path.resolve(),
                source_size=(20, 15),
                mask_paths={"sky": cached_mask},
                model_id="shi-labs/oneformer_ade20k_swin_tiny",
                model_version="revision-1",
                weights_hash="sha256:abc123",
                cache_hit=False,
                presence={
                    "sky": SemanticCategoryPresence(
                        present=True,
                        coverage=0.45,
                        largest_component_coverage=0.44,
                        peak_confidence=0.98,
                        mean_confidence=0.91,
                    ),
                },
            )

            panel = PhotoEditorPanel()
            panel.set_image(source_path)
            _session_path, session = panel._ensure_session()
            session["coordinateSpaces"][0]["sourceWidth"] = None
            session["coordinateSpaces"][0]["sourceHeight"] = None
            save_session(panel._session_path, session)
            panel._session = session

            mask_id = panel._register_semantic_mask("sky", result)
            mask = panel._mask_by_id(mask_id)
            self.assertEqual("subject-select", mask["type"])
            self.assertEqual("sky", mask["semanticCategory"])
            self.assertEqual("sha256:abc123", mask["model"]["weightsHash"])
            self.assertEqual(
                semantic_masks.SEMANTIC_MASK_REFINEMENT_VERSION,
                mask["model"]["refinementVersion"],
            )
            self.assertEqual(
                (20, 15),
                (
                    panel._session["coordinateSpaces"][0]["sourceWidth"],
                    panel._session["coordinateSpaces"][0]["sourceHeight"],
                ),
            )
            self.assertTrue(panel._bitmap_asset_path(mask).is_file())
            # The list row is a custom widget; the name lives on its label (and
            # the item's AccessibleTextRole), not the item's display text.
            current = panel.masks_list.currentItem()
            self.assertEqual("Sky", current.data(Qt.ItemDataRole.AccessibleTextRole))
            row_label = panel.masks_list.itemWidget(current).findChild(QLabel, "maskRowLabel")
            self.assertEqual("Sky", row_label.text())

            replace_mask_operations(
                panel._session,
                mask_id,
                EditRecipe.from_dict({"exposure": 1.0}),
            )
            panel._write_session(panel._session, "Test local semantic adjustment")
            masked = panel.masked_adjustments()
            self.assertEqual(1, len(masked))
            components, source_size, recipe = masked[0]
            self.assertEqual("bitmap", components[0][0])
            self.assertTrue(Path(components[0][1]["assetPath"]).is_file())
            self.assertEqual((20, 15), source_size)
            self.assertEqual(1.0, recipe.exposure)
            panel.close()

    def test_existing_semantic_mask_bitmap_refreshes_without_changing_its_id(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_semantic_refresh_") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.jpg"
            Image.new("RGB", (40, 30), (30, 90, 150)).save(source_path)
            old_bitmap = root / "old-sky.png"
            new_bitmap = root / "new-sky.png"
            Image.new("L", (20, 15), 220).save(old_bitmap)
            Image.new("L", (20, 15), 70).save(new_bitmap)

            panel = PhotoEditorPanel()
            panel.set_image(source_path)
            old_result = SemanticMaskResult(
                source_path=source_path.resolve(),
                source_size=(20, 15),
                mask_paths={"sky": old_bitmap},
                model_id="owner/model",
                model_version="revision-1",
                weights_hash="sha256:abc123",
                cache_hit=False,
                refinement_version="guided-v1",
            )
            mask_id = panel._register_semantic_mask("sky", old_result)
            panel._session["operations"].append(
                {
                    "id": "local-op",
                    "type": "adjust.exposure",
                    "enabled": True,
                    "maskId": mask_id,
                    "params": {"ev": 1.0},
                }
            )
            save_session(panel._session_path, panel._session)
            new_result = SemanticMaskResult(
                source_path=source_path.resolve(),
                source_size=(20, 15),
                mask_paths={"sky": new_bitmap},
                model_id="owner/model",
                model_version="revision-1",
                weights_hash="sha256:abc123",
                cache_hit=False,
            )

            self.assertEqual(1, panel._refresh_existing_semantic_masks(new_result))
            mask = panel._mask_by_id(mask_id)
            self.assertEqual(mask_id, mask["id"])
            self.assertEqual(
                semantic_masks.SEMANTIC_MASK_REFINEMENT_VERSION,
                mask["model"]["refinementVersion"],
            )
            self.assertEqual("local-op", panel._session["operations"][-1]["id"])
            with Image.open(panel._bitmap_asset_path(mask)) as refreshed:
                self.assertEqual(70, refreshed.convert("L").getpixel((10, 7)))
            panel.close()

    def test_semantic_mask_can_be_added_as_a_subtracted_group_member(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_semantic_group_") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.jpg"
            mask_path = root / "sky.png"
            Image.new("RGB", (40, 30), (30, 90, 150)).save(source_path)
            Image.new("L", (20, 15), 220).save(mask_path)

            panel = PhotoEditorPanel()
            panel.set_image(source_path)
            session_path, session = panel._ensure_session()
            space_id = session["coordinateSpaces"][0]["id"]
            session["masks"].append(
                {
                    "id": "mask-001",
                    "type": "radial",
                    "coordinateSpaceId": space_id,
                    "params": {},
                }
            )
            save_session(session_path, session)
            panel._session = session
            result = SemanticMaskResult(
                source_path=source_path.resolve(),
                source_size=(20, 15),
                mask_paths={"sky": mask_path},
                model_id="owner/model",
                model_version="revision-1",
                weights_hash="sha256:abc123",
                cache_hit=False,
            )

            mask_id = panel._register_semantic_mask(
                "sky",
                result,
                parent_id="mask-001",
                combine="subtract",
            )

            mask = panel._mask_by_id(mask_id)
            self.assertEqual("mask-001", mask["parentId"])
            self.assertEqual("subtract", mask["combine"])
            panel.close()

    def test_mask_panel_only_displays_detected_semantic_categories(self) -> None:
        panel = PhotoEditorPanel()
        panel._semantic_mask_result = SemanticMaskResult(
            source_path=Path("source.jpg").resolve(),
            source_size=(20, 15),
            mask_paths={},
            model_id="model",
            model_version="revision",
            weights_hash="sha256:abc",
            cache_hit=True,
            presence={
                "sky": SemanticCategoryPresence(True, 0.4, 0.39, 0.98, 0.9),
                "mountains": SemanticCategoryPresence(True, 0.3, 0.29, 0.97, 0.88),
                "people": SemanticCategoryPresence(False, 0.0001, 0.0001, 0.4, 0.4),
            },
        )
        panel._populate_semantic_mask_buttons(
            panel._semantic_mask_result.detected_categories
        )
        self.assertEqual(
            {"sky", "mountains"},
            {
                category
                for category, button in panel._semantic_mask_buttons.items()
                if not button.isHidden()
            },
        )
        panel.close()

    def test_entering_masks_requests_oneformer_model_warmup(self) -> None:
        panel = PhotoEditorPanel()
        requested: list[str] = []
        panel.semantic_warm_requested.connect(requested.append)
        try:
            panel._set_editor_page(1)
            self.assertEqual(["model"], requested)
        finally:
            panel.close()

    def test_opening_masks_tab_starts_scene_inventory_lazily(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_semantic_lazy_") as temp_dir:
            source_path = Path(temp_dir) / "source.jpg"
            Image.new("RGB", (40, 30), (30, 90, 150)).save(source_path)
            panel = PhotoEditorPanel()
            requests: list[str] = []
            original_start = panel._start_semantic_mask_task
            panel._start_semantic_mask_task = requests.append
            try:
                panel.set_image(source_path)
                self.assertEqual([], requests)
                panel._set_editor_page(1)
                self.assertEqual([SEMANTIC_MASK_INVENTORY_REQUEST], requests)
            finally:
                panel._start_semantic_mask_task = original_start
                panel.close()


if __name__ == "__main__":
    unittest.main()
