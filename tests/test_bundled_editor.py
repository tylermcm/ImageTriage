"""The PhotoCraft editor is part of the installed program: the installer carries it (photocraft/photocraft.exe beside the app, with its licences)
and the installed app uses that copy before any other."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

import freeze_support
from image_triage import shell_actions


# --- finding the editor inside the installed program ------------------------------------------------------------------

@pytest.fixture(autouse=True)
def fresh_detection():
    """``detect_photocraft_executable`` remembers its answer for the life of the program: not from one test to the next."""
    shell_actions.detect_photocraft_executable.cache_clear()
    yield
    shell_actions.detect_photocraft_executable.cache_clear()


@pytest.fixture
def installed(tmp_path, monkeypatch):
    """A frozen app in ``tmp_path`` with an editor beside it."""
    app = tmp_path / "ImageTriage.exe"
    app.write_bytes(b"app")
    editor = tmp_path / "photocraft" / "photocraft.exe"
    editor.parent.mkdir()
    editor.write_bytes(b"editor")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(app))
    monkeypatch.delenv("IMAGE_TRIAGE_PHOTOCRAFT_EXE", raising=False)
    return editor


def test_the_installed_program_finds_its_editor(installed):
    assert Path(shell_actions.bundled_photocraft_executable()) == installed.resolve()


def test_a_program_that_is_not_installed_has_none(installed, monkeypatch):
    monkeypatch.delattr(sys, "frozen")
    assert shell_actions.bundled_photocraft_executable() is None


def test_an_installed_program_whose_editor_is_missing_has_none(installed):
    installed.unlink()
    assert shell_actions.bundled_photocraft_executable() is None


def test_the_bundled_editor_comes_before_a_development_checkout_and_the_path(installed, monkeypatch):
    other = installed.parent.parent / "checkout-build.exe"
    other.write_bytes(b"other")
    monkeypatch.setattr(shell_actions, "companion_photocraft_executables", lambda: [str(other)])
    monkeypatch.setattr(shell_actions.shutil, "which", lambda name: str(other))
    assert Path(shell_actions.detect_photocraft_executable()) == installed.resolve()


def test_a_named_editor_still_wins(installed, monkeypatch, tmp_path):
    chosen = tmp_path / "chosen.exe"
    chosen.write_bytes(b"chosen")
    monkeypatch.setenv("IMAGE_TRIAGE_PHOTOCRAFT_EXE", str(chosen))
    assert shell_actions.detect_photocraft_executable() == str(chosen)


def test_running_from_source_looks_where_it_always_did(monkeypatch, tmp_path):
    monkeypatch.delenv("IMAGE_TRIAGE_PHOTOCRAFT_EXE", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    found = tmp_path / "companion.exe"
    found.write_bytes(b"x")
    monkeypatch.setattr(shell_actions, "companion_photocraft_executables", lambda: [str(found)])
    assert shell_actions.detect_photocraft_executable() == str(found)


# --- putting it in the installer ------------------------------------------------------------------------------------

@pytest.fixture
def checkout(tmp_path):
    """A PhotoCraft checkout with a release build and its licence files."""
    root = tmp_path / "photocraft"
    exe = root / "target" / "release" / "photocraft.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"editor")
    for name in freeze_support.PHOTOCRAFT_LICENSE_FILES:
        (root / name).write_text(name)
    return exe


def test_the_installer_gets_the_editor_and_its_licences(checkout, monkeypatch):
    monkeypatch.setenv(freeze_support.PHOTOCRAFT_BUNDLE_ENV, str(checkout))
    files = dict((destination, Path(source)) for source, destination in freeze_support.photocraft_bundle_files())
    assert files["photocraft/photocraft.exe"] == checkout.resolve()
    for name in freeze_support.PHOTOCRAFT_LICENSE_FILES:
        assert files[f"photocraft/{name}"].read_text() == name


def test_a_licence_missing_is_a_warning_and_not_the_end_of_the_build(checkout, monkeypatch, capsys):
    (checkout.parents[2] / "NOTICE").unlink()
    monkeypatch.setenv(freeze_support.PHOTOCRAFT_BUNDLE_ENV, str(checkout))
    destinations = {destination for _source, destination in freeze_support.photocraft_bundle_files()}
    assert "photocraft/photocraft.exe" in destinations and "photocraft/NOTICE" not in destinations
    assert "NOTICE was not found" in capsys.readouterr().out


def test_the_editor_is_found_in_the_companion_checkout_when_not_named(checkout, monkeypatch):
    monkeypatch.delenv(freeze_support.PHOTOCRAFT_BUNDLE_ENV, raising=False)
    monkeypatch.setattr(freeze_support, "ROOT", checkout.parents[3] / "ImageTriage")
    assert freeze_support.photocraft_bundle_source() == checkout


def test_a_build_with_no_editor_stops_and_says_what_to_do(tmp_path, monkeypatch):
    monkeypatch.setenv(freeze_support.PHOTOCRAFT_BUNDLE_ENV, str(tmp_path / "nothing.exe"))
    with pytest.raises(SystemExit) as stopped:
        freeze_support.photocraft_bundle_files()
    message = str(stopped.value)
    assert "cargo build --release -p photocraft" in message and freeze_support.PHOTOCRAFT_BUNDLE_SWITCH_ENV in message


def test_an_installer_without_the_editor_has_to_be_asked_for(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv(freeze_support.PHOTOCRAFT_BUNDLE_ENV, str(tmp_path / "nothing.exe"))
    monkeypatch.setenv(freeze_support.PHOTOCRAFT_BUNDLE_SWITCH_ENV, "0")
    assert freeze_support.photocraft_bundle_files() == []
    assert "no editor" in capsys.readouterr().out


def test_the_installer_script_includes_what_the_bundle_function_returns():
    text = (Path(freeze_support.__file__).parent / "setup_msi.py").read_text(encoding="utf-8")
    assert "photocraft_bundle_files()" in text and '"include_files": [*freeze_assets.include_files, *photocraft_bundle_files()]' in text
