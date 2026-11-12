from __future__ import annotations

from pathlib import Path
import pytest

from image_triage.main import qml_argv_from_launch


@pytest.mark.parametrize("selector", ["--widgets", "--widgets-popout"])
def test_removed_ui_selectors_are_not_accepted(selector):
    from image_triage.qml_shell.app import main
    with pytest.raises(SystemExit) as error:
        main([selector])
    assert error.value.code == 2


def test_main_launches_only_qml(monkeypatch):
    import sys
    from image_triage import main as app
    from image_triage.qml_shell import app as qml
    seen = []
    monkeypatch.setattr(sys, "argv", ["ImageTriage.exe"])
    monkeypatch.setattr(qml, "main", lambda args: seen.append(args) or 0)
    assert app.main() == 0
    assert seen == [[]]


def test_launch_without_arguments_passes_nothing_on():
    assert qml_argv_from_launch(["ImageTriage.exe"]) == []


def test_flags_pass_through_untouched():
    assert qml_argv_from_launch(["ImageTriage.exe", "--light", "--size", "1200x800"]) == ["--light", "--size", "1200x800"]


def test_folder_target_becomes_folder_option(tmp_path: Path):
    assert qml_argv_from_launch(["ImageTriage.exe", str(tmp_path)]) == ["--folder", str(tmp_path)]


def test_image_target_opens_its_folder(tmp_path: Path):
    photo = tmp_path / "DSC_0001.jpg"
    photo.write_bytes(b"")
    assert qml_argv_from_launch(["ImageTriage.exe", str(photo)]) == ["--folder", str(tmp_path), "--select", str(photo)]
    notes = tmp_path / "notes.txt"
    notes.write_bytes(b"")
    assert qml_argv_from_launch(["ImageTriage.exe", str(notes)]) == ["--folder", str(tmp_path)]      # not a photo: only its folder
