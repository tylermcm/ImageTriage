"""Characterization for WI-4.4 slice 4: folder create/rename/move/delete
prompts moved out of MainWindow into image_triage.folder_ops_controller.

This code path had zero existing test coverage before this slice.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QMessageBox

from image_triage.folder_ops_controller import FolderOpsController
from tests.harness import make_jpegs, open_folder


def test_create_folder_prompt_creates_and_remembers_destination(main_window, tmp_path) -> None:
    with patch("image_triage.folder_ops_controller.QInputDialog.getText", return_value=("New Kid", True)):
        created = main_window._create_folder_prompt(str(tmp_path), select_created=False)

    assert created is not None
    assert Path(created).is_dir()
    assert Path(created).name == "New Kid"


def test_create_folder_prompt_cancelled_creates_nothing(main_window, tmp_path) -> None:
    with patch("image_triage.folder_ops_controller.QInputDialog.getText", return_value=("", False)):
        created = main_window._create_folder_prompt(str(tmp_path), select_created=False)

    assert created is None
    assert list(tmp_path.iterdir()) == []


def test_rename_folder_remaps_favorites_and_recent_lists(main_window, tmp_path) -> None:
    folder = tmp_path / "before"
    folder.mkdir()
    make_jpegs(folder, ["a.jpg"])
    main_window._favorites = [str(folder)]
    main_window._recent_destinations = [str(folder)]
    main_window._recent_folders = [str(folder)]

    with patch("image_triage.folder_ops_controller.QInputDialog.getText", return_value=("after", True)):
        main_window._rename_folder(str(folder))

    renamed = tmp_path / "after"
    assert renamed.is_dir() and not folder.exists()
    assert main_window._favorites == [str(renamed)]
    assert main_window._recent_destinations == [str(renamed)]
    assert main_window._recent_folders == [str(renamed)]


def test_delete_folder_prompt_removes_folder_and_cleans_up_lists(main_window, tmp_path) -> None:
    folder = tmp_path / "gone"
    folder.mkdir()
    make_jpegs(folder, ["a.jpg"])
    main_window._favorites = [str(folder)]
    main_window._recent_destinations = [str(folder)]
    main_window._recent_folders = [str(folder)]

    with patch(
        "image_triage.folder_ops_controller.QMessageBox.question",
        return_value=QMessageBox.StandardButton.Yes,
    ):
        main_window._delete_folder_prompt(str(folder))

    assert not folder.exists()
    assert main_window._favorites == []
    assert main_window._recent_destinations == []
    assert main_window._recent_folders == []


def test_delete_folder_prompt_declined_keeps_the_folder(main_window, tmp_path) -> None:
    folder = tmp_path / "keep_me"
    folder.mkdir()

    with patch(
        "image_triage.folder_ops_controller.QMessageBox.question",
        return_value=QMessageBox.StandardButton.No,
    ):
        main_window._delete_folder_prompt(str(folder))

    assert folder.exists()


def test_delete_folder_prompt_refuses_a_filesystem_root(tmp_path) -> None:
    controller = FolderOpsController(window=None)
    root = str(Path(tmp_path).anchor or tmp_path)
    with patch("image_triage.folder_ops_controller.QMessageBox.question") as fake_question:
        controller.delete_folder_prompt(root)
    fake_question.assert_not_called()


def test_move_folder_prompt_updates_current_folder_when_moving_the_open_folder(main_window, tmp_path) -> None:
    source = tmp_path / "src"
    make_jpegs(source, ["a.jpg"])
    destination_parent = tmp_path / "dest_parent"
    destination_parent.mkdir()
    open_folder(main_window, source, 1)

    with patch(
        "image_triage.folder_ops_controller.QFileDialog.getExistingDirectory",
        return_value=str(destination_parent),
    ):
        main_window._move_folder_prompt(str(source))

    moved = destination_parent / "src"
    assert moved.is_dir() and not source.exists()
    assert main_window._current_folder == str(moved)


def test_remap_folder_path_is_pure_and_ignores_unrelated_paths() -> None:
    mapped = FolderOpsController.remap_folder_path(
        "C:/shots/src/sub/a.jpg", "C:/shots/src", "C:/shots/dest"
    )
    assert mapped == str(Path("C:/shots/dest/sub/a.jpg"))

    unrelated = FolderOpsController.remap_folder_path(
        "C:/other/a.jpg", "C:/shots/src", "C:/shots/dest"
    )
    assert unrelated == "C:/other/a.jpg"
