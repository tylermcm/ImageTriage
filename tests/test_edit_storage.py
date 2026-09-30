from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from image_triage.edit_storage import (
    EDIT_STORAGE_ROOT_NAME,
    consolidate_folder,
    editor_session_path,
    legacy_session_path,
    migrate_bundle,
    resolve_session_for_read,
    session_has_edits,
)
from image_triage.photo_terminal.session import SCHEMA_NAME, SCHEMA_VERSION


def _write_legacy_bundle(folder: Path, stem: str, *, with_assets: bool = True) -> Path:
    """Create a beside-photo <stem>.jpg + <stem>.edit.json (+ .edit-assets)."""

    folder.mkdir(parents=True, exist_ok=True)
    image = folder / f"{stem}.jpg"
    image.write_bytes(b"original")
    (folder / f"{stem}.edit.json").write_text('{"version": 1}')
    if with_assets:
        asset_dir = folder / f"{stem}.edit-assets"
        asset_dir.mkdir()
        (asset_dir / "mask-001.png").write_bytes(b"mask")
    return image


class EditStoragePathTests(unittest.TestCase):
    def test_editor_session_path_resolves_into_hidden_root(self) -> None:
        image = Path("/shoot/_DSC1.NEF")
        session = editor_session_path(image)
        self.assertEqual(session.name, "_DSC1.edit.json")
        self.assertEqual(session.parent.name, EDIT_STORAGE_ROOT_NAME)
        self.assertEqual(session.parent.parent, image.parent)

    def test_legacy_session_path_stays_beside_original(self) -> None:
        image = Path("/shoot/_DSC1.NEF")
        self.assertEqual(legacy_session_path(image), image.parent / "_DSC1.edit.json")


class MigrateBundleTests(unittest.TestCase):
    def test_migrate_moves_session_and_assets_and_leaves_original(self) -> None:
        with tempfile.TemporaryDirectory(prefix="edit_storage_") as temp_dir:
            folder = Path(temp_dir)
            image = _write_legacy_bundle(folder, "_DSC1")

            returned = migrate_bundle(image)

            new_session = editor_session_path(image)
            new_mask = new_session.parent / "_DSC1.edit-assets" / "mask-001.png"
            self.assertEqual(returned, new_session)
            self.assertTrue(new_session.exists())
            self.assertTrue(new_mask.exists())
            self.assertFalse(legacy_session_path(image).exists())
            self.assertFalse((folder / "_DSC1.edit-assets").exists())
            self.assertTrue(image.exists())

    def test_migrate_is_a_noop_without_a_legacy_bundle(self) -> None:
        with tempfile.TemporaryDirectory(prefix="edit_storage_") as temp_dir:
            folder = Path(temp_dir)
            image = folder / "_DSC1.jpg"
            image.write_bytes(b"original")

            self.assertIsNone(migrate_bundle(image))
            self.assertFalse((folder / EDIT_STORAGE_ROOT_NAME).exists())

    def test_migrate_does_not_clobber_an_existing_new_bundle(self) -> None:
        with tempfile.TemporaryDirectory(prefix="edit_storage_") as temp_dir:
            folder = Path(temp_dir)
            image = _write_legacy_bundle(folder, "_DSC1", with_assets=False)
            new_session = editor_session_path(image)
            new_session.parent.mkdir(parents=True, exist_ok=True)
            new_session.write_text('{"version": 1, "kept": true}')

            migrate_bundle(image)

            self.assertIn("kept", new_session.read_text())
            # The legacy file is left untouched rather than risk overwriting.
            self.assertTrue(legacy_session_path(image).exists())


