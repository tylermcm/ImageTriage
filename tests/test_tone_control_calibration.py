from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "tone_control_calibration.py"
SPEC = importlib.util.spec_from_file_location("tone_control_calibration", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
tone_calibration = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = tone_calibration
SPEC.loader.exec_module(tone_calibration)


class ToneControlCalibrationTests(unittest.TestCase):
    def test_target_contains_an_exact_256_level_neutral_ramp(self) -> None:
        target = tone_calibration.build_target()

        channels, luma = tone_calibration._extract_gray_curve(target)

        self.assertEqual((tone_calibration.TARGET_WIDTH, tone_calibration.TARGET_HEIGHT), target.size)
        np.testing.assert_array_equal(channels[:, 0], np.arange(256, dtype=np.float32))
        np.testing.assert_allclose(luma, np.arange(256, dtype=np.float32), atol=1e-4)

    def test_highlight_endpoint_is_monotonic(self) -> None:
        target = tone_calibration.build_target()
        rendered = tone_calibration.EditRecipe(highlights=-100).apply(target)

        metrics, _channels = tone_calibration._metrics(target, rendered)

        self.assertEqual(0, metrics["reversals"])
        self.assertLess(metrics["maximum"], 255.0)

    def test_analyzer_accepts_an_incremental_photoshop_export_set(self) -> None:
        with tempfile.TemporaryDirectory(prefix="tone_calibration_test_") as temp_dir:
            run_dir = Path(temp_dir)
            target = tone_calibration.build_target()
            target.save(run_dir / "tone_target.png")
            photoshop_dir = run_dir / "photoshop"
            photoshop_dir.mkdir()
            target.save(photoshop_dir / "contrast_m100.png")

            report_path = tone_calibration.analyze(run_dir)
            report = report_path.read_text(encoding="utf-8")

            self.assertIn("| Photoshop | contrast | -100", report)
            self.assertIn("Missing Photoshop exports (23)", report)
            self.assertTrue((run_dir / "curves.csv").exists())


if __name__ == "__main__":
    unittest.main()
