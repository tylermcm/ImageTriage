from __future__ import annotations

from pathlib import Path

from image_triage.main import qml_argv_from_launch, wants_widgets_ui


def test_qml_shell_is_the_default_ui():
    assert not wants_widgets_ui(["ImageTriage.exe"], {})
    assert not wants_widgets_ui(["ImageTriage.exe", "D:/Photos"], {"IMAGE_TRIAGE_UI": ""})


def test_widgets_ui_is_opt_in_by_flag_or_environment():
    assert wants_widgets_ui(["ImageTriage.exe", "--widgets"], {})
    assert wants_widgets_ui(["ImageTriage.exe"], {"IMAGE_TRIAGE_UI": "Widgets"})


def test_launch_without_arguments_passes_nothing_on():
    assert qml_argv_from_launch(["ImageTriage.exe"]) == []


def test_flags_pass_through_untouched():
    assert qml_argv_from_launch(["ImageTriage.exe", "--light", "--size", "1200x800"]) == ["--light", "--size", "1200x800"]


def test_folder_target_becomes_folder_option(tmp_path: Path):
    assert qml_argv_from_launch(["ImageTriage.exe", str(tmp_path)]) == ["--folder", str(tmp_path)]


def test_image_target_opens_its_folder(tmp_path: Path):
    photo = tmp_path / "DSC_0001.jpg"
    photo.write_bytes(b"")
    assert qml_argv_from_launch(["ImageTriage.exe", str(photo)]) == ["--folder", str(tmp_path)]
