from __future__ import annotations

import io
import json
import os
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from PIL import Image

import image_triage.oneformer_worker as worker


class OneFormerMappingTests(unittest.TestCase):
    """Ported from the validated OneFormer sandbox mapping suite."""

    def test_ade_labels_map_to_editor_categories(self) -> None:
        self.assertEqual("buildings", worker._category_for_raw_label("building, edifice"))
        self.assertEqual("trees", worker._category_for_raw_label("tree"))
        self.assertEqual("foliage", worker._category_for_raw_label("plant, flora"))
        self.assertEqual("water", worker._category_for_raw_label("swimming_pool"))
        self.assertEqual("water", worker._category_for_raw_label("falls"))
        self.assertEqual("water", worker._category_for_raw_label("pool"))
        self.assertEqual("mountains", worker._category_for_raw_label("hill"))
        self.assertIsNone(worker._category_for_raw_label("road, route"))

    def test_comma_separated_aliases_split_and_match(self) -> None:
        # "palm, palm tree" is one ADE label with two comma-delimited aliases.
        self.assertEqual("trees", worker._category_for_raw_label("palm, palm tree"))

    def test_unknown_labels_are_ignored(self) -> None:
        self.assertIsNone(worker._category_for_raw_label("wall"))
        self.assertIsNone(worker._category_for_raw_label("road"))

    def test_parse_categories_normalizes_and_rejects_unknown(self) -> None:
        self.assertEqual(("sky", "water"), worker._parse_categories(["Sky", "water", "SKY"]))
        self.assertEqual((), worker._parse_categories(["road", "bogus"]))
        self.assertEqual((), worker._parse_categories("not-a-list"))

    def test_category_class_ids_group_by_editor_category(self) -> None:
        id2label = {
            0: "wall",
            1: "building, edifice",
            2: "sky",
            4: "tree",
            9: "grass",
            12: "person",
            16: "mountain",
            21: "water",
        }
        class_ids, source_labels = worker._category_class_ids(
            id2label, ("sky", "trees", "water", "buildings")
        )
        self.assertEqual([2], class_ids["sky"])
        self.assertEqual([4], class_ids["trees"])
        self.assertEqual([21], class_ids["water"])
        self.assertEqual([1], class_ids["buildings"])
        self.assertEqual(["building, edifice"], source_labels["buildings"])


class _FakeInferenceMode:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _FakeCuda:
    @staticmethod
    def is_available() -> bool:
        return False


class _FakeTorch:
    cuda = _FakeCuda()

    @staticmethod
    def inference_mode():
        return _FakeInferenceMode()


class _FakeSegmentation:
    def __init__(self, array: np.ndarray) -> None:
        self._array = array

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self._array


class _FakeProcessor:
    def __init__(self, segmentation: np.ndarray) -> None:
        self._segmentation = segmentation

    def __call__(self, *, images, task_inputs, return_tensors):
        assert task_inputs == ["semantic"]
        return {"pixel_values": object()}

    def post_process_semantic_segmentation(self, outputs, target_sizes):
        return [_FakeSegmentation(self._segmentation)]


class _FakeEngine:
    """Stands in for the torch/transformers engine without importing them."""

    def __init__(self, segmentation: np.ndarray, id2label: dict[int, str]) -> None:
        self.torch = _FakeTorch()
        self.np = np
        self.Image = Image
        self.processor = _FakeProcessor(segmentation)
        self.model = lambda **_kwargs: object()
        self.id2label = id2label
        self.device = "cpu"

    def load_model(self, _model_dir: Path) -> str:
        return "cpu"


