"""WI-8.3: the always-on error logger is independent of the opt-in perf log."""
from __future__ import annotations

import logging
import os
import tempfile
import unittest

from image_triage import app_logging
from image_triage.perf import PerformanceLogger


class AppLoggingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old_override = os.environ.get("IMAGE_TRIAGE_LOG_DIR")
        self._old_configured = app_logging._configured
        self._old_handlers = list(logging.getLogger().handlers)
        self._old_level = logging.getLogger().level
        self._temp_dir = tempfile.TemporaryDirectory(prefix="image_triage_applog_")
        # A subdirectory that does not exist yet, so we can verify configure_app_logging()
        # creates it rather than relying on the TemporaryDirectory's own existing path.
        self._log_dir = os.path.join(self._temp_dir.name, "logs")
        os.environ["IMAGE_TRIAGE_LOG_DIR"] = self._log_dir
        app_logging._configured = False

    def tearDown(self) -> None:
        root = logging.getLogger()
        for handler in list(root.handlers):
            if handler not in self._old_handlers:
                root.removeHandler(handler)
                handler.close()
        root.setLevel(self._old_level)
        app_logging._configured = self._old_configured
        if self._old_override is None:
            os.environ.pop("IMAGE_TRIAGE_LOG_DIR", None)
        else:
            os.environ["IMAGE_TRIAGE_LOG_DIR"] = self._old_override
        self._temp_dir.cleanup()

    def test_configure_creates_log_dir_if_missing(self) -> None:
        log_path = app_logging.app_log_path()
        self.assertFalse(log_path.parent.exists())
        app_logging.configure_app_logging()
        self.assertTrue(log_path.parent.exists())


    def test_configure_is_idempotent(self) -> None:
        app_logging.configure_app_logging()
        handlers_after_first = len(logging.getLogger().handlers)
        app_logging.configure_app_logging()
        self.assertEqual(len(logging.getLogger().handlers), handlers_after_first)



if __name__ == "__main__":
    unittest.main()
