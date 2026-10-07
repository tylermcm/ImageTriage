from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from image_triage.frozen_bootstrap import configure_frozen_dll_search


configure_frozen_dll_search()

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QIcon, QImageReader
from PySide6.QtWidgets import QApplication

from image_triage.app_data_migration import migrate_legacy_app_data_once
from image_triage.app_identity import migrate_legacy_settings_once
from image_triage.app_logging import configure_app_logging
from image_triage.formats import is_image_file_candidate
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


def is_quick_view_launch_target(target: str) -> bool:
    """Return whether a shell launch should open directly in the popout viewer."""

    candidate = str(target or "").strip()
    return bool(candidate and is_image_file_candidate(candidate) and Path(candidate).is_file())


def main() -> int:
    configure_app_logging()
    _configure_windows_app_identity()
    # QSettings lives under one identity, HKCU\Software\Image Triage (see
    # app_identity.py). The organisation name below also drives
    # QStandardPaths (thumbnail cache, decisions.sqlite3, and everything
    # under AppDataLocation/AppLocalDataLocation): it used to stay "Codex"
    # here deliberately, because flipping it would silently relocate that
    # data. WI-3.6 replaced that silence with an explicit, verified,
    # one-time migration (migrate_legacy_app_data_once, just below), so the
    # org name can now simply be "Image Triage" like everything else.
    QCoreApplication.setOrganizationName("Image Triage")
    QCoreApplication.setApplicationName("Image Triage")
    QCoreApplication.setApplicationVersion(current_app_version())
    migrate_legacy_settings_once()
    migrate_legacy_app_data_once()
    # Large edited derivatives can legitimately exceed Qt's conservative default.
    QImageReader.setAllocationLimit(1024)

    app = QApplication(sys.argv)
    app.setApplicationDisplayName("Image Triage")
    app.setWindowIcon(QIcon(str(_APP_ICON_PATH)))

    launch_target = launch_target_from_argv(sys.argv)
    quick_view = is_quick_view_launch_target(launch_target)
    splash = None
    if _STARTUP_SPLASH_ENABLED and not quick_view:
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
    window = MainWindow(launch_target=launch_target, quick_view=quick_view)
    if splash is not None:
        splash.set_status("Ready", 100)
        app.processEvents()
        splash.finish(window)
    elif not quick_view:
        window.show()
    if not quick_view:
        # The popout viewer is built on first use rather than during startup
        # (WI-8.1); pre-build it in idle time once the window is up so the first
        # Space press doesn't pay for it. A quick-view launch needs it at once
        # and builds it synchronously when it opens the image.
        window.schedule_deferred_preview_build()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
