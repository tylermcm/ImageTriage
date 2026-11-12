from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from image_triage.frozen_bootstrap import configure_frozen_dll_search


configure_frozen_dll_search()

from image_triage.formats import is_image_file_candidate


def _configure_windows_app_identity() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(  # type: ignore[attr-defined]
            "ImageTriage.Desktop"
        )
    except (AttributeError, OSError):
        pass


def launch_target_from_argv(argv: Sequence[str]) -> str:
    for raw_argument in argv[1:]:
        candidate = str(raw_argument).strip().strip('"')
        if candidate:
            return candidate
    return ""


def is_quick_view_launch_target(target: str) -> bool:
    """Return whether a shell launch names a photo: the app then opens its folder with the grid on that photo."""

    candidate = str(target or "").strip()
    return bool(candidate and is_image_file_candidate(candidate) and Path(candidate).is_file())


def qml_argv_from_launch(argv: Sequence[str]) -> list[str]:
    """Translate a shell launch (``ImageTriage.exe <folder or image>``, as file associations send it) into QML shell arguments."""

    qml_args = list(argv[1:])
    if not qml_args or qml_args[0].startswith("-"):
        return qml_args
    target = Path(qml_args[0].strip().strip('"'))
    rest = qml_args[1:]
    if target.is_file():
        # A photo was opened: its folder, with the grid on that photo. Any other file just opens the folder it is in.
        select = ["--select", str(target)] if is_quick_view_launch_target(str(target)) else []
        return [*rest, "--folder", str(target.parent), *select]
    return [*rest, "--folder", str(target)] if str(target) else rest


def main() -> int:
    """Start the QML application."""
    from image_triage.qml_shell.app import main as qml_main

    return qml_main(qml_argv_from_launch(sys.argv))


if __name__ == "__main__":
    raise SystemExit(main())
