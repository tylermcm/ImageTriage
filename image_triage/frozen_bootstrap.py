from __future__ import annotations

import os
import sys
from pathlib import Path


_DLL_DIRECTORY_HANDLES: list[object] = []


def _frozen_app_root() -> Path | None:
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve().parent


def configure_frozen_stdlib() -> None:
    """Expose the full stdlib bundled for isolated AI helper processes."""
    app_root = _frozen_app_root()
    if app_root is None:
        return

    stdlib_dir = app_root / "ai_stdlib"
    if not stdlib_dir.is_dir():
        return

    stdlib_text = str(stdlib_dir)
    if stdlib_text in sys.path:
        sys.path.remove(stdlib_text)
    sys.path.insert(0, stdlib_text)


def configure_frozen_dll_search() -> None:
    """Register cx_Freeze's shared library directory before native imports."""
    if sys.platform != "win32":
        return

    app_root = _frozen_app_root()
    if app_root is None:
        return

    lib_dir = app_root / "lib"
    if not lib_dir.is_dir():
        return

    lib_text = str(lib_dir)
    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    if lib_text.casefold() not in {entry.casefold() for entry in path_entries if entry}:
        os.environ["PATH"] = (
            lib_text if not os.environ.get("PATH") else lib_text + os.pathsep + os.environ["PATH"]
        )

    add_dll_directory = getattr(os, "add_dll_directory", None)
    if add_dll_directory is None:
        return
    try:
        handle = add_dll_directory(lib_text)
    except OSError:
        return
    _DLL_DIRECTORY_HANDLES.append(handle)
