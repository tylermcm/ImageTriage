from __future__ import annotations

import os
import sys
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from types import SimpleNamespace
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

    @unittest.skipUnless(os.name == "nt", "asserts the backslash spelling of a drive path, which only Windows pathlib produces")
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

    @unittest.skipUnless(os.name == "nt", "patches os.name to 'nt', and pathlib cannot build a WindowsPath off Windows")
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

    def test_a_registered_command_is_stale_only_when_its_program_is_gone(self) -> None:
        from image_triage import file_associations as fa

        with TemporaryDirectory() as temp_dir:
            present = Path(temp_dir) / "Image Triage" / "ImageTriage.exe"
            present.parent.mkdir()
            present.write_bytes(b"")
            gone = Path(temp_dir) / "Old Place" / "ImageTriage.exe"
            self.assertEqual(str(present), fa.command_executable(f'"{present}" "%1"'))
            self.assertFalse(fa.command_is_stale(f'"{present}" "%1"'))
            self.assertTrue(fa.command_is_stale(f'"{gone}" "%1"'))
            self.assertTrue(fa.command_is_stale(f'"{gone}" -m image_triage "%1"'))
            self.assertEqual("python.exe", fa.command_executable('python.exe -m image_triage "%1"'))
        for unreadable in ("", "   ", '"unterminated'):
            self.assertFalse(fa.command_is_stale(unreadable), unreadable)      # nothing to judge: left alone

    def _repair(self, registered: dict[str, str], *, frozen: bool = True, existing_keys: tuple[str, ...] = ()):
        """Run the start-up repair against a fake registry; returns (changed, the values it wrote)."""
        from image_triage import file_associations as fa

        written: dict[str, str] = {}
        keys = set(registered) | set(existing_keys)
        with patch.object(fa.os, "name", "nt"), patch.object(fa, "winreg", SimpleNamespace(HKEY_CURRENT_USER=1)), patch.object(
            fa.sys, "frozen", frozen, create=True
        ), patch.object(fa, "registered_open_commands", lambda: dict(registered)), patch.object(
            fa, "_registry_key_exists", lambda root, key: key in keys
        ), patch.object(fa, "_set_default_value", lambda root, key, value: written.__setitem__(key, value)), patch.object(
            fa, "_notify_windows_shell_of_association_change", lambda: None
        ), patch.object(fa, "current_file_association_command", lambda: '"X:\\New\\ImageTriage.exe" "%1"'):
            changed = fa.repair_stale_windows_file_associations()
        return changed, written

    def test_startup_points_a_registration_at_the_running_program_when_the_registered_one_is_gone(self) -> None:
        from image_triage import file_associations as fa

        with TemporaryDirectory() as temp_dir:
            gone = f'"{Path(temp_dir) / "gone" / "ImageTriage.exe"}" "%1"'
            present_file = Path(temp_dir) / "ImageTriage.exe"
            present_file.write_bytes(b"")
            present = f'"{present_file}" "%1"'
            app_key, prog_key = fa._OPEN_COMMAND_KEYS
            icon_key = rf"{fa._PROG_ID_KEY}\DefaultIcon"

            changed, written = self._repair({app_key: gone, prog_key: gone}, existing_keys=(icon_key,))
            self.assertTrue(changed)
            self.assertEqual({app_key, prog_key, icon_key}, set(written))
            self.assertEqual('"X:\\New\\ImageTriage.exe" "%1"', written[app_key])
            self.assertEqual("X:\\New\\ImageTriage.exe", written[icon_key])

            self.assertEqual((False, {}), self._repair({app_key: present, prog_key: present}))   # a working registration is not touched
            self.assertEqual((False, {}), self._repair({}))                                        # nothing registered: nothing is added
            self.assertEqual((False, {}), self._repair({app_key: gone}, frozen=False))             # a run from source takes nothing over
            changed, written = self._repair({app_key: gone})                                       # only the keys that exist are rewritten
            self.assertEqual((True, {app_key}), (changed, set(written)))

    def test_repair_does_nothing_off_windows(self) -> None:
        from image_triage import file_associations as fa

        if os.name == "nt":
            self.skipTest("this is the answer every other system gives")
        self.assertFalse(fa.repair_stale_windows_file_associations())
        self.assertEqual({}, fa.registered_open_commands())
        self.assertFalse(fa.query_windows_file_association_status().stale_command)

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