class OneFormerGenerateTests(unittest.TestCase):
    def _fixture(self, directory: Path) -> tuple[Path, Path, np.ndarray, dict[int, str]]:
        # A 6x4 scene: top two rows sky, bottom two water, one speck of person.
        segmentation = np.array(
            [
                [2, 2, 2, 2, 2, 2],
                [2, 2, 2, 2, 2, 2],
                [21, 21, 21, 21, 21, 21],
                [21, 21, 21, 21, 21, 12],
            ],
            dtype=np.int64,
        )
        id2label = {0: "wall", 2: "sky", 12: "person", 21: "water", 4: "tree"}
        source = directory / "input.png"
        Image.new("RGB", (6, 4), (10, 20, 30)).save(source)
        output_dir = directory / "masks"
        return source, output_dir, segmentation, id2label

    def test_writes_every_category_and_reports_coverage(self) -> None:
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            source, output_dir, segmentation, id2label = self._fixture(directory)
            engine = _FakeEngine(segmentation, id2label)
            categories = ("sky", "trees", "water", "people")

            result = worker.generate_semantic_masks(
                model_dir=directory,
                input_path=source,
                output_dir=output_dir,
                categories=categories,
                minimum_coverage=0.0,
                requested_device="cpu",
                engine=engine,
                emit_result=False,
            )

            self.assertEqual([6, 4], result["sourceSize"])
            self.assertEqual("cpu", result["device"])
            # A PNG exists for every requested category, blanks included.
            for category in categories:
                self.assertTrue((output_dir / f"{category}.png").is_file(), category)
            # trees is absent -> blank mask, zero coverage, no source labels.
            with Image.open(output_dir / "trees.png") as trees:
                self.assertEqual(0, int(np.asarray(trees.convert("L")).max()))
            stats = result["categoryStats"]
            self.assertEqual(0.0, stats["trees"]["coverage"])
            self.assertEqual([], stats["trees"]["sourceLabels"])
            # sky is the top half.
            self.assertAlmostEqual(0.5, stats["sky"]["coverage"], places=6)
            self.assertEqual(["sky"], stats["sky"]["sourceLabels"])
            with Image.open(output_dir / "sky.png") as sky:
                sky_values = np.asarray(sky.convert("L"), dtype=np.uint8)
            self.assertTrue((sky_values[:2] == 255).all())
            self.assertTrue((sky_values[2:] == 0).all())

    def test_minimum_coverage_suppresses_specks(self) -> None:
        with TemporaryDirectory() as tmp:
            directory = Path(tmp)
            source, output_dir, segmentation, id2label = self._fixture(directory)
            engine = _FakeEngine(segmentation, id2label)

            result = worker.generate_semantic_masks(
                model_dir=directory,
                input_path=source,
                output_dir=output_dir,
                categories=("sky", "water", "people"),
                minimum_coverage=0.10,  # the single person pixel is ~0.04
                requested_device="cpu",
                engine=engine,
                emit_result=False,
            )

            self.assertEqual(0.0, result["categoryStats"]["people"]["coverage"])
            with Image.open(output_dir / "people.png") as people:
                self.assertEqual(0, int(np.asarray(people.convert("L")).max()))
            # Sky and water clear the threshold and stay.
            self.assertGreater(result["categoryStats"]["sky"]["coverage"], 0.10)


class OneFormerMetricTests(unittest.TestCase):
    def test_metric_is_structured_json_when_enabled(self) -> None:
        previous = os.environ.get("IMAGE_TRIAGE_AI_METRICS")
        os.environ["IMAGE_TRIAGE_AI_METRICS"] = "1"
        output = io.StringIO()
        try:
            with redirect_stdout(output):
                worker._emit_metric(
                    "ai.mask.oneformer.worker.inference",
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
        self.assertEqual("ai.mask.oneformer.worker.inference", payload["event"])
        self.assertGreater(payload["duration_ms"], 0)

    def test_metric_is_silent_when_disabled(self) -> None:
        previous = os.environ.get("IMAGE_TRIAGE_AI_METRICS")
        os.environ["IMAGE_TRIAGE_AI_METRICS"] = "0"
        output = io.StringIO()
        try:
            with redirect_stdout(output):
                worker._emit_metric("ai.mask.oneformer.worker.total", time.perf_counter())
        finally:
            if previous is None:
                os.environ.pop("IMAGE_TRIAGE_AI_METRICS", None)
            else:
                os.environ["IMAGE_TRIAGE_AI_METRICS"] = previous
        self.assertEqual("", output.getvalue().strip())


if __name__ == "__main__":
    unittest.main()
