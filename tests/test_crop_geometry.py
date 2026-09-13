"""The crop box must stay on the photo, and keep its shape while straightening.

A straightened photo is a rotated quad inside the rendered frame; the wedges
around it are blank. These are the rules the on-canvas crop box obeys.
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from image_triage.editor_geometry import (
    ViewTransform,
    fit_rect_in_quad,
    limit_rect_to_quad,
    quad_contains,
    rect_in_quad,
)


def _view(angle: float = 0.0) -> ViewTransform:
    # The Crop tool's transform: the whole straightened frame, no crop applied.
    return ViewTransform(source_size=(400, 300), angle=angle)


class ImageQuadTests(unittest.TestCase):
    def test_without_an_angle_the_quad_is_the_frame(self) -> None:
        quad = _view().image_quad()
        self.assertEqual(
            [(0.0, 0.0), (400.0, 0.0), (400.0, 300.0), (0.0, 300.0)],
            [(round(x, 6), round(y, 6)) for x, y in quad],
        )

    def test_a_straighten_leaves_blank_wedges_in_the_corners(self) -> None:
        view = _view(12.0)
        quad = view.image_quad()
        frame_w, frame_h = view.frame_size()
        # The frame grew to hold the rotation, and its corners are now blank.
        self.assertGreater(frame_w, 400)
        self.assertFalse(quad_contains(quad, 0.0, 0.0))
        self.assertTrue(quad_contains(quad, frame_w / 2.0, frame_h / 2.0))


class CropRectMappingTests(unittest.TestCase):
    def test_the_box_keeps_its_size_at_every_angle(self) -> None:
        # The regression: mapping two opposite corners made the box change
        # shape (and break its locked ratio) as the angle turned.
        crop = (100.0, 75.0, 300.0, 225.0)
        for angle in (0.0, 5.0, 17.5, -33.0, 45.0):
            with self.subTest(angle=angle):
                left, top, right, bottom = _view(angle).frame_rect_for_crop(crop)
                self.assertAlmostEqual(200.0, right - left, places=6)
                self.assertAlmostEqual(150.0, bottom - top, places=6)

    def test_frame_and_stored_forms_round_trip(self) -> None:
        crop = (100.0, 75.0, 300.0, 225.0)
        for angle in (0.0, 9.0, -21.0):
            with self.subTest(angle=angle):
                view = _view(angle)
                back = view.crop_for_frame_rect(view.frame_rect_for_crop(crop))
                for expected, actual in zip(crop, back):
                    self.assertAlmostEqual(expected, actual, places=6)


class ContainmentTests(unittest.TestCase):
    def test_a_straighten_shrinks_a_full_frame_box_onto_the_photo(self) -> None:
        view = _view(15.0)
        quad = view.image_quad()
        frame_w, frame_h = view.frame_size()
        full = (0.0, 0.0, float(frame_w), float(frame_h))
        self.assertFalse(rect_in_quad(quad, full))
        fitted = fit_rect_in_quad(quad, full)
        self.assertTrue(rect_in_quad(quad, fitted))
        # Centre and aspect survive; only size gives way.
        self.assertAlmostEqual(frame_w / 2.0, (fitted[0] + fitted[2]) / 2.0, places=4)
        self.assertAlmostEqual(frame_h / 2.0, (fitted[1] + fitted[3]) / 2.0, places=4)
        self.assertAlmostEqual(
            frame_w / frame_h, (fitted[2] - fitted[0]) / (fitted[3] - fitted[1]), places=4
        )

    def test_a_drag_that_stays_on_the_photo_is_untouched(self) -> None:
        quad = _view(10.0).image_quad()
        start = (150.0, 120.0, 250.0, 200.0)
        desired = (160.0, 130.0, 260.0, 210.0)
        self.assertEqual(desired, limit_rect_to_quad(quad, start, desired))

    def test_a_drag_off_the_photo_stops_at_the_edge(self) -> None:
        view = _view(10.0)
        quad = view.image_quad()
        start = (150.0, 120.0, 250.0, 200.0)
        desired = (-500.0, -500.0, 250.0, 200.0)  # yanked into the blank corner
        limited = limit_rect_to_quad(quad, start, desired)
        self.assertTrue(rect_in_quad(quad, limited))
        # It moved towards the drag, just not off the photo.
        self.assertLess(limited[0], start[0])
        self.assertGreater(limited[0], desired[0])

    def test_an_unrotated_photo_still_allows_the_whole_frame(self) -> None:
        quad = _view().image_quad()
        full = (0.0, 0.0, 400.0, 300.0)
        self.assertTrue(rect_in_quad(quad, full))
        self.assertEqual(full, limit_rect_to_quad(quad, full, full))

    def test_every_quarter_turn_keeps_the_auto_crop_inside_and_oriented(self) -> None:
        source_crop = (0.0, 0.0, 400.0, 300.0)
        for rotate in (0.0, 90.0, 180.0, 270.0):
            for angle in (-45.0, -20.0, 20.0, 45.0):
                with self.subTest(rotate=rotate, angle=angle):
                    view = ViewTransform(
                        source_size=(400, 300), angle=angle, rotate=rotate
                    )
                    quad = view.image_quad()
                    candidate = view.frame_rect_for_crop(source_crop)
                    fitted = fit_rect_in_quad(quad, candidate)
                    self.assertTrue(rect_in_quad(quad, fitted))
                    aspect = (fitted[2] - fitted[0]) / (fitted[3] - fitted[1])
                    expected = 3.0 / 4.0 if view.swaps_axes() else 4.0 / 3.0
                    self.assertAlmostEqual(expected, aspect, places=6)


if __name__ == "__main__":
    unittest.main()
