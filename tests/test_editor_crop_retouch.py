"""Crop geometry, parametric retouch, and the overlays that edit them.

The theme of this file is that geometry is single-sourced. ViewTransform is the
one description of how source pixels reach the screen, and the renderer, the
mask strength field, the mattes and every overlay must all read it — if any one
of them keeps its own idea of the mapping, masks drift with no error raised.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cli_editor"))

import numpy as np
from PIL import Image
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QLabel

from image_triage.editor_geometry import ViewTransform, view_transform_for
from image_triage.ui.canvas_overlay import CanvasOverlay, OverlayStack
from image_triage.ui.crop_overlay import CropOverlay
from image_triage.ui.mask_overlay import MaskOverlay, mask_strength_qimage
from image_triage.ui.photo_editor_panel import (
    GUI_GLOBAL_ONLY_OP_TYPES,
    EditRecipe,
    PhotoEditorPanel,
    operations_from_recipe,
    recipe_from_session,
)
from image_triage.ui.retouch_overlay import RetouchOverlay
from photo_terminal.adjustments import apply_retouch, heal_spot_patch, remove_red_eye
from photo_terminal.session import RENDERER_ORDER, load_session, validate_operation_params


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class ViewTransformTests(unittest.TestCase):
    """The invariant the whole feature rests on."""

    def test_crop_corners_map_to_frame_corners(self) -> None:
        view = ViewTransform(source_size=(400, 300), crop=(50, 40, 250, 190))
        width, height = view.frame_size()
        self.assertEqual((200, 150), (width, height))
        for source, frame in (
            ((50, 40), (0, 0)),
            ((250, 40), (width, 0)),
            ((250, 190), (width, height)),
            ((50, 190), (0, height)),
        ):
            with self.subTest(source=source):
                got = view.source_to_frame(*source)
                self.assertAlmostEqual(frame[0], got[0], places=6)
                self.assertAlmostEqual(frame[1], got[1], places=6)

    def test_mapping_round_trips_at_every_angle_and_flip(self) -> None:
        rng = np.random.RandomState(3)
        points = rng.uniform(0, 300, size=(50, 2))
        for angle in (0.0, 7.5, -12.0, 45.0):
            for flip_h, flip_v in ((False, False), (True, False), (False, True), (True, True)):
                view = ViewTransform(
                    (400, 300), crop=(50, 40, 250, 190),
                    angle=angle, flip_h=flip_h, flip_v=flip_v,
                )
                with self.subTest(angle=angle, flip_h=flip_h, flip_v=flip_v):
                    for x, y in points:
                        fx, fy = view.source_to_frame(x, y)
                        bx, by = view.frame_to_source(fx, fy)
                        self.assertAlmostEqual(x, bx, places=6)
                        self.assertAlmostEqual(y, by, places=6)

    def test_identity_when_nothing_is_set(self) -> None:
        view = view_transform_for(EditRecipe(), (400, 300))
        self.assertTrue(view.is_identity())
        self.assertEqual((400, 300), view.frame_size())

    def test_bypass_ignores_the_crop_but_keeps_the_straighten(self) -> None:
        recipe = EditRecipe.from_dict({"crop": (50, 40, 250, 190), "crop_angle": 0.0})
        view = view_transform_for(recipe, (400, 300), bypass_crop=True)
        self.assertEqual((400, 300), view.frame_size())
        self.assertIsNone(view.effective_crop)


class GeometryRenderTests(unittest.TestCase):
    def setUp(self) -> None:
        rng = np.random.RandomState(5)
        self.arr = rng.randint(0, 255, (300, 400, 3), dtype=np.uint8)
        self.image = Image.fromarray(self.arr, "RGB")

    def test_a_plain_crop_is_exactly_an_array_slice(self) -> None:
        # The renderer goes through an affine resample; for an axis-aligned
        # integer crop that must still be lossless.
        out = np.asarray(EditRecipe.from_dict({"crop": (50, 40, 250, 190)}).apply(self.image))
        self.assertTrue(np.array_equal(out, self.arr[40:190, 50:250]))

    def test_flips_are_exact_mirrors(self) -> None:
        flipped_h = np.asarray(EditRecipe.from_dict({"flip_h": True}).apply(self.image))
        self.assertTrue(np.array_equal(flipped_h, self.arr[:, ::-1]))
        flipped_v = np.asarray(EditRecipe.from_dict({"flip_v": True}).apply(self.image))
        self.assertTrue(np.array_equal(flipped_v, self.arr[::-1, :]))

    def test_straighten_keeps_the_whole_rotated_frame(self) -> None:
        out = EditRecipe.from_dict({"crop_angle": 10.0}).apply(self.image)
        self.assertGreater(out.width, 400)
        self.assertGreater(out.height, 300)

    def test_no_geometry_leaves_the_frame_untouched(self) -> None:
        self.assertTrue(np.array_equal(np.asarray(EditRecipe().apply(self.image)), self.arr))


class MaskStrengthUnderCropTests(unittest.TestCase):
    """Masks must go through the same transform the pixels do."""

    def setUp(self) -> None:
        self.app = _app()
        # density is a percentage (0..100), not a fraction.
        self.components = [("radial", {"cx": 200.0, "cy": 150.0, "rx": 80.0, "ry": 60.0,
                                       "feather": 50.0, "density": 100.0}, "add")]

    def test_no_transform_is_the_original_path(self) -> None:
        # The equivalence that makes this safe to add: passing no transform must
        # be byte-identical to what the code did before crop existed.
        plain = mask_strength_qimage(self.components, 400, 300, (400, 300))
        identity = mask_strength_qimage(
            self.components, 400, 300, (400, 300),
            transform=ViewTransform(source_size=(400, 300)).qtransform(),
        )
        self.assertIsNotNone(plain)
        self.assertIsNotNone(identity)
        # Painted through a transform, so allow resampling slack — but the
        # fields must describe the same mask.
        a = np.frombuffer(plain.constBits(), dtype=np.uint8).astype(float)
        b = np.frombuffer(identity.constBits(), dtype=np.uint8).astype(float)
        self.assertEqual(a.shape, b.shape)
        self.assertLess(np.abs(a - b).mean(), 2.0)

    def test_a_crop_moves_the_mask_with_the_photo(self) -> None:
        # The mask sits at source (200, 150). Crop to (100, 75)-(300, 225) and it
        # must land at the centre of the 200x150 frame, not wherever a
        # scale-only mapping would put it.
        view = ViewTransform(source_size=(400, 300), crop=(100, 75, 300, 225))
        field = mask_strength_qimage(
            self.components, 200, 150, (400, 300), transform=view.qtransform()
        )
        self.assertIsNotNone(field)
        arr = np.frombuffer(field.constBits(), dtype=np.uint8).reshape(
            field.height(), field.bytesPerLine()
        )[:, : field.width()]
        ys, xs = np.nonzero(arr > 200)
        self.assertTrue(len(xs) > 0, "mask vanished under the crop")
        self.assertAlmostEqual(100.0, float(xs.mean()), delta=8.0)
        self.assertAlmostEqual(75.0, float(ys.mean()), delta=8.0)


class RetouchKernelTests(unittest.TestCase):
    def setUp(self) -> None:
        rng = np.random.RandomState(11)
        self.arr = rng.randint(0, 255, (600, 800, 3), dtype=np.uint8)
        self.image = Image.fromarray(self.arr, "RGB")

    def test_no_spots_is_a_no_op(self) -> None:
        self.assertIs(self.image, apply_retouch(self.image, None))
        self.assertIs(self.image, apply_retouch(self.image, []))

    def test_a_spot_only_touches_its_own_box(self) -> None:
        spots = [{"id": "s1", "kind": "heal", "x": 400.0, "y": 300.0, "r": 30.0,
                  "feather": 50.0, "strength": 0.8}]
        out = np.asarray(apply_retouch(self.image, spots))
        changed = np.any(out != self.arr, axis=2)
        self.assertTrue(changed.any(), "heal did nothing")
        outside = changed.copy()
        outside[300 - 33:300 + 33, 400 - 33:400 + 33] = False
        self.assertEqual(0, int(outside.sum()))

    def test_every_kind_renders(self) -> None:
        spots = [
            {"id": "a", "kind": "heal", "x": 400.0, "y": 300.0, "r": 30.0,
             "feather": 50.0, "strength": 0.8},
            {"id": "b", "kind": "clone", "x": 200.0, "y": 200.0, "sx": 120.0,
             "sy": 120.0, "r": 25.0, "feather": 50.0, "strength": 0.9},
            {"id": "c", "kind": "red_eye", "x": 600.0, "y": 450.0, "r": 20.0,
             "feather": 40.0, "strength": 1.0},
        ]
        out = np.asarray(apply_retouch(self.image, spots))
        self.assertEqual(self.arr.shape, out.shape)
        self.assertFalse(np.array_equal(self.arr, out))

    def test_red_eye_neutralizes_a_red_pupil(self) -> None:
        arr = np.zeros((40, 40, 3), np.uint8)
        arr[15:25, 15:25] = (220, 30, 30)
        fixed = np.asarray(remove_red_eye(Image.fromarray(arr, "RGB"), 20, 20, 12))
        self.assertLess(int(fixed[20, 20, 0]), 100)

    def test_heal_cost_does_not_scale_with_the_frame(self) -> None:
        # The original heal median-filtered the whole image per spot. This is
        # the guard against that regressing.
        big = np.array(
            np.random.RandomState(2).randint(0, 255, (3000, 4000, 3), dtype=np.uint8)
        )
        small = np.array(big[:300, :400])
        big_ms = self._time(lambda: heal_spot_patch(big, 2000.0, 1500.0, 40.0))
        small_ms = self._time(lambda: heal_spot_patch(small, 200.0, 150.0, 40.0))
        self.assertLess(big_ms, max(25.0, small_ms * 6.0), "heal is scaling with frame area")

    def test_heal_cost_does_not_scale_with_the_radius(self) -> None:
        arr = np.array(np.random.RandomState(4).randint(0, 255, (2000, 2000, 3), dtype=np.uint8))
        small_r = self._time(lambda: heal_spot_patch(arr, 900.0, 900.0, 10.0))
        large_r = self._time(lambda: heal_spot_patch(arr, 1100.0, 1100.0, 120.0))
        self.assertLess(large_r, max(30.0, small_r * 12.0), "heal is scaling with radius")

    @staticmethod
    def _time(call, repeats: int = 5) -> float:
        best = float("inf")
        for _ in range(repeats):
            start = time.perf_counter()
            call()
            best = min(best, (time.perf_counter() - start) * 1000.0)
        return best


class RetouchCacheTests(unittest.TestCase):
    """A slider drag must not re-run the spot stack."""

    def setUp(self) -> None:
        self.app = _app()
        from image_triage.editor_render import CpuEditorRenderBackend

        self.backend = CpuEditorRenderBackend()
        self.source = Image.fromarray(
            np.random.RandomState(9).randint(0, 255, (400, 500, 3), dtype=np.uint8), "RGB"
        )
        self.spots = [
            {"id": f"s{i}", "kind": "heal", "x": 100.0 + i * 40, "y": 200.0,
             "r": 20.0, "feather": 50.0, "strength": 0.8}
            for i in range(6)
        ]

    def _retouch(self, spots, base_key=("k",)):
        from image_triage.perf import perf_logger

        return self.backend._retouched_source(self.source, spots, base_key, perf_logger())

    def test_an_unchanged_spot_list_hits_the_cache(self) -> None:
        first = self._retouch(self.spots)
        second = self._retouch(list(self.spots))
        self.assertIs(first, second)

    def test_renaming_a_spot_does_not_invalidate(self) -> None:
        first = self._retouch(self.spots)
        renamed = [dict(spot, id=f"renamed-{spot['id']}") for spot in self.spots]
        self.assertIs(first, self._retouch(renamed))

    def test_appending_a_spot_reuses_the_cached_prefix(self) -> None:
        self._retouch(self.spots)
        cached_prefix = self.backend._retouch_pil
        extended = [*self.spots, {"id": "s99", "kind": "heal", "x": 400.0, "y": 300.0,
                                  "r": 20.0, "feather": 50.0, "strength": 0.8}]
        result = self._retouch(extended)
        self.assertIsNot(cached_prefix, result)
        self.assertEqual(len(extended), len(self.backend._retouch_key[1]))

    def test_moving_a_spot_invalidates(self) -> None:
        first = self._retouch(self.spots)
        moved = [dict(spot) for spot in self.spots]
        moved[0]["x"] += 50.0
        self.assertIsNot(first, self._retouch(moved))

    def test_a_different_base_invalidates(self) -> None:
        first = self._retouch(self.spots, base_key=("a",))
        self.assertIsNot(first, self._retouch(self.spots, base_key=("b",)))

    def test_invalidate_clears_it(self) -> None:
        self._retouch(self.spots)
        self.backend.invalidate()
        self.assertIsNone(self.backend._retouch_pil)


class CropRetouchPersistenceTests(unittest.TestCase):
    VALUES = {
        "crop": (100, 80, 500, 380),
        "crop_angle": 3.5,
        "flip_h": True,
        "crop_aspect": "16:9",
        "exposure": 0.4,
        "retouch": [
            {"id": "spot-001", "kind": "heal", "x": 200.0, "y": 150.0, "r": 30.0,
             "feather": 50.0, "strength": 0.8, "seq": 0},
            {"id": "spot-002", "kind": "clone", "x": 300.0, "y": 250.0, "sx": 260.0,
             "sy": 210.0, "r": 25.0, "feather": 40.0, "strength": 0.9, "seq": 1},
            {"id": "spot-003", "kind": "red_eye", "x": 400.0, "y": 300.0, "r": 15.0,
             "feather": 30.0, "strength": 1.0, "seq": 2},
        ],
    }

    def _ops(self):
        return operations_from_recipe(EditRecipe.from_dict(self.VALUES))

    def test_crop_round_trips(self) -> None:
        back = recipe_from_session({"operations": self._ops()})
        self.assertEqual((100, 80, 500, 380), back.crop)
        self.assertAlmostEqual(3.5, back.crop_angle)
        self.assertTrue(back.flip_h)
        self.assertEqual("16:9", back.crop_aspect)

    def test_spots_round_trip_in_placement_order(self) -> None:
        back = recipe_from_session({"operations": self._ops()})
        self.assertEqual(3, len(back.retouch))
        self.assertEqual(["heal", "clone", "red_eye"], [s["kind"] for s in back.retouch])
        clone = back.retouch[1]
        self.assertAlmostEqual(260.0, clone["sx"])
        self.assertAlmostEqual(210.0, clone["sy"])

    def test_pixel_ops_carry_the_source_coordinate_space(self) -> None:
        # PIXEL_OPERATION_TYPES require it; validate_session rejects them without.
        for op in self._ops():
            if op["type"].startswith(("retouch.", "transform.")):
                with self.subTest(op=op["type"]):
                    self.assertEqual("space-source-full", op.get("coordinateSpaceId"))

    def test_ops_are_emitted_in_renderer_order(self) -> None:
        order = [RENDERER_ORDER.index(op["type"]) for op in self._ops()]
        self.assertEqual(sorted(order), order)

    def test_emitted_ops_validate(self) -> None:
        for op in self._ops():
            with self.subTest(op=op["type"]):
                self.assertEqual([], validate_operation_params(op["id"], op["type"], op["params"]))

    def test_resaving_does_not_duplicate_owned_ops(self) -> None:
        # These op types used to be "preserved" as non-GUI-owned. Now that the
        # GUI writes them, blind preservation would append a second copy on
        # every save — with fresh ids, so no duplicate-id check would catch it.
        ops = self._ops()
        recipe = recipe_from_session({"operations": ops})
        preserved = [op for op in ops if op["type"] not in GUI_GLOBAL_ONLY_OP_TYPES]
        preserved = [op for op in preserved if op["type"].startswith(("retouch.", "transform."))]
        self.assertEqual([], preserved)
        self.assertEqual(len(ops), len(operations_from_recipe(recipe)))

    def test_an_empty_spot_list_writes_nothing(self) -> None:
        self.assertEqual([], operations_from_recipe(EditRecipe.from_dict({"retouch": []})))


class ResetSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _app()
        self.panel = PhotoEditorPanel()
        self.panel._recipe = EditRecipe.from_dict(CropRetouchPersistenceTests.VALUES)

    def tearDown(self) -> None:
        self.panel.close()

    def test_reset_keeps_the_crop_and_the_spots(self) -> None:
        self.panel.reset_recipe()
        self.assertEqual(0.0, self.panel.recipe.exposure)
        self.assertEqual((100, 80, 500, 380), self.panel.recipe.crop)
        self.assertEqual(3, len(self.panel.recipe.retouch))

    def test_reset_crop_clears_geometry_only(self) -> None:
        self.panel.reset_crop()
        self.assertIsNone(self.panel.recipe.crop)
        self.assertEqual(0.0, self.panel.recipe.crop_angle)
        self.assertFalse(self.panel.recipe.flip_h)
        self.assertEqual(3, len(self.panel.recipe.retouch))
        self.assertAlmostEqual(0.4, self.panel.recipe.exposure)

    def test_clearing_spots_leaves_the_others(self) -> None:
        self.panel.clear_retouch_spots(("heal", "clone"))
        kinds = [spot["kind"] for spot in self.panel.recipe.retouch or []]
        self.assertEqual(["red_eye"], kinds)

    def test_mask_session_write_keeps_unsaved_crop_and_retouch_in_memory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.jpg"
            Image.new("RGB", (640, 480), "gray").save(source)
            self.panel.set_image(source)
            session_path, session = self.panel._ensure_session()
            unsaved_recipe = EditRecipe.from_dict(CropRetouchPersistenceTests.VALUES)
            self.panel._recipe = unsaved_recipe

            self.panel._write_session(session, "Mask updated")

            self.assertIs(unsaved_recipe, self.panel.recipe)
            self.assertEqual((100, 80, 500, 380), self.panel.recipe.crop)
            self.assertEqual(3, len(self.panel.recipe.retouch or ()))
            self.assertIsNone(recipe_from_session(load_session(session_path)).crop)


class OverlayStackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _app()
        self.label = QLabel()
        self.label.resize(400, 300)
        self.stack = OverlayStack(
            {"mask": MaskOverlay(), "crop": CropOverlay(), "retouch": RetouchOverlay()}
        )
        self.stack.attach(self.label)

    def test_exactly_one_overlay_takes_the_mouse(self) -> None:
        from PySide6.QtCore import Qt

        for active in ("mask", "crop", "retouch", None):
            with self.subTest(active=active):
                self.stack.set_active(active)
                live = [
                    name
                    for name, overlay in self.stack
                    if not overlay.testAttribute(
                        Qt.WidgetAttribute.WA_TransparentForMouseEvents
                    )
                ]
                self.assertEqual([active] if active else [], live)

    def test_stacking_order_is_fixed_not_last_attached(self) -> None:
        # attach_to no longer raises on its own; the stack raises in ORDER, so
        # the crop box and spot pins always draw over the mask tint.
        self.assertEqual(("mask", "crop", "retouch"), OverlayStack.ORDER)
        self.stack.attach(self.label)
        siblings = [c for c in self.label.children() if isinstance(c, CanvasOverlay)]
        self.assertEqual(3, len(siblings))

    def test_overlays_share_one_view_transform(self) -> None:
        view = ViewTransform(source_size=(800, 600), crop=(200, 150, 600, 450))
        self.stack.set_view_transform(view)
        for name, overlay in self.stack:
            with self.subTest(overlay=name):
                self.assertIs(view, overlay._view)


class OverlayMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _app()
        self.label = QLabel()
        self.label.resize(400, 300)
        self.overlay = MaskOverlay()
        self.overlay.attach_to(self.label)
        self.overlay._source_size = (800, 600)

    def test_without_a_crop_the_mapping_is_the_old_scale_only_one(self) -> None:
        self.assertEqual((0.5, 0.5), self.overlay._scales())
        point = self.overlay._to_display(400, 300)
        self.assertAlmostEqual(200.0, point.x())
        self.assertAlmostEqual(150.0, point.y())
        self.assertEqual((400.0, 300.0), self.overlay._to_source(QPointF(200, 150)))

    def test_a_crop_offsets_the_mapping(self) -> None:
        self.overlay.set_view_transform(
            ViewTransform(source_size=(800, 600), crop=(200, 150, 600, 450))
        )
        self.assertEqual((1.0, 1.0), self.overlay._scales())
        origin = self.overlay._to_display(200, 150)
        self.assertAlmostEqual(0.0, origin.x())
        self.assertAlmostEqual(0.0, origin.y())
        self.assertEqual((200.0, 150.0), self.overlay._to_source(QPointF(0, 0)))


class CropOverlayInteractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _app()
        self.label = QLabel()
        self.label.resize(400, 300)
        self.overlay = CropOverlay()
        self.overlay.attach_to(self.label)
        self.overlay.set_state(
            interactive=True, crop=None, source_size=(400, 300), aspect=None
        )

    def test_no_crop_starts_as_the_whole_frame(self) -> None:
        self.assertEqual((0, 0, 400, 300), self.overlay.crop_rect())

    def test_dragging_an_edge_reports_a_new_crop(self) -> None:
        seen: list[dict] = []
        self.overlay.crop_changed.connect(seen.append)
        self.overlay._drag_mode = "l"
        self.overlay._drag_origin = QPointF(0, 150)
        self.overlay._drag_start_crop = (0.0, 0.0, 400.0, 300.0)
        self.overlay._apply_drag(QPointF(80, 150))
        self.assertTrue(seen)
        left, top, right, bottom = seen[-1]["crop"]
        self.assertAlmostEqual(80, left, delta=1)
        self.assertEqual((0, 400, 300), (top, right, bottom))

    def test_a_crop_can_never_collapse(self) -> None:
        self.overlay._drag_mode = "l"
        self.overlay._drag_origin = QPointF(0, 150)
        self.overlay._drag_start_crop = (0.0, 0.0, 400.0, 300.0)
        self.overlay._apply_drag(QPointF(4000, 150))
        left, _top, right, _bottom = self.overlay.crop_rect()
        self.assertGreaterEqual(right - left, 16)

    def test_a_locked_aspect_is_honoured(self) -> None:
        self.overlay.set_state(
            interactive=True, crop=(0, 0, 400, 300), source_size=(400, 300), aspect=1.0
        )
        self.overlay._drag_mode = "br"
        self.overlay._drag_origin = QPointF(400, 300)
        self.overlay._drag_start_crop = (0.0, 0.0, 400.0, 300.0)
        self.overlay._apply_drag(QPointF(200, 260))
        left, top, right, bottom = self.overlay.crop_rect()
        self.assertAlmostEqual(1.0, (right - left) / (bottom - top), places=1)


class RetouchOverlayInteractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _app()
        self.label = QLabel()
        self.label.resize(400, 300)
        self.overlay = RetouchOverlay()
        self.overlay.attach_to(self.label)

    def _state(self, spots=(), tool=None, selected=None):
        self.overlay.set_state(
            interactive=True, spots=list(spots), tool=tool, selected_id=selected,
            brush={"size": 40.0, "feather": 50.0, "strength": 80.0},
            source_size=(800, 600),
        )

    def test_clicking_with_a_tool_armed_adds_a_spot(self) -> None:
        self._state(tool="heal")
        added: list[dict] = []
        self.overlay.spot_added.connect(added.append)
        event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(100, 75),
            QPointF(100, 75),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(self.overlay, event)
        self.assertEqual(1, len(added))
        self.assertEqual("heal", added[0]["kind"])
        self.assertEqual((200.0, 150.0), (added[0]["x"], added[0]["y"]))

    def test_dragging_a_clone_moves_its_source_with_it(self) -> None:
        spot = {"id": "s1", "kind": "clone", "x": 200.0, "y": 150.0, "sx": 150.0,
                "sy": 100.0, "r": 30.0}
        self._state(spots=[spot], selected="s1")
        moves: list[tuple] = []
        self.overlay.spot_moved.connect(lambda i, c: moves.append((i, c)))
        self.overlay._drag_id = "s1"
        self.overlay._drag_part = "move"
        self.overlay._drag_origin = QPointF(100, 75)
        self.overlay._drag_start = dict(spot)
        self.overlay._apply_drag(QPointF(120, 75))
        self.assertTrue(moves)
        changes = moves[-1][1]
        self.assertAlmostEqual(240.0, changes["x"], delta=1.0)
        self.assertAlmostEqual(190.0, changes["sx"], delta=1.0)

    def test_dragging_the_source_leaves_the_target(self) -> None:
        spot = {"id": "s1", "kind": "clone", "x": 200.0, "y": 150.0, "sx": 150.0,
                "sy": 100.0, "r": 30.0}
        self._state(spots=[spot], selected="s1")
        moves: list[tuple] = []
        self.overlay.spot_moved.connect(lambda i, c: moves.append((i, c)))
        self.overlay._drag_id = "s1"
        self.overlay._drag_part = "source"
        self.overlay._drag_origin = QPointF(75, 50)
        self.overlay._drag_start = dict(spot)
        self.overlay._apply_drag(QPointF(95, 50))
        self.assertNotIn("x", moves[-1][1])
        self.assertAlmostEqual(190.0, moves[-1][1]["sx"], delta=1.0)


class PanelToolArbitrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _app()
        self.panel = PhotoEditorPanel()
        self.panel._source_path = Path("fake.jpg")

    def tearDown(self) -> None:
        self.panel.close()

    def test_each_page_claims_the_right_overlay(self) -> None:
        expected = {
            self.panel.PAGE_ADJUST: None,
            self.panel.PAGE_CROP: "crop",
            self.panel.PAGE_REMOVE: "retouch",
            self.panel.PAGE_RED_EYE: "retouch",
            self.panel.PAGE_MASKS: "mask",
            self.panel.PAGE_BACKGROUND: None,
            self.panel.PAGE_LENS_BLUR: None,
            self.panel.PAGE_PRESETS: None,
        }
        for page, tool in expected.items():
            with self.subTest(page=page):
                self.panel._set_editor_page(page)
                self.assertEqual(tool, self.panel.active_canvas_tool())

    def test_the_crop_is_only_bypassed_on_the_crop_page(self) -> None:
        for page, *_rest in self.panel.RAIL_TOOLS:
            self.panel._set_editor_page(page)
            with self.subTest(page=page):
                self.assertEqual(
                    page == self.panel.PAGE_CROP,
                    self.panel.view_render_spec()["bypass_crop"],
                )

    def test_no_image_means_no_tool(self) -> None:
        self.panel._source_path = None
        self.panel._set_editor_page(self.panel.PAGE_CROP)
        self.assertIsNone(self.panel.active_canvas_tool())

    def test_overlay_states_always_covers_every_overlay(self) -> None:
        for page, *_rest in self.panel.RAIL_TOOLS:
            self.panel._set_editor_page(page)
            with self.subTest(page=page):
                self.assertEqual({"mask", "crop", "retouch"}, set(self.panel.overlay_states()))

    def test_point_color_picker_claims_the_mask_overlay_without_showing_a_mask(self) -> None:
        self.panel._set_editor_page(self.panel.PAGE_ADJUST)
        self.panel._set_point_color_sample_armed(True)
        state = self.panel.mask_overlay_state()
        self.assertEqual("mask", self.panel.active_canvas_tool())
        self.assertTrue(state["interactive"])
        self.assertEqual("color-range", state["create_mode"])
        self.assertFalse(state["scene_pick"])
        self.assertFalse(state["show_overlay"])
        self.assertIsNone(state["params"])

    def test_leaving_adjust_disarms_point_color_picker(self) -> None:
        self.panel._set_editor_page(self.panel.PAGE_ADJUST)
        self.panel._set_point_color_sample_armed(True)
        self.panel._set_editor_page(self.panel.PAGE_CROP)
        self.assertFalse(self.panel._point_color_sample_armed)
        self.assertEqual("crop", self.panel.active_canvas_tool())


class PointColorOverlayInteractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = _app()
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "sample.png"
        Image.new("RGB", (8, 8), (0, 255, 0)).save(self.path)
        self.panel = PhotoEditorPanel()
        self.panel.set_image(self.path)
        self.panel._set_point_color_sample_armed(True)
        self.label = QLabel()
        self.label.resize(80, 80)
        self.overlay = MaskOverlay()
        self.overlay.attach_to(self.label)
        self.overlay.set_state(**self.panel.mask_overlay_state())
        self.overlay.source_clicked.connect(self.panel.handle_overlay_source_clicked)

    def tearDown(self) -> None:
        self.panel.close()
        self.temp.cleanup()

    def test_canvas_click_samples_the_photo(self) -> None:
        event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(40, 40),
            QPointF(40, 40),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(self.overlay, event)
        self.assertFalse(self.panel._point_color_sample_armed)
        sample = self.panel.recipe.point_colors[0]
        self.assertEqual({"h": 85, "s": 255, "l": 255}, {
            key: sample[key] for key in ("h", "s", "l")
        })


if __name__ == "__main__":
    unittest.main()
