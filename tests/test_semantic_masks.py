from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

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

    def test_onnxruntime_falls_back_to_the_installed_ai_runtime(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_ort_runtime_") as temp_dir:
            site_packages = Path(temp_dir) / "site-packages"
            (site_packages / "onnxruntime" / "capi").mkdir(parents=True)
            marker = object()
            calls: list[str] = []
            original_ort = semantic_masks.ort
            original_error = semantic_masks._ORT_IMPORT_ERROR
            original_import = semantic_masks.importlib.import_module
            original_candidates = semantic_masks._candidate_onnxruntime_site_packages

            def fake_import(name: str):
                calls.append(name)
                if str(site_packages) not in os.sys.path:
                    raise ModuleNotFoundError(name)
                return marker

            semantic_masks.ort = None
            semantic_masks._ORT_IMPORT_ERROR = ""
            semantic_masks.importlib.import_module = fake_import
            semantic_masks._candidate_onnxruntime_site_packages = lambda: (site_packages,)
            try:
                self.assertIs(marker, semantic_masks._load_onnxruntime())
            finally:
                semantic_masks.ort = original_ort
                semantic_masks._ORT_IMPORT_ERROR = original_error
                semantic_masks.importlib.import_module = original_import
                semantic_masks._candidate_onnxruntime_site_packages = original_candidates
                if str(site_packages) in os.sys.path:
                    os.sys.path.remove(str(site_packages))

            self.assertEqual(["onnxruntime", "onnxruntime"], calls)

    def test_box_mean_preserves_constant_images(self) -> None:
        source = np.full((17, 23), 0.375, dtype=np.float32)
        filtered = _box_mean(source, 4)
        self.assertEqual(source.shape, filtered.shape)
        np.testing.assert_allclose(filtered, source, atol=1e-5)

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

    def test_one_inference_populates_all_categories_and_then_hits_cache(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_semantic_cache_") as temp_dir:
            root = Path(temp_dir)
            source_path = root / "source.jpg"
            Image.new("RGB", (12, 8), (20, 80, 140)).save(source_path)
            model_root = root / "model"
            onnx_root = model_root / "onnx"
            onnx_root.mkdir(parents=True)
            (onnx_root / "model.onnx").write_bytes(b"fake-model")
            (onnx_root / "config.json").write_text('{"id2label":{"0":"sky"}}', encoding="utf-8")
            (onnx_root / "preprocessor_config.json").write_text("{}", encoding="utf-8")
            installation = AIModelInstallation(
                repo_id="owner/model",
                revision="revision-1",
                install_dir=model_root,
                required_filenames=(
                    "onnx/model.onnx",
                    "onnx/config.json",
                    "onnx/preprocessor_config.json",
                ),
            )
            rgb = np.full((8, 12, 3), 96, dtype=np.uint8)
            generated = {
                category: np.full((8, 12), (index + 1) / 10.0, dtype=np.float32)
                for index, category in enumerate(SEMANTIC_MASK_CATEGORIES)
            }
            cache_root = root / "cache"
            originals = {
                name: getattr(semantic_masks, name)
                for name in (
                    "_decode_rgb_preview",
                    "_model_session",
                    "_resolve_category_indices",
                    "_infer_masks",
                    "_guided_filter",
                    "_repair_sky_mask_boundaries",
                    "_refine_water_mask_topology",
                    "_tighten_mask_confidence",
                )
            }
            calls = {"decode": 0, "infer": 0}

            def fake_decode(*_args, **_kwargs):
                calls["decode"] += 1
                return rgb

            def fake_infer(*_args, **_kwargs):
                calls["infer"] += 1
                return generated

            semantic_masks._decode_rgb_preview = fake_decode
            semantic_masks._model_session = lambda *_args, **_kwargs: object()
            semantic_masks._resolve_category_indices = lambda *_args, **_kwargs: {}
            semantic_masks._infer_masks = fake_infer
            semantic_masks._guided_filter = lambda _rgb, mask: mask
            semantic_masks._repair_sky_mask_boundaries = lambda _rgb, mask: mask
            semantic_masks._refine_water_mask_topology = lambda mask: mask
            semantic_masks._tighten_mask_confidence = lambda mask: mask
            try:
                first = ensure_semantic_masks(
                    source_path,
                    installation=installation,
                    cache_root=cache_root,
                )
                second = ensure_semantic_masks(
                    source_path,
                    installation=installation,
                    cache_root=cache_root,
                )
            finally:
                for name, original in originals.items():
                    setattr(semantic_masks, name, original)

            self.assertFalse(first.cache_hit)
            self.assertTrue(second.cache_hit)
            self.assertEqual(set(first.mask_paths), set(SEMANTIC_MASK_CATEGORIES))
            self.assertTrue(all(path.is_file() for path in first.mask_paths.values()))
            self.assertEqual(
                ("water", "mountains", "animals", "people"),
                first.detected_categories,
            )
            self.assertEqual(first.presence, second.presence)
            self.assertEqual(1, calls["decode"])
            self.assertEqual(1, calls["infer"])
            metadata = json.loads(
                (first.mask_paths["sky"].parent / "metadata.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                semantic_masks.SEMANTIC_MASK_REFINEMENT_VERSION,
                metadata["refinementVersion"],
            )


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
                model_id="nvidia/segformer-b0-finetuned-ade-512-512",
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
