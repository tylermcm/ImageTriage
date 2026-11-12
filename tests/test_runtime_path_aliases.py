"""Alternate spellings of a runtime directory must not duplicate search paths."""
import os
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from image_triage import frozen_bootstrap
from test_ai_python_runner import _load_runner_module


@pytest.fixture
def runtime_alias(tmp_path):
    root = tmp_path / "runtime installation"
    root.mkdir()
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.GetShortPathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        api.GetShortPathNameW.restype = wintypes.DWORD
        size = api.GetShortPathNameW(str(root), None, 0)
        if not size:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_unicode_buffer(size)
        if not api.GetShortPathNameW(str(root), buffer, size):
            raise ctypes.WinError(ctypes.get_last_error())
        alias = Path(buffer.value)
        if os.path.normcase(str(alias)) == os.path.normcase(str(root)):
            pytest.skip("This volume does not provide a distinct Windows 8.3 name")
    else:
        alias = tmp_path / "alias"
        alias.symlink_to(root, target_is_directory=True)
    assert alias.resolve() == root.resolve()
    return root.resolve(), alias


def test_frozen_stdlib_replaces_all_aliases_without_changing_other_entries(runtime_alias, monkeypatch):
    root, alias = runtime_alias
    stdlib = root / "ai_stdlib"
    stdlib.mkdir()
    monkeypatch.chdir(stdlib)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(alias / "helper.exe"))
    monkeypatch.setattr(sys, "path", [str(alias / "ai_stdlib"), "library.zip", str(stdlib), ""])
    frozen_bootstrap.configure_frozen_stdlib()
    assert sys.path == [str(stdlib), "library.zip", ""]


def test_frozen_dll_path_does_not_duplicate_an_alias(runtime_alias, monkeypatch):
    root, alias = runtime_alias
    lib = root / "lib"
    lib.mkdir()
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(root / "app.exe"))
    monkeypatch.setattr(frozen_bootstrap, "_DLL_DIRECTORY_HANDLES", [])
    register = Mock(return_value=object())
    monkeypatch.setattr(os, "add_dll_directory", register, raising=False)
    monkeypatch.setenv("PATH", str(alias / "lib"))
    frozen_bootstrap.configure_frozen_dll_search()
    assert len(os.environ["PATH"].split(os.pathsep)) == 1
    register.assert_called_once_with(str(lib))


def test_ai_runner_replaces_all_python_search_aliases(runtime_alias, monkeypatch):
    root, alias = runtime_alias
    runner = _load_runner_module()
    monkeypatch.chdir(root)
    monkeypatch.setattr(sys, "path", [str(alias), "library.zip", str(root), ""])
    runner._prepend_path_entry(root)
    assert sys.path == [str(root), "library.zip", ""]


def test_ai_runner_dll_search_does_not_duplicate_an_alias(runtime_alias, monkeypatch):
    root, alias = runtime_alias
    runner = _load_runner_module()
    monkeypatch.setenv("PATH", str(alias))
    monkeypatch.setattr(os, "add_dll_directory", Mock(return_value=object()), raising=False)
    runner._register_binary_search_path(root)
    assert len(os.environ["PATH"].split(os.pathsep)) == 1


def test_aliases_do_not_hide_bundled_packages_shadowing_the_managed_runtime(runtime_alias, monkeypatch):
    root, alias = runtime_alias
    managed, bundled = root / "managed", root / "bundled"
    managed.mkdir()
    bundled.mkdir()
    runner = _load_runner_module()
    monkeypatch.setattr(sys, "path", [str(bundled), str(managed)])
    with pytest.raises(RuntimeError, match="shadow"):
        runner._assert_runtime_precedence([alias / "managed"], [alias / "bundled"])


def test_scanner_resolves_alias_input_to_the_same_photo(runtime_alias):
    from PIL import Image
    from image_triage.scanner import scan_folder
    root, alias = runtime_alias
    image = root / "frame.jpg"
    Image.new("RGB", (16, 12), "teal").save(image)
    for folder in (alias, root):
        records = scan_folder(str(folder))
        assert len(records) == 1
        assert Path(records[0].path) == image
        assert records[0].name == image.name


def test_managed_root_accepts_an_alias_without_changing_its_location(runtime_alias, monkeypatch):
    from image_triage.ai_paths import managed_ai_root
    root, alias = runtime_alias
    monkeypatch.setenv("IMAGE_TRIAGE_AI_ROOT", str(alias))
    assert managed_ai_root() == root


def test_edit_discovery_excludes_stack_members_recorded_through_an_alias(runtime_alias):
    from PIL import Image
    from image_triage.models import ImageRecord
    from image_triage.scanner import discover_edited_paths
    root, alias = runtime_alias
    for name in ("frame.CR3", "frame_1.jpg", "frame_2.jpg"):
        Image.new("RGB", (16, 12), "teal").save(root / name, format="JPEG")
    record = ImageRecord(
        path=str(alias / "frame.CR3"), name="frame.CR3", size=0, modified_ns=0,
        edited_paths=(str(alias / "frame_1.jpg"),),
    )
    assert discover_edited_paths(record) == (str(root / "frame_2.jpg"),)
