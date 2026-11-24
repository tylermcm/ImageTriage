"""The PhotoCraft editor is part of the installed program: the installer carries it (photocraft/photocraft.exe beside the app, with its licences)
and the installed app uses that copy before any other."""
from __future__ import annotations

import os
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
    editor = tmp_path / "photocraft" / shell_actions.PHOTOCRAFT_EXE_NAME
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


def test_the_installed_program_ignores_a_named_editor_and_development_builds(installed, monkeypatch, tmp_path):
    # IMAGE_TRIAGE_PHOTOCRAFT_EXE left pointing at an old debug build made the installed app save every edit with it (sunlit rock
    # saved bright red while the preview showed it right): the installed program uses the editor it was built with, only that one
    chosen = tmp_path / "old-debug-build.exe"
    chosen.write_bytes(b"chosen")
    monkeypatch.setenv("IMAGE_TRIAGE_PHOTOCRAFT_EXE", str(chosen))
    assert Path(shell_actions.detect_photocraft_executable()) == installed.resolve()
    assert shell_actions.companion_photocraft_executables() == []      # nor as a fallback when the editor will not start


def test_running_from_source_a_named_editor_still_wins(monkeypatch, tmp_path):
    monkeypatch.delattr(sys, "frozen", raising=False)
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
def checkout(tmp_path, monkeypatch):
    """A PhotoCraft checkout with a release build and its licence files (and no real checkout beside this repository to fall back on)."""
    root = tmp_path / "photocraft"
    exe = root / "target" / "release" / freeze_support.PHOTOCRAFT_EXE_NAME
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"editor")
    (root / "Cargo.toml").write_text("[workspace]\n")
    for name in freeze_support.PHOTOCRAFT_LICENSE_FILES:
        (root / name).write_text(name)
    monkeypatch.setattr(freeze_support, "ROOT", tmp_path / "ImageTriage")
    monkeypatch.setattr(freeze_support.Path, "home", staticmethod(lambda: tmp_path / "home"))
    monkeypatch.delenv(freeze_support.PHOTOCRAFT_CHECKOUT_ENV, raising=False)
    return exe


def test_the_installer_gets_the_editor_and_its_licences(checkout, monkeypatch):
    monkeypatch.setenv(freeze_support.PHOTOCRAFT_BUNDLE_ENV, str(checkout))
    files = dict((destination, Path(source)) for source, destination in freeze_support.photocraft_bundle_files())
    assert files[f"photocraft/{freeze_support.PHOTOCRAFT_EXE_NAME}"] == checkout.resolve()
    for name in freeze_support.PHOTOCRAFT_LICENSE_FILES:
        assert files[f"photocraft/{name}"].read_text() == name


def test_a_licence_missing_stops_the_build(checkout, monkeypatch):
    # An installer that carries the editor without its licences breaks them (the Mac build used to, with only a warning)
    (checkout.parents[2] / "NOTICE").unlink()
    monkeypatch.setenv(freeze_support.PHOTOCRAFT_BUNDLE_ENV, str(checkout))
    with pytest.raises(SystemExit, match=freeze_support.PHOTOCRAFT_CHECKOUT_ENV):
        freeze_support.photocraft_bundle_files()


def test_an_editor_built_outside_its_checkout_gets_the_checkouts_licences(checkout, tmp_path, monkeypatch):
    # The Mac build: CARGO_TARGET_DIR=$BUILD/photocraft-target, and build_macos.sh names the checkout
    outside = tmp_path / "build" / "photocraft-target" / "aarch64-apple-darwin" / "release" / "photocraft"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"editor")
    monkeypatch.setenv(freeze_support.PHOTOCRAFT_BUNDLE_ENV, str(outside))
    monkeypatch.setattr(freeze_support, "ROOT", tmp_path / "elsewhere" / "ImageTriage")   # no companion checkout beside it
    with pytest.raises(SystemExit):
        freeze_support.photocraft_bundle_files()
    monkeypatch.setenv(freeze_support.PHOTOCRAFT_CHECKOUT_ENV, str(checkout.parents[2]))
    files = dict((destination, Path(source)) for source, destination in freeze_support.photocraft_bundle_files())
    for name in freeze_support.PHOTOCRAFT_LICENSE_FILES:
        assert files[f"photocraft/{name}"].read_text() == name


