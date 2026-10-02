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

    def test_logger_exception_writes_to_log_file_after_configuration(self) -> None:
        app_logging.configure_app_logging()
        logger = logging.getLogger("image_triage.window")
        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception("something failed")
        for handler in logging.getLogger().handlers:
            handler.flush()
        log_path = app_logging.app_log_path()
        self.assertTrue(log_path.exists())
        text = log_path.read_text(encoding="utf-8")
        self.assertIn("something failed", text)
        self.assertIn("ValueError: boom", text)

    def test_configure_is_idempotent(self) -> None:
        app_logging.configure_app_logging()
        handlers_after_first = len(logging.getLogger().handlers)
        app_logging.configure_app_logging()
        self.assertEqual(len(logging.getLogger().handlers), handlers_after_first)

    def test_works_regardless_of_performance_logging_toggle(self) -> None:
        # The error logger is a separate, always-on system: it must not
        # require (or be affected by) the opt-in perf JSONL logger's state.
        app_logging.configure_app_logging()
        logger = logging.getLogger("image_triage.window")
        log_path = app_logging.app_log_path()

        perf_logger = PerformanceLogger()
        self.assertFalse(perf_logger.enabled)
        logger.warning("failure with perf logging disabled")
        for handler in logging.getLogger().handlers:
            handler.flush()
        self.assertIn("failure with perf logging disabled", log_path.read_text(encoding="utf-8"))

        try:
            perf_logger.set_enabled(True, reason="test")
            logger.warning("failure with perf logging enabled")
            for handler in logging.getLogger().handlers:
                handler.flush()
        finally:
            perf_logger.set_enabled(False, reason="test_cleanup")

        text = log_path.read_text(encoding="utf-8")
        self.assertIn("failure with perf logging enabled", text)


if __name__ == "__main__":
    unittest.main()
