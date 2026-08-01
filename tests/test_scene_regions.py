from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget

from image_triage.ui.scene_regions import SceneRegionIndex

SOURCE_SIZE = (200, 100)


def _write_scene(directory: Path) -> dict[str, Path]:
    """A 200x100 frame: sky across the top half, water across the bottom."""
    paths: dict[str, Path] = {}
    for name, rows in (("sky", slice(0, 50)), ("water", slice(50, 100))):
        plane = np.zeros((100, 200), np.uint8)
        plane[rows, :] = 255
        path = directory / f"{name}.png"
        Image.fromarray(plane, "L").save(path)
        paths[name] = path
    return paths


class SceneRegionIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.paths = _write_scene(self.dir)
        self.index = SceneRegionIndex.from_mask_paths(
            SOURCE_SIZE, self.paths, ("sky", "water")
        )
        assert self.index is not None

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_lookup_returns_the_region_under_the_point(self) -> None:
        self.assertEqual("sky", self.index.category_at(100, 10))
        self.assertEqual("water", self.index.category_at(100, 90))

    def test_points_outside_the_frame_match_nothing(self) -> None:
        self.assertIsNone(self.index.category_at(-5, 10))
        self.assertIsNone(self.index.category_at(100, 500))

    def test_uncovered_areas_match_nothing(self) -> None:
        sparse = SceneRegionIndex.from_mask_paths(
            SOURCE_SIZE, {"sky": self.paths["sky"]}, ("sky",)
        )
        assert sparse is not None
        self.assertEqual("sky", sparse.category_at(100, 10))
        self.assertIsNone(sparse.category_at(100, 90))

    def test_overlapping_regions_go_to_the_stronger_one(self) -> None:
        faint = np.full((100, 200), 200, np.uint8)
        faint_path = self.dir / "trees.png"
        Image.fromarray(faint, "L").save(faint_path)
        index = SceneRegionIndex.from_mask_paths(
            SOURCE_SIZE,
            {**self.paths, "trees": faint_path},
            ("sky", "water", "trees"),
        )
        assert index is not None
        # Sky is 255 where trees is 200, so the stronger claim wins.
        self.assertEqual("sky", index.category_at(100, 10))

    def test_empty_or_missing_masks_produce_no_index(self) -> None:
        self.assertIsNone(SceneRegionIndex.from_mask_paths(SOURCE_SIZE, {}, ()))
        blank = self.dir / "blank.png"
        Image.fromarray(np.zeros((100, 200), np.uint8), "L").save(blank)
        self.assertIsNone(
            SceneRegionIndex.from_mask_paths(SOURCE_SIZE, {"sky": blank}, ("sky",))
        )

    def test_labels_are_human_readable(self) -> None:
        self.assertEqual("Sky", self.index.label_for("sky"))

    def test_highlight_matches_the_requested_display_size(self) -> None:
        image = self.index.highlight("sky", 400, 200)
        assert image is not None
        self.assertEqual((400, 200), (image.width(), image.height()))
        self.assertGreater(image.pixelColor(200, 20).alpha(), 0)
        self.assertEqual(0, image.pixelColor(200, 180).alpha())
        self.assertIsNone(self.index.highlight("mountains", 400, 200))

    def test_highlight_is_cached_per_size(self) -> None:
        first = self.index.highlight("sky", 400, 200)
        self.assertIs(first, self.index.highlight("sky", 400, 200))


