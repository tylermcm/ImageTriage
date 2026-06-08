from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

from image_triage import __version__


class VersionTests(unittest.TestCase):
    def test_package_version_matches_pyproject(self) -> None:
        pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
        data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        self.assertEqual(__version__, data["project"]["version"])


if __name__ == "__main__":
    unittest.main()
