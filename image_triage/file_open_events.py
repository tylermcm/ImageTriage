"""Files the system opens with the app by event rather than on the command line: macOS's Finder ("Open", a double-click, a file dropped on
the Dock icon) launches the app with no arguments and then sends each file as a ``QFileOpenEvent``; a file opened while the app runs
reaches the running app the same way, never a second process.

``FileOpenFilter`` turns those events into a signal; ``wait_for_opened_file`` is what ``main.py`` asks at a Mac launch with no arguments:
was the app started to open a photo (the quick viewer then shows it), or just started (the library opens)?
"""
from __future__ import annotations

import time

from PySide6.QtCore import QEvent, QObject, Signal

LAUNCH_WAIT_S = 0.35        # the open event follows the launch within a few event-loop turns; a plain launch waits this long, once


class FileOpenFilter(QObject):
    """Installed on the application: each file the system opens with it comes out of ``opened`` (the event is consumed)."""

    opened = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.received: list[str] = []

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 (Qt's name)
        if event.type() == QEvent.Type.FileOpen:
            path = event.file() or (event.url().toLocalFile() if event.url().isLocalFile() else "")
            if path:
                self.received.append(path)
                self.opened.emit(path)
                return True
        return False


def install(app) -> FileOpenFilter:
    """A filter on ``app``, parented to it (it lives as long as the app)."""
    event_filter = FileOpenFilter(app)
    app.installEventFilter(event_filter)
    return event_filter


def wait_for_opened_file(app, event_filter: FileOpenFilter, timeout_s: float = LAUNCH_WAIT_S) -> str:
    """The first file opened with the app, if one arrives within ``timeout_s`` of now; "" for a plain launch."""
    end = time.monotonic() + timeout_s
    while not event_filter.received and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)
    return event_filter.received[0] if event_filter.received else ""
