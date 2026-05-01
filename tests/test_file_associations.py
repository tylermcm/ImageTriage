from __future__ import annotations

import sys
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

from image_triage.file_associations import (
    APP_FRIENDLY_NAME,
    ExtensionAssociationState,
    current_file_association_command,
    describe_windows_default_handler,
    open_windows_file_association_chooser,
    query_windows_file_association_states,
    supported_file_association_suffixes,
)


class FileAssociationTests(unittest.TestCase):
    def test_supported_suffixes_include_core_formats_and_exclude_composite_suffixes(self) -> None:
        suffixes = supported_file_association_suffixes()
        self.assertIn(".jpg", suffixes)
        self.assertIn(".nef", suffixes)
        self.assertIn(".fits", suffixes)
        self.assertNotIn(".fits.gz", suffixes)

    def test_supported_suffixes_prioritize_common_formats_first(self) -> None:
        suffixes = supported_file_association_suffixes()
        self.assertLess(suffixes.index(".jpg"), suffixes.index(".avif"))
        self.assertLess(suffixes.index(".png"), suffixes.index(".bmp"))
        self.assertLess(suffixes.index(".nef"), suffixes.index(".bay"))
        self.assertLess(suffixes.index(".fits"), suffixes.index(".psd"))

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

    def test_query_states_marks_registered_default_extensions(self) -> None:
        with patch("image_triage.file_associations.os.name", "nt"), patch(
            "image_triage.file_associations.winreg",
            object(),
        ), patch(
            "image_triage.file_associations._extension_has_progid",
            side_effect=lambda suffix: suffix == ".nef",
        ), patch(
            "image_triage.file_associations._default_progid_for_extension",
            side_effect=lambda suffix: "ImageTriage.SupportedImage" if suffix == ".nef" else "Other.App",
        ):
            states = query_windows_file_association_states()
        nef_state = next(state for state in states if state.suffix == ".nef")
        self.assertTrue(nef_state.registered)
        self.assertTrue(nef_state.is_default)
        jpg_state = next(state for state in states if state.suffix == ".jpg")
        self.assertFalse(jpg_state.is_default)
        self.assertEqual(jpg_state.default_progid, "Other.App")

    def test_open_windows_file_association_chooser_creates_probe_and_opens_dialog(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with patch("image_triage.file_associations.os.name", "nt"), patch(
                "image_triage.file_associations.register_windows_file_associations",
            ) as register_associations, patch(
                "image_triage.file_associations.tempfile.gettempdir",
                return_value=temp_dir,
            ), patch(
                "image_triage.file_associations.open_with_dialog",
            ) as open_dialog:
                probe_path = open_windows_file_association_chooser(".fits")
                self.assertTrue(Path(probe_path).exists())
        register_associations.assert_called_once_with([".fits"])
        open_dialog.assert_called_once_with(probe_path)
        self.assertTrue(probe_path.endswith(".fits"))

    def test_describe_windows_default_handler_prefers_friendly_names(self) -> None:
        self.assertEqual(
            describe_windows_default_handler(
                ExtensionAssociationState(".jpg", registered=True, is_default=True, default_progid="ImageTriage.SupportedImage")
            ),
            APP_FRIENDLY_NAME,
        )
        self.assertEqual(
            describe_windows_default_handler(
                ExtensionAssociationState(".nef", registered=True, is_default=False, default_progid="Applications\\Photoshop.exe")
            ),
            "Photoshop.exe",
        )
        with patch("image_triage.file_associations._resolve_progid_display_name", return_value="Windows Photos"):
            self.assertEqual(
                describe_windows_default_handler(
                    ExtensionAssociationState(".jpg", registered=True, is_default=False, default_progid="AppX9rkaq77s0jh1tyccadx9ghba15r6t3h")
                ),
                "Windows Photos",
            )
        self.assertEqual(
            describe_windows_default_handler(
                ExtensionAssociationState(".psd", registered=False, is_default=False, default_progid="")
            ),
            "Not Registered",
        )


if __name__ == "__main__":
    unittest.main()
