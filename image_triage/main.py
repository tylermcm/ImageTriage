from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from image_triage.frozen_bootstrap import configure_frozen_dll_search


configure_frozen_dll_search()

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QIcon, QImageReader
from PySide6.QtWidgets import QApplication

from image_triage.updater import current_app_version


_APP_ICON_PATH = Path(__file__).resolve().parent / "ui" / "assets" / "app_icon-v2.ico"
# One deployment switch controls whether the preserved splash is shown.
_STARTUP_SPLASH_ENABLED = True


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


def main() -> int:
    _configure_windows_app_identity()
    QCoreApplication.setOrganizationName("Codex")
    QCoreApplication.setApplicationName("Image Triage")
    QCoreApplication.setApplicationVersion(current_app_version())
    # Large edited derivatives can legitimately exceed Qt's conservative default.
    QImageReader.setAllocationLimit(1024)

    app = QApplication(sys.argv)
    app.setApplicationDisplayName("Image Triage")
    app.setWindowIcon(QIcon(str(_APP_ICON_PATH)))

    splash = None
    if _STARTUP_SPLASH_ENABLED:
        from image_triage.ui.splash_screen import StartupSplash

        splash = StartupSplash(QCoreApplication.applicationVersion())
        splash.show_centered()
        app.processEvents()
        splash.set_status("Loading workspace…", 22)
        app.processEvents()

    from image_triage.window import MainWindow

    if splash is not None:
        splash.set_status("Restoring your library…", 58)
        app.processEvents()
    window = MainWindow(launch_target=launch_target_from_argv(sys.argv))
    if splash is not None:
        splash.set_status("Ready", 100)
        app.processEvents()
        splash.finish(window)
    else:
        window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
