from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from image_triage import frozen_bootstrap


class FrozenBootstrapTests(unittest.TestCase):
    def setUp(self) -> None:
        frozen_bootstrap._DLL_DIRECTORY_HANDLES.clear()

    def tearDown(self) -> None:
        frozen_bootstrap._DLL_DIRECTORY_HANDLES.clear()

    def test_configure_frozen_dll_search_registers_shared_lib_before_native_imports(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            lib_dir = root / "lib"
            lib_dir.mkdir()
            handle = object()
            add_dll_directory = Mock(return_value=handle)
            with (
                patch.object(sys, "platform", "win32"),
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "executable", str(root / "ImageTriage.exe")),
                patch.object(os, "add_dll_directory", add_dll_directory, create=True),
                patch.dict(os.environ, {"PATH": r"C:\Other"}, clear=False),
            ):
                frozen_bootstrap.configure_frozen_dll_search()

                self.assertEqual(os.environ["PATH"].split(os.pathsep)[0], str(lib_dir))

        add_dll_directory.assert_called_once_with(str(lib_dir))
        self.assertEqual(frozen_bootstrap._DLL_DIRECTORY_HANDLES, [handle])

    def test_configure_frozen_stdlib_prepends_helper_standard_library(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            stdlib_dir = root / "ai_stdlib"
            stdlib_dir.mkdir()
            with (
                patch.object(sys, "frozen", True, create=True),
                patch.object(sys, "executable", str(root / "ai_runtime_installer.exe")),
                patch.object(sys, "path", ["library.zip", str(stdlib_dir)]),
            ):
                frozen_bootstrap.configure_frozen_stdlib()

                self.assertEqual(sys.path, [str(stdlib_dir), "library.zip"])


if __name__ == "__main__":
    unittest.main()