class SceneHoverOverlayTests(unittest.TestCase):
    def setUp(self) -> None:
        from image_triage.ui.mask_overlay import MaskOverlay

        self.app = QApplication.instance() or QApplication([])
        self._tmp = TemporaryDirectory()
        directory = Path(self._tmp.name)
        self.index = SceneRegionIndex.from_mask_paths(
            SOURCE_SIZE, _write_scene(directory), ("sky", "water")
        )
        self.host = QWidget()
        self.host.resize(400, 200)
        self.overlay = MaskOverlay(self.host)
        self.overlay.setGeometry(0, 0, 400, 200)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _arm(self, **overrides) -> None:
        state = {
            "interactive": True,
            "show_overlay": True,
            "create_mode": None,
            "mask_type": None,
            "params": None,
            "source_size": SOURCE_SIZE,
            "scene_index": self.index,
            "scene_pick": True,
        }
        state.update(overrides)
        self.overlay.set_state(**state)

    def _move_to(self, x: float, y: float) -> None:
        event = QMouseEvent(
            QMouseEvent.Type.MouseMove,
            QPointF(x, y),
            Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.overlay.mouseMoveEvent(event)

    def _click_at(self, x: float, y: float) -> None:
        event = QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            QPointF(x, y),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        self.overlay.mousePressEvent(event)

    def test_hovering_tracks_the_region_under_the_cursor(self) -> None:
        self._arm()
        self._move_to(200, 20)
        self.assertEqual("sky", self.overlay._scene_hover)
        self._move_to(200, 180)
        self.assertEqual("water", self.overlay._scene_hover)

    def test_hover_normalizes_full_source_coordinates_to_the_preview_index(self) -> None:
        # Segmentation runs on a bounded preview, while editor geometry uses
        # full source dimensions. At y=60 this is 30% down either coordinate
        # space and must remain sky; passing 60 directly into the 100px index
        # incorrectly lands in water.
        self._arm(source_size=(400, 200))
        self._move_to(200, 60)
        self.assertEqual("sky", self.overlay._scene_hover)
        self._move_to(200, 140)
        self.assertEqual("water", self.overlay._scene_hover)

    def test_scene_pick_takes_mouse_events_with_no_mask_present(self) -> None:
        # Without this the overlay stays mouse-transparent and hovering an
        # empty canvas — the whole point — never fires.
        self._arm()
        self.assertFalse(
            self.overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        )

    def test_clicking_a_region_emits_its_category(self) -> None:
        self._arm()
        picked: list[str] = []
        self.overlay.scene_region_picked.connect(picked.append)
        self._move_to(200, 20)
        self._click_at(200, 20)
        self.assertEqual(["sky"], picked)

    def test_an_armed_create_tool_wins_over_scene_picking(self) -> None:
        self._arm(create_mode="radial")
        picked: list[str] = []
        self.overlay.scene_region_picked.connect(picked.append)
        self._move_to(200, 20)
        self._click_at(200, 20)
        self.assertEqual([], picked)
        self.assertIsNone(self.overlay._scene_hover)

    def test_selected_mask_handles_win_over_scene_picking(self) -> None:
        # Hovering the middle of a radial should offer to move it, not to
        # replace it with a sky mask.
        self._arm(
            mask_type="radial",
            params={
                "cx": 100, "cy": 25, "rx": 40, "ry": 20,
                "angle": 0.0, "feather": 50.0, "density": 100.0, "invert": False,
            },
        )
        self._move_to(200, 50)  # display centre == source (100, 25)
        self.assertIsNone(self.overlay._scene_hover)
        self._move_to(20, 20)   # well outside the ellipse, still sky
        self.assertEqual("sky", self.overlay._scene_hover)

    def test_leaving_the_canvas_clears_the_hover(self) -> None:
        from PySide6.QtCore import QEvent

        self._arm()
        self._move_to(200, 20)
        self.overlay.leaveEvent(QEvent(QEvent.Type.Leave))
        self.assertIsNone(self.overlay._scene_hover)

    def test_turning_scene_pick_off_clears_the_hover(self) -> None:
        self._arm()
        self._move_to(200, 20)
        self._arm(scene_pick=False)
        self.assertIsNone(self.overlay._scene_hover)

    def test_painting_a_hover_with_no_mask_does_not_crash(self) -> None:
        from PySide6.QtGui import QPixmap

        self._arm()
        self._move_to(200, 20)
        pixmap = QPixmap(400, 200)
        pixmap.fill()
        self.overlay.render(pixmap)


class _FakeResult:
    """Stands in for SemanticMaskResult without needing onnxruntime."""

    def __init__(self, source_path, source_size, mask_paths, detected) -> None:
        self.source_path = source_path
        self.source_size = source_size
        self.mask_paths = mask_paths
        self.detected_categories = detected


class ScenePanelWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])

    def test_scene_pick_is_off_until_the_masks_tab_is_open(self) -> None:
        from image_triage.ui.photo_editor_panel import PhotoEditorPanel

        panel = PhotoEditorPanel()
        self.assertFalse(panel.mask_overlay_state()["scene_pick"])

    def _masks_tab_panel(self):
        from image_triage.ui.photo_editor_panel import PhotoEditorPanel

        panel = PhotoEditorPanel()
        panel._source_path = Path("photo.jpg")
        panel.editor_stack.setCurrentIndex(1)
        return panel

    def test_scene_pick_runs_on_both_mask_panes(self) -> None:
        panel = self._masks_tab_panel()
        self.assertTrue(panel.mask_overlay_state()["scene_pick"])
        panel._show_mask_pane(panel.MASK_PANE_CREATE)
        self.assertTrue(panel.mask_overlay_state()["scene_pick"])
        panel._show_mask_pane(panel.MASK_PANE_WORK)
        self.assertTrue(panel.mask_overlay_state()["scene_pick"])

    def test_an_armed_tool_disables_scene_pick(self) -> None:
        panel = self._masks_tab_panel()
        self.assertTrue(panel.mask_overlay_state()["scene_pick"])
        panel._mask_create_mode = "radial"
        self.assertFalse(panel.mask_overlay_state()["scene_pick"])
        panel._mask_create_mode = None
        panel._brush_paint_mode = "add"
        self.assertFalse(panel.mask_overlay_state()["scene_pick"])

    def test_the_index_is_built_off_the_ui_thread(self) -> None:
        from image_triage.ui.photo_editor_panel import PhotoEditorPanel
        from image_triage.ui.scene_regions import SceneIndexTask

        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            photo = directory / "photo.jpg"
            Image.new("RGB", SOURCE_SIZE, "gray").save(photo)
            paths = _write_scene(directory)

            panel = PhotoEditorPanel()
            panel._source_path = photo
            panel._semantic_mask_result = _FakeResult(
                photo.resolve(), SOURCE_SIZE, paths, ("sky", "water")
            )
            # First ask only schedules the build; hovering stays inert.
            self.assertIsNone(panel._ensure_scene_index())
            task = panel._scene_index_task
            self.assertIsInstance(task, SceneIndexTask)
            # Asking again must not queue a second build.
            self.assertIsNone(panel._ensure_scene_index())
            self.assertIs(task, panel._scene_index_task)

            panel._semantic_mask_pool.waitForDone(5000)
            self.app.processEvents()
            index = panel._ensure_scene_index()
            self.assertIsNotNone(index)
            self.assertEqual("sky", index.category_at(100, 10))

    def test_a_stale_build_is_dropped_when_the_image_changed(self) -> None:
        from image_triage.ui.photo_editor_panel import PhotoEditorPanel

        panel = PhotoEditorPanel()
        panel._source_path = Path("current.jpg")
        panel._handle_scene_index_ready("other.jpg", object())
        self.assertIsNone(panel._scene_index)
        self.assertIsNone(panel._scene_index_source)

    def test_picking_a_region_routes_to_the_semantic_mask_request(self) -> None:
        from image_triage.ui.photo_editor_panel import PhotoEditorPanel

        panel = PhotoEditorPanel()
        requested: list[str] = []
        panel.request_semantic_mask = requested.append  # type: ignore[assignment]
        panel.handle_overlay_scene_picked("sky")
        self.assertEqual(["sky"], requested)


if __name__ == "__main__":
    unittest.main()