class ResolveForReadTests(unittest.TestCase):
    def test_prefers_new_then_legacy_then_new_default(self) -> None:
        with tempfile.TemporaryDirectory(prefix="edit_storage_") as temp_dir:
            folder = Path(temp_dir)
            image = folder / "_DSC1.jpg"
            image.write_bytes(b"original")

            # Nothing on disk -> defaults to the new write target.
            self.assertEqual(resolve_session_for_read(image), editor_session_path(image))

            # Only a legacy bundle -> read the legacy file.
            legacy = legacy_session_path(image)
            legacy.write_text('{"version": 1}')
            self.assertEqual(resolve_session_for_read(image), legacy)

            # New bundle present -> prefer it over legacy.
            new_session = editor_session_path(image)
            new_session.parent.mkdir(parents=True, exist_ok=True)
            new_session.write_text('{"version": 1}')
            self.assertEqual(resolve_session_for_read(image), new_session)


class ConsolidateFolderTests(unittest.TestCase):
    def test_sweeps_every_bundle_in_a_folder(self) -> None:
        with tempfile.TemporaryDirectory(prefix="edit_storage_") as temp_dir:
            folder = Path(temp_dir)
            image_a = _write_legacy_bundle(folder, "_DSC1")
            image_b = _write_legacy_bundle(folder, "_DSC2", with_assets=False)

            moved = consolidate_folder(folder)

            self.assertEqual(moved, 2)
            self.assertTrue(editor_session_path(image_a).exists())
            self.assertTrue(editor_session_path(image_b).exists())
            self.assertFalse(legacy_session_path(image_a).exists())
            self.assertFalse((folder / "_DSC1.edit-assets").exists())

    def test_sweep_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="edit_storage_") as temp_dir:
            folder = Path(temp_dir)
            _write_legacy_bundle(folder, "_DSC1")

            self.assertEqual(consolidate_folder(folder), 1)
            self.assertEqual(consolidate_folder(folder), 0)


def _write_session(session_path: Path, *, operations=(), masks=()) -> None:
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(
        json.dumps(
            {
                "version": SCHEMA_VERSION,
                "schema": SCHEMA_NAME,
                "operations": list(operations),
                "masks": list(masks),
            }
        )
    )


class SessionHasEditsTests(unittest.TestCase):
    def test_no_session_file_returns_false(self) -> None:
        with tempfile.TemporaryDirectory(prefix="edit_storage_") as temp_dir:
            image = Path(temp_dir) / "_DSC1.jpg"
            image.write_bytes(b"original")

            self.assertFalse(session_has_edits(image))

    def test_opened_but_untouched_session_returns_false(self) -> None:
        with tempfile.TemporaryDirectory(prefix="edit_storage_") as temp_dir:
            image = Path(temp_dir) / "_DSC1.jpg"
            image.write_bytes(b"original")
            _write_session(editor_session_path(image))

            self.assertFalse(session_has_edits(image))

    def test_session_with_operations_returns_true(self) -> None:
        with tempfile.TemporaryDirectory(prefix="edit_storage_") as temp_dir:
            image = Path(temp_dir) / "_DSC1.jpg"
            image.write_bytes(b"original")
            _write_session(
                editor_session_path(image),
                operations=[{"id": "op-1", "type": "adjust.exposure", "params": {"exposure": 0.5}}],
            )

            self.assertTrue(session_has_edits(image))

    def test_session_with_masks_returns_true(self) -> None:
        with tempfile.TemporaryDirectory(prefix="edit_storage_") as temp_dir:
            image = Path(temp_dir) / "_DSC1.jpg"
            image.write_bytes(b"original")
            _write_session(
                editor_session_path(image),
                masks=[{"id": "mask-1", "type": "radial", "params": {}}],
            )

            self.assertTrue(session_has_edits(image))

    def test_corrupt_session_file_returns_false_not_a_crash(self) -> None:
        with tempfile.TemporaryDirectory(prefix="edit_storage_") as temp_dir:
            image = Path(temp_dir) / "_DSC1.jpg"
            image.write_bytes(b"original")
            session_path = editor_session_path(image)
            session_path.parent.mkdir(parents=True, exist_ok=True)
            session_path.write_text("{not valid json")

            self.assertFalse(session_has_edits(image))


if __name__ == "__main__":
    unittest.main()
