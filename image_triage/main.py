from __future__ import annotations

import os
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


def mac_finder_launch(argv: Sequence[str]) -> list[str]:
    """macOS's Finder starts the app with no file arguments (older systems add a ``-psn_`` process number) and sends the file it opened
    as an event once the app is up. Make the app, wait a moment for that event, and return the launch it amounts to:
    ``[argv[0], <file or folder>]``, or ``argv`` as it was when nothing was opened (the app was started on its own)."""
    if sys.platform != "darwin" or any(a and not str(a).startswith("-psn_") for a in argv[1:]):
        return list(argv)
    from image_triage.file_open_events import install, wait_for_opened_file
    from image_triage.quick_view import application

    app = application()
    listener = install(app)
    opened = wait_for_opened_file(app, listener)
    app.removeEventFilter(listener)                     # the app that starts listens for later files itself
    return [argv[0], opened] if opened else [argv[0]]


def main() -> int:
    """Start the QML application, or the quick viewer for a photo opened from the file manager (``image_triage.quick_view``)."""
    if sys.platform == "darwin":
        sys.argv = mac_finder_launch(sys.argv)
    target = launch_target_from_argv(sys.argv)
    if len(sys.argv) == 2 and not target.startswith("-") and is_quick_view_launch_target(target):
        from image_triage.quick_view import run as quick_view

        code = quick_view(os.path.abspath(target))
        if code is not None:
            return code
    from image_triage.qml_shell.app import main as qml_main

    return qml_main(qml_argv_from_launch(sys.argv))


if __name__ == "__main__":
    raise SystemExit(main())
