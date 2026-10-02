from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QStandardPaths

from image_triage.scan_cache import app_data_root


class ScanCacheAppDataRootTests(unittest.TestCase):
    """WI-3.6: app_data_root() now resolves through QStandardPaths instead of
    hand-rolling %APPDATA%\\ImageTriage, except for the pre-existing
    IMAGE_TRIAGE_APPDATA test-sandboxing override, which is unchanged."""

    def setUp(self) -> None:
        self._previous_override = os.environ.pop("IMAGE_TRIAGE_APPDATA", None)

    def tearDown(self) -> None:
        if self._previous_override is None:
            os.environ.pop("IMAGE_TRIAGE_APPDATA", None)
        else:
            os.environ["IMAGE_TRIAGE_APPDATA"] = self._previous_override

    def test_image_triage_appdata_override_still_wins_and_appends_imagetriage(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_scan_cache_override_") as temp_dir:
            with mock.patch.dict(os.environ, {"IMAGE_TRIAGE_APPDATA": temp_dir}):
                self.assertEqual(Path(temp_dir) / "ImageTriage", app_data_root())

    def test_without_the_override_it_resolves_via_qstandardpaths_not_a_raw_env_var(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_scan_cache_qsp_") as temp_dir:
            with mock.patch.object(
                QStandardPaths, "writableLocation", staticmethod(lambda location: temp_dir)
            ):
                self.assertEqual(Path(temp_dir), app_data_root())


if __name__ == "__main__":
    unittest.main()
