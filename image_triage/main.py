from __future__ import annotations

import sys
from collections.abc import Sequence

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QImageReader
from PySide6.QtWidgets import QApplication

from image_triage.window import MainWindow


def launch_target_from_argv(argv: Sequence[str]) -> str:
    for raw_argument in argv[1:]:
        candidate = str(raw_argument).strip().strip('"')
        if candidate:
            return candidate
    return ""


def main() -> int:
    QCoreApplication.setOrganizationName("Codex")
    QCoreApplication.setApplicationName("Image Triage")
    # Large edited derivatives can legitimately exceed Qt's conservative default.
    QImageReader.setAllocationLimit(1024)

    app = QApplication(sys.argv)
    app.setApplicationDisplayName("Image Triage")
    window = MainWindow(launch_target=launch_target_from_argv(sys.argv))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
