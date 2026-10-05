"""Characterization for WI-4.4 slice 5: drive-type detection and the
per-drive "recycle bin" (distinct from the OS trash) moved out of MainWindow
into image_triage.recycle_bin_controller.RecycleBinController.

The move/restore round trip through the annotation controller's delete_record and _undo_last_action is
already covered end-to-end by tests/test_char_move_delete_undo.py (which
patches the _recycle_root_for_folder delegate directly, so it continues to
exercise this controller unchanged). This file covers the pieces that had
no coverage anywhere: _empty_recycle_bin and the manifest read/write cycle.
"""
from __future__ import annotations

from unittest.mock import patch

from PySide6.QtWidgets import QMessageBox

from image_triage.file_ops import FileMove
from tests.harness import make_jpegs, open_folder


def test_empty_recycle_bin_does_nothing_off_a_removable_drive(main_window, tmp_path) -> None:
    make_jpegs(tmp_path, ["a.jpg"])
    open_folder(main_window, tmp_path, 1)

    with patch.object(main_window._recycle_bin, "is_temporary_storage_folder", return_value=False):
        main_window._recycle_bin.empty_recycle_bin()

    assert "removable-drive" in main_window.statusBar().currentMessage()


def test_empty_recycle_bin_reports_already_empty(main_window, tmp_path) -> None:
    recycle_root = tmp_path / "recycle bin"

    with patch.object(main_window._recycle_bin, "is_temporary_storage_folder", return_value=True), patch.object(
        main_window._recycle_bin, "recycle_root_for_folder", return_value=recycle_root
    ):
        main_window._recycle_bin.empty_recycle_bin()

    assert "already empty" in main_window.statusBar().currentMessage()


def test_empty_recycle_bin_deletes_contents_after_confirmation(main_window, tmp_path) -> None:
    recycle_root = tmp_path / "recycle bin"
    make_jpegs(recycle_root, ["trashed.jpg"])

    with patch.object(main_window._recycle_bin, "is_temporary_storage_folder", return_value=True), patch.object(
        main_window._recycle_bin, "recycle_root_for_folder", return_value=recycle_root
    ), patch(
        "image_triage.recycle_bin_controller.QMessageBox.warning",
        return_value=QMessageBox.StandardButton.Yes,
    ):
        main_window._recycle_bin.empty_recycle_bin()

    assert not recycle_root.exists()


def test_empty_recycle_bin_declined_keeps_contents(main_window, tmp_path) -> None:
    recycle_root = tmp_path / "recycle bin"
    make_jpegs(recycle_root, ["trashed.jpg"])

    with patch.object(main_window._recycle_bin, "is_temporary_storage_folder", return_value=True), patch.object(
        main_window._recycle_bin, "recycle_root_for_folder", return_value=recycle_root
    ), patch(
        "image_triage.recycle_bin_controller.QMessageBox.warning",
        return_value=QMessageBox.StandardButton.No,
    ):
        main_window._recycle_bin.empty_recycle_bin()

    assert recycle_root.exists() and (recycle_root / "trashed.jpg").exists()


def test_recycle_manifest_round_trips_remember_and_forget(main_window, tmp_path) -> None:
    recycle_root = tmp_path / "recycle bin"
    recycle_root.mkdir()
    controller = main_window._recycle_bin

    with patch.object(controller, "recycle_root_for_folder", return_value=recycle_root), patch.object(
        main_window, "_is_recycle_folder", return_value=True
    ):
        moves = (FileMove(source_path=str(tmp_path / "a.jpg"), target_path=str(recycle_root / "a.jpg")),)
        controller.remember_recycle_origins(moves)
        manifest = controller.load_recycle_manifest()
        assert manifest == {str(recycle_root / "a.jpg"): str(tmp_path / "a.jpg")}

        controller.forget_recycle_origins((str(recycle_root / "a.jpg"),))
        assert controller.load_recycle_manifest() == {}


def test_drive_type_is_cached_per_root() -> None:
    from image_triage.recycle_bin_controller import RecycleBinController

    controller = RecycleBinController(window=None)
    with patch(
        "image_triage.recycle_bin_controller.ctypes.windll.kernel32.GetDriveTypeW", return_value=3
    ) as fake_get_drive_type:
        first = controller.drive_type("C:\\")
        second = controller.drive_type("C:\\")

    assert first == second == 3
    fake_get_drive_type.assert_called_once()