def test_the_licence_and_notices_ship_at_the_root_and_the_notices_are_written_for_the_build(checkout, monkeypatch):
    calls = []

    def run(command, **_kwargs):
        calls.append(command)
        Path(command[command.index("--out") + 1]).write_text("notices")
        return type("Done", (), {"returncode": 0})()

    monkeypatch.setenv(freeze_support.PHOTOCRAFT_BUNDLE_ENV, str(checkout))
    monkeypatch.setattr("subprocess.run", run)
    files = {destination: Path(source) for source, destination in freeze_support.licence_bundle_files()}
    assert set(files) == {"LICENSE", "THIRD_PARTY_NOTICES.txt"} and files["THIRD_PARTY_NOTICES.txt"].read_text() == "notices"
    assert calls[0][calls[0].index("--photocraft") + 1] == str(checkout.parents[2])
    monkeypatch.setattr("subprocess.run", lambda command, **_k: type("Failed", (), {"returncode": 1})())
    files["THIRD_PARTY_NOTICES.txt"].unlink()
    with pytest.raises(SystemExit, match="notices"):
        freeze_support.licence_bundle_files()


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


# ---- the RAW preview worker ------------------------------------------------------------------------------------------------------


def test_the_installer_gets_the_raw_preview_worker_beside_the_app(tmp_path, monkeypatch):
    worker = tmp_path / freeze_support.RAW_PREVIEW_EXE_NAME
    worker.write_bytes(b"worker")
    monkeypatch.setenv(freeze_support.RAW_PREVIEW_BUNDLE_ENV, str(worker))
    monkeypatch.delenv(freeze_support.RAW_PREVIEW_BUNDLE_SWITCH_ENV, raising=False)
    # beside ImageTriage.exe, under the name the app looks for
    assert freeze_support.raw_preview_bundle_files() == [(str(worker.resolve()), freeze_support.RAW_PREVIEW_EXE_NAME)]
    text = (Path(freeze_support.__file__).parent / "setup_msi.py").read_text(encoding="utf-8")
    assert "+ raw_preview_bundle_files()" in text
    from image_triage.qml_shell import raw_preview

    assert raw_preview.EXECUTABLE_NAME == freeze_support.RAW_PREVIEW_EXE_NAME
    assert "with_name(EXECUTABLE_NAME)" in Path(raw_preview.__file__).read_text(encoding="utf-8")
    assert raw_preview.EXECUTABLE_NAME.endswith(".exe") == (os.name == "nt")
    linux = (Path(freeze_support.__file__).parent / "setup_linux.py").read_text(encoding="utf-8")
    assert "raw_preview_bundle_files()" in linux


@pytest.mark.parametrize("named", [None, "nothing.exe"])
def test_a_build_with_no_raw_preview_worker_stops_unless_asked_to_go_without(tmp_path, monkeypatch, capsys, named):
    monkeypatch.delenv(freeze_support.RAW_PREVIEW_BUNDLE_SWITCH_ENV, raising=False)
    if named is None:
        monkeypatch.delenv(freeze_support.RAW_PREVIEW_BUNDLE_ENV, raising=False)
    else:
        monkeypatch.setenv(freeze_support.RAW_PREVIEW_BUNDLE_ENV, str(tmp_path / named))
    with pytest.raises(SystemExit) as stopped:
        freeze_support.raw_preview_bundle_files()
    assert freeze_support.RAW_PREVIEW_BUNDLE_ENV in str(stopped.value) and freeze_support.RAW_PREVIEW_BUNDLE_SWITCH_ENV in str(stopped.value)
    monkeypatch.setenv(freeze_support.RAW_PREVIEW_BUNDLE_SWITCH_ENV, "0")
    assert freeze_support.raw_preview_bundle_files() == []
    assert "RAW preview worker out" in capsys.readouterr().out
