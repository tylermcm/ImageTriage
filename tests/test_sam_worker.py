from __future__ import annotations

import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image

from image_triage.sam_worker import _SamEngine, _choose_mask_index


class _FakeTensor:
    def __init__(self, values) -> None:
        self.values = np.asarray(values)

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self.values


class _FakeInputs(dict):
    def to(self, _device):
        return self


class _FakeProcessor:
    def __init__(self) -> None:
        self.input_points = None

    def __call__(self, *, images, input_points, input_labels, return_tensors):
        self.input_points = input_points
        return _FakeInputs(original_sizes=[(4, 4)])

    def post_process_masks(self, _pred_masks, _original_sizes):
        masks = np.zeros((2, 3, 4, 4), dtype=np.float32)
        masks[0, 0, :2, :2] = 1.0
        masks[1, 1, 2:, 2:] = 1.0
        return [_FakeTensor(masks)]


class _FakeModel:
    def __call__(self, **_inputs):
        return SimpleNamespace(
            pred_masks=_FakeTensor([]),
            iou_scores=_FakeTensor([[[0.9, 0.1, 0.0], [0.1, 0.9, 0.0]]]),
        )


class SamMaskChoiceTests(unittest.TestCase):
    def test_prefers_the_whole_object_over_parts(self) -> None:
        # SAM returns [part, subpart, whole]; the largest (whole) wins.
        self.assertEqual(2, _choose_mask_index([100, 10, 500], frame=10_000))

    def test_skips_a_near_whole_frame_background_mask(self) -> None:
        # A mask covering ~95% of the frame is background; pick the next largest.
        self.assertEqual(2, _choose_mask_index([100, 9_500, 500], frame=10_000))

    def test_falls_back_to_largest_when_all_are_near_frame(self) -> None:
        self.assertEqual(1, _choose_mask_index([9_200, 9_600], frame=10_000))

    def test_single_candidate(self) -> None:
        self.assertEqual(0, _choose_mask_index([42], frame=10_000))

    def test_empty_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _choose_mask_index([], 100)

    def test_segment_many_keeps_prompts_as_independent_objects(self) -> None:
        engine = _SamEngine("cpu")
        processor = _FakeProcessor()
        engine.device = "cpu"
        engine.torch = SimpleNamespace(inference_mode=nullcontext)
        engine.np = np
        engine.Image = Image
        engine.model = _FakeModel()
        engine.processor = processor
        engine._image = Image.new("RGB", (4, 4))
        engine._image_key = "probe"

        with tempfile.TemporaryDirectory(prefix="sam_many_") as temp_dir:
            outputs = [Path(temp_dir) / "one.png", Path(temp_dir) / "two.png"]
            results = engine.segment_many(
                point_groups=[[(1.0, 1.0)], [(3.0, 3.0)]],
                label_groups=[[1], [1]],
                output_paths=outputs,
                image_key="probe",
            )

            self.assertEqual([[[[1.0, 1.0]], [[3.0, 3.0]]]], processor.input_points)
            self.assertEqual(2, len(results))
            self.assertTrue(all(path.is_file() for path in outputs))


if __name__ == "__main__":
    unittest.main()
