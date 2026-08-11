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

    def test_person_instances_replace_the_merged_people_region(self) -> None:
        # Base index with a merged people region (two blobs) + sky.
        sky = np.zeros((100, 200), np.uint8)
        sky[0:20, :] = 255
        people = np.zeros((100, 200), np.uint8)
        people[40:90, 20:60] = 255
        people[40:90, 140:180] = 255
        for name, arr in (("sky", sky), ("people", people)):
            Image.fromarray(arr, "L").save(self.dir / f"{name}.png")
        paths = {"sky": self.dir / "sky.png", "people": self.dir / "people.png"}
        base = SceneRegionIndex.from_mask_paths(SOURCE_SIZE, paths, ("sky", "people"))
        assert base is not None
        self.assertEqual("people", base.category_at(40, 65))

        left = np.zeros((100, 200), bool)
        left[40:90, 20:60] = True
        right = np.zeros((100, 200), bool)
        right[40:90, 140:180] = True
        combined = base.with_person_instances(
            [(left, (0.20, 0.65)), (right, (0.80, 0.65))]
        )
        self.assertNotIn("people", combined.categories)
        self.assertIn("person:0", combined.categories)
        self.assertIn("person:1", combined.categories)
        self.assertEqual("person:0", combined.category_at(40, 65))
        self.assertEqual("person:1", combined.category_at(160, 65))
        self.assertEqual("Person", combined.label_for("person:0"))
        self.assertEqual((0.80, 0.65), combined.seed_for("person:1"))
        self.assertTrue(SceneRegionIndex.is_person("person:0"))
        self.assertFalse(SceneRegionIndex.is_person("sky"))
        self.assertEqual("sky", combined.category_at(5, 10))  # other regions kept
        # No instances -> unchanged index.
        self.assertIs(base, base.with_person_instances([]))


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

    def test_scene_pick_runs_only_on_the_new_mask_pane(self) -> None:
        panel = self._masks_tab_panel()
        self.assertFalse(panel.mask_overlay_state()["scene_pick"])
        panel._show_mask_pane(panel.MASK_PANE_CREATE)
        self.assertTrue(panel.mask_overlay_state()["scene_pick"])
        panel._show_mask_pane(panel.MASK_PANE_WORK)
        self.assertFalse(panel.mask_overlay_state()["scene_pick"])

    def test_an_armed_tool_disables_scene_pick(self) -> None:
        panel = self._masks_tab_panel()
        panel._show_mask_pane(panel.MASK_PANE_CREATE)
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

    def test_analysis_shows_a_busy_message_on_the_new_mask_pane(self) -> None:
        from image_triage.ui.photo_editor_panel import PhotoEditorPanel

        panel = PhotoEditorPanel()
        panel._source_path = Path("photo.jpg")
        panel.editor_stack.setCurrentIndex(1)
        panel._show_mask_pane(panel.MASK_PANE_CREATE)
        self.assertIsNone(panel.mask_overlay_state()["busy_message"])

        panel._semantic_mask_task = object()  # analysis running
        self.assertEqual(
            "Analyzing photo...", panel.mask_overlay_state()["busy_message"]
        )
        panel._semantic_mask_task = None
        panel._people_instance_task = object()  # splitting people
        self.assertEqual(
            "Finding people...", panel.mask_overlay_state()["busy_message"]
        )
        # A click-to-select session owns the canvas — no analysis spinner then.
        panel._prompt_session_active = True
        self.assertIsNone(panel.mask_overlay_state()["busy_message"])
        panel._prompt_session_active = False
        panel._people_instance_task = None
        self.assertIsNone(panel.mask_overlay_state()["busy_message"])
        panel.close()

    def test_picking_a_person_instance_routes_to_click_to_select(self) -> None:
        from image_triage.ui.photo_editor_panel import PhotoEditorPanel
        from image_triage.ui.scene_regions import SceneRegionIndex

        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            people = np.zeros((100, 200), np.uint8)
            people[40:90, 140:180] = 255
            Image.fromarray(people, "L").save(directory / "people.png")
            base = SceneRegionIndex.from_mask_paths(
                SOURCE_SIZE, {"people": directory / "people.png"}, ("people",)
            )
            assert base is not None
            blob = np.zeros((100, 200), bool)
            blob[40:90, 140:180] = True
            index = base.with_person_instances([(blob, (0.80, 0.65))])

            panel = PhotoEditorPanel()
            panel._source_path = Path("photo.jpg")
            panel._scene_index = index
            started: list[tuple[list, dict]] = []
            panel._start_prompt_mask_task = lambda pts, **kw: started.append((pts, kw))  # type: ignore[assignment]
            panel.handle_overlay_scene_picked("person:0")
            self.assertEqual(1, len(started))
            points, kwargs = started[0]
            self.assertEqual([(0.80, 0.65)], points)
            self.assertTrue(kwargs["refine"])  # people get BiRefNet edge refine
            panel.close()

    def test_click_to_select_takes_over_scene_pick_and_normalizes_clicks(self) -> None:
        from image_triage.ui.photo_editor_panel import PhotoEditorPanel

        panel = self._masks_tab_panel()
        panel._show_mask_pane(panel.MASK_PANE_CREATE)
        # Set the flag directly to avoid the warm task side effect.
        panel._point_select_active = True
        state = panel.mask_overlay_state()
        self.assertTrue(state["point_pick"])
        self.assertFalse(state["scene_pick"])  # click-select takes over

        panel._mask_source_size = lambda: (200, 100)  # type: ignore[assignment]
        started: list[list[tuple[float, float]]] = []
        panel._start_prompt_mask_task = lambda pts, **kw: started.append(pts)  # type: ignore[assignment]
        panel.handle_overlay_point_picked(100.0, 50.0)  # dead center
        self.assertEqual([[(0.5, 0.5)]], started)

        # A click with the mode off is ignored.
        panel._point_select_active = False
        started.clear()
        panel.handle_overlay_point_picked(10.0, 10.0)
        self.assertEqual([], started)
        panel.close()

    def test_click_to_select_session_builds_one_mask_from_many_clicks(self) -> None:
        """The 'Click to Select (AI)' button opens the touch-up window and each
        click adds an 'add' component to the SAME mask; OK returns to Work."""
        from image_triage import prompt_masks
        from image_triage.ui.photo_editor_panel import PhotoEditorPanel

        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            photo = directory / "photo.png"
            Image.new("RGB", SOURCE_SIZE, "gray").save(photo)

            def _fake_ensure(source_path, points_norm, labels=None, refine=False, **kw):
                mask_png = directory / f"m{len(list(directory.glob('m*.png')))}.png"
                Image.new("L", SOURCE_SIZE, 255).save(mask_png)
                return prompt_masks.PromptMaskResult(
                    source_path=Path(source_path),
                    source_size=SOURCE_SIZE,
                    mask_path=mask_png,
                    bounds=(0, 0, *SOURCE_SIZE),
                    coverage=0.5,
                    model_id="facebook/sam2.1-hiera-tiny",
                    model_version="1",
                    weights_hash="abc",
                )

            original = prompt_masks.ensure_prompt_mask
            prompt_masks.ensure_prompt_mask = _fake_ensure
            try:
                panel = PhotoEditorPanel()
                panel.set_image(photo)
                panel.editor_stack.setCurrentIndex(1)
                panel._show_mask_pane(panel.MASK_PANE_CREATE)
                panel._mask_source_size = lambda: SOURCE_SIZE

                # Enter the session via the button toggle.
                panel._set_point_select_active(True)
                self.assertTrue(panel._prompt_session_active)
                self.assertFalse(panel._mask_touchup_page.isHidden())
                self.assertTrue(panel.editor_stack.isHidden())
                # point_pick stays live even though the New Mask pane is hidden.
                self.assertTrue(panel.mask_overlay_state()["point_pick"])

                def _click(x, y, on_person):
                    panel._click_is_on_person = lambda nx, ny: on_person
                    panel.handle_overlay_point_picked(x, y)
                    self.assertIsNotNone(panel._prompt_mask_task)
                    panel._semantic_mask_pool.waitForDone(5000)
                    QApplication.instance().processEvents()

                # First click makes the root mask; the touch-up targets it.
                _click(100.0, 50.0, on_person=False)
                root = panel._prompt_session_root_id
                self.assertIsNotNone(root)
                self.assertEqual(panel._mask_touchup_mask_id, root)
                self.assertFalse(panel._prompt_meta[root]["refined"])

                # A person click adds a child to the SAME group, auto-refined.
                _click(50.0, 25.0, on_person=True)
                members = panel._group_members(root)
                self.assertEqual(len(members), 2)
                child = next(m for m in members if m.get("id") != root)
                self.assertEqual(child.get("parentId"), root)
                self.assertTrue(panel._prompt_meta[child["id"]]["refined"])
                self.assertEqual(panel._mask_touchup_mask_id, root)

                # Clear deletes the whole selection (not a hide) and resets the
                # session; a later click starts fresh instead of resurfacing it.
                panel._toggle_mask_touchup_clear()
                subject_selects = [
                    m for m in panel._session.get("masks", [])
                    if m.get("type") == "subject-select"
                ]
                self.assertEqual(subject_selects, [])
                self.assertIsNone(panel._prompt_session_root_id)
                self.assertEqual(panel._prompt_meta, {})
                self.assertFalse(panel.mask_touchup_clear_button.isEnabled())
                self.assertTrue(panel._prompt_session_active)
                _click(150.0, 75.0, on_person=False)
                self.assertEqual(
                    len([
                        m for m in panel._session.get("masks", [])
                        if m.get("type") == "subject-select"
                    ]),
                    1,
                )
                root = panel._prompt_session_root_id
                self.assertIsNotNone(root)

                # Refine Edges is offered for the un-refined root, then clears it.
                self.assertTrue(panel.mask_touchup_refine_button.isEnabled())
                panel._refine_touchup_session()
                for _ in range(10):
                    if panel._refine_active_task is None and not panel._refine_queue:
                        break
                    panel._semantic_mask_pool.waitForDone(5000)
                    QApplication.instance().processEvents()
                self.assertTrue(panel._prompt_meta[root]["refined"])
                self.assertFalse(panel.mask_touchup_refine_button.isEnabled())

                # OK tears the session down and returns to the Work pane.
                panel._finish_mask_touchup(accepted=True)
                self.assertFalse(panel._prompt_session_active)
                self.assertIsNone(panel._prompt_session_root_id)
                self.assertFalse(panel._point_select_active)
                self.assertEqual(panel.mask_stack.currentIndex(), panel.MASK_PANE_WORK)
                self.assertFalse(panel.point_select_button.isChecked())
                panel.close()
            finally:
                prompt_masks.ensure_prompt_mask = original


if __name__ == "__main__":
    unittest.main()
