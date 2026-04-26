from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from image_triage.file_associations import current_file_association_command, supported_file_association_suffixes


class FileAssociationTests(unittest.TestCase):
    def test_supported_suffixes_include_core_formats_and_exclude_composite_suffixes(self) -> None:
        suffixes = supported_file_association_suffixes()
        self.assertIn(".jpg", suffixes)
        self.assertIn(".nef", suffixes)
        self.assertIn(".fits", suffixes)
        self.assertNotIn(".fits.gz", suffixes)

    def test_current_command_uses_module_launch_in_source_mode(self) -> None:
        with patch.object(sys, "executable", str(Path("C:/Python313/python.exe"))):
            with patch.object(sys, "frozen", False, create=True):
                command = current_file_association_command()
        self.assertIn("-m image_triage", command)
        self.assertIn("%1", command)

    def test_current_command_uses_executable_when_frozen(self) -> None:
        with patch.object(sys, "executable", str(Path("C:/Program Files/ImageTriage/ImageTriage.exe"))):
            with patch.object(sys, "frozen", True, create=True):
                command = current_file_association_command()
        self.assertEqual(command, '"C:\\Program Files\\ImageTriage\\ImageTriage.exe" "%1"')


if __name__ == "__main__":
    unittest.main()
