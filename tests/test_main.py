from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from image_triage.main import is_quick_view_launch_target, launch_target_from_argv


class MainLaunchTests(unittest.TestCase):
    def test_launch_target_from_argv_returns_first_nonempty_argument(self) -> None:
        target = launch_target_from_argv(["image_triage", "", '"C:\\Photos\\Set 1"'])
        self.assertEqual(target, "C:\\Photos\\Set 1")

    def test_launch_target_from_argv_returns_empty_when_missing(self) -> None:
        self.assertEqual(launch_target_from_argv(["image_triage"]), "")

    def test_existing_supported_image_is_a_quick_view_target(self) -> None:
        with TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "photo.jpg"
            image_path.touch()
            self.assertTrue(is_quick_view_launch_target(str(image_path)))

    def test_folder_and_non_image_file_are_not_quick_view_targets(self) -> None:
        with TemporaryDirectory() as temp_dir:
            text_path = Path(temp_dir) / "notes.txt"
            text_path.touch()
            self.assertFalse(is_quick_view_launch_target(temp_dir))
            self.assertFalse(is_quick_view_launch_target(str(text_path)))


if __name__ == "__main__":
    unittest.main()
