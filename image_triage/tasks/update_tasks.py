"""Background tasks for checking for and downloading application updates."""
from __future__ import annotations

from PySide6.QtCore import QObject, QRunnable, Signal

from ..updater import UpdateInfo, check_for_update, download_update_installer


class AppUpdateCheckSignals(QObject):
    """Signals for the application update check worker."""
    finished = Signal(object)
    failed = Signal(str)


class AppUpdateCheckTask(QRunnable):
    """Checks the configured release feed without blocking the UI."""
    def __init__(self, *, current_version: str) -> None:
        super().__init__()
        self.current_version = current_version
        self.signals = AppUpdateCheckSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            self.signals.finished.emit(check_for_update(current_version=self.current_version))
        except Exception as exc:
            self.signals.failed.emit(str(exc))


class AppUpdateDownloadSignals(QObject):
    """Signals for downloading a newer MSI installer."""
    started = Signal(str)
    progress = Signal(object, object, str)
    finished = Signal(str)
    failed = Signal(str)


class AppUpdateDownloadTask(QRunnable):
    """Downloads an update installer without blocking the UI."""
    def __init__(self, *, update: UpdateInfo) -> None:
        super().__init__()
        self.update = update
        self.signals = AppUpdateDownloadSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            self.signals.started.emit(self.update.installer_filename)

            def emit_progress(current: int, total: int, filename: str) -> None:
                self.signals.progress.emit(current, total, filename)

            installer_path = download_update_installer(self.update, progress_callback=emit_progress)
            self.signals.finished.emit(str(installer_path))
        except Exception as exc:
            self.signals.failed.emit(str(exc))
