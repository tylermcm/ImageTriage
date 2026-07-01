from __future__ import annotations

import unittest

import numpy as np

from image_triage.quality.face import (
    FaceQualityAnalyzer,
    FaceRecord,
    _eye_sharpness_from_keypoints,
    aggregate_face_quality,
    eye_aspect_ratio,
    eye_region_sharpness,
    is_blink,
)


class _MockFace:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def _eye_points(vertical_half: float) -> np.ndarray:
    # corners at x=0 and x=10 (h=10); two vertical lid pairs of height 2*vertical_half.
    return np.array([
        [0.0, 0.0],                       # p0 left corner
        [3.0, vertical_half],             # p1 upper
        [7.0, vertical_half],             # p2 upper
        [10.0, 0.0],                      # p3 right corner
        [7.0, -vertical_half],            # p4 lower
        [3.0, -vertical_half],            # p5 lower
    ])


def _checkerboard(h=40, w=40, block=4) -> np.ndarray:
    yy, xx = np.indices((h, w))
    g = (((yy // block + xx // block) % 2) * 255).astype(np.uint8)
    return np.stack([g, g, g], axis=-1)


def _gradient(h=40, w=40) -> np.ndarray:
    g = np.tile(np.linspace(0, 255, w), (h, 1)).astype(np.uint8)
    return np.stack([g, g, g], axis=-1)


class EyeMathTests(unittest.TestCase):
    def test_ear_open_vs_closed(self) -> None:
        open_ear = eye_aspect_ratio(_eye_points(3.0))
        closed_ear = eye_aspect_ratio(_eye_points(0.2))
        self.assertGreater(open_ear, closed_ear)
        self.assertGreater(open_ear, 0.21)
        self.assertLess(closed_ear, 0.21)

    def test_eye_region_sharpness_sharp_beats_blurry(self) -> None:
        self.assertGreater(eye_region_sharpness(_checkerboard()), eye_region_sharpness(_gradient()))
        self.assertLessEqual(eye_region_sharpness(_checkerboard()), 10.0)


class FaceQualityTests(unittest.TestCase):
    def test_single_face(self) -> None:
        self.assertAlmostEqual(aggregate_face_quality([0.9]), 9.0)

    def test_weakest_face_dominates(self) -> None:
        # min=0.5, avg=0.7 -> 0.7*0.5 + 0.3*0.7 = 0.56 -> 5.6
        self.assertAlmostEqual(aggregate_face_quality([0.9, 0.5]), 5.6)

    def test_no_faces_returns_none(self) -> None:
        self.assertIsNone(aggregate_face_quality([]))


class BlinkTests(unittest.TestCase):
    def test_closed_frontal_is_blink(self) -> None:
        self.assertTrue(is_blink(0.1, yaw=0.0, pitch=0.0))

    def test_open_is_not_blink(self) -> None:
        self.assertFalse(is_blink(0.4))

    def test_steep_pose_suppresses_blink(self) -> None:
        self.assertFalse(is_blink(0.1, yaw=50.0))
        self.assertFalse(is_blink(0.1, pitch=40.0))


class KeypointEyeSharpnessTests(unittest.TestCase):
    def test_sharp_eye_region_scores_higher(self) -> None:
        sharp = _checkerboard(80, 80)
        blurry = _gradient(80, 80)
        kps = np.array([[24.0, 40.0], [56.0, 40.0]])  # two eye centers
        s_sharp = _eye_sharpness_from_keypoints(sharp, kps)
        s_blur = _eye_sharpness_from_keypoints(blurry, kps)
        self.assertIsNotNone(s_sharp)
        self.assertGreater(s_sharp, s_blur)
        self.assertLessEqual(s_sharp, 10.0)


class GenderAgeTests(unittest.TestCase):
    def test_gender_from_sex_string(self) -> None:
        self.assertEqual(FaceQualityAnalyzer._gender(_MockFace(sex="M")), "M")
        self.assertEqual(FaceQualityAnalyzer._gender(_MockFace(sex="Female")), "F")

    def test_gender_from_int(self) -> None:
        self.assertEqual(FaceQualityAnalyzer._gender(_MockFace(gender=1)), "M")
        self.assertEqual(FaceQualityAnalyzer._gender(_MockFace(gender=0)), "F")
        self.assertIsNone(FaceQualityAnalyzer._gender(_MockFace()))

    def test_age(self) -> None:
        self.assertEqual(FaceQualityAnalyzer._age(_MockFace(age=31)), 31)
        self.assertIsNone(FaceQualityAnalyzer._age(_MockFace()))


class FaceRecordTests(unittest.TestCase):
    def test_record_fields(self) -> None:
        rec = FaceRecord(bbox=(1.0, 2.0, 3.0, 4.0), det_score=0.8, eye_sharpness=7.5, gender="M", age=30)
        self.assertEqual(rec.bbox, (1.0, 2.0, 3.0, 4.0))
        self.assertIsNone(rec.blink)  # deferred


class GracefulDegradeTests(unittest.TestCase):
    def test_analyzer_degrades_without_insightface(self) -> None:
        # insightface is not installed in this environment -> analyzer unavailable,
        # dims are None, nothing raises.
        analyzer = FaceQualityAnalyzer()
        self.assertFalse(analyzer.available)
        result = analyzer.analyze(_gradient())
        self.assertEqual(result["face_count"], 0)
        self.assertIsNone(result["face_quality"])
        self.assertIsNone(result["eye_sharpness"])
        self.assertIsNone(result["blink"])
        self.assertEqual(result["faces"], [])


if __name__ == "__main__":
    unittest.main()
