from __future__ import annotations

from pathlib import Path

from image_triage.main import qml_argv_from_launch


def test_retired_ui_selectors_cannot_launch_widgets(monkeypatch):
    import sys
    from image_triage import main as app
    from image_triage.qml_shell import app as qml
    seen = []
    monkeypatch.setenv("IMAGE_TRIAGE_UI", "widgets")
    monkeypatch.setattr(sys, "argv", ["ImageTriage.exe", "--widgets", "--widgets-popout"])
    monkeypatch.setattr(qml, "main", lambda args: seen.append(args) or 0)
    assert app.main() == 0
    assert seen == [[]]
    assert not hasattr(app, "widgets_main")


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
