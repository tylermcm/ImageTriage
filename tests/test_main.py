from __future__ import annotations

import unittest

from image_triage.main import launch_target_from_argv


class MainLaunchTests(unittest.TestCase):
    def test_launch_target_from_argv_returns_first_nonempty_argument(self) -> None:
        target = launch_target_from_argv(["image_triage", "", '"C:\\Photos\\Set 1"'])
        self.assertEqual(target, "C:\\Photos\\Set 1")

    def test_launch_target_from_argv_returns_empty_when_missing(self) -> None:
        self.assertEqual(launch_target_from_argv(["image_triage"]), "")


if __name__ == "__main__":
    unittest.main()
