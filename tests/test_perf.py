from __future__ import annotations

import os
import tempfile
import unittest

from image_triage.perf import PerformanceLogger


class PerformanceLoggerTests(unittest.TestCase):
    def test_enable_creates_and_flushes_log_file(self) -> None:
        old_override = os.environ.get("IMAGE_TRIAGE_LOG_DIR")
        old_localappdata = os.environ.get("LOCALAPPDATA")
        with tempfile.TemporaryDirectory(prefix="image_triage_perf_") as temp_dir:
            os.environ["IMAGE_TRIAGE_LOG_DIR"] = temp_dir
            os.environ["LOCALAPPDATA"] = temp_dir
            logger = PerformanceLogger()
            try:
                logger.set_enabled(True, reason="test")
                self.assertTrue(logger.is_writing)
                self.assertIsNotNone(logger.path)
                assert logger.path is not None
                self.assertTrue(logger.path.exists())
                text = logger.path.read_text(encoding="utf-8")
                self.assertIn("perf.enabled", text)
            finally:
                logger.set_enabled(False, reason="test_cleanup")
                if old_override is None:
                    os.environ.pop("IMAGE_TRIAGE_LOG_DIR", None)
                else:
                    os.environ["IMAGE_TRIAGE_LOG_DIR"] = old_override
                if old_localappdata is None:
                    os.environ.pop("LOCALAPPDATA", None)
                else:
                    os.environ["LOCALAPPDATA"] = old_localappdata


if __name__ == "__main__":
    unittest.main()
