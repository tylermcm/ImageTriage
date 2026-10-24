"""Bootstrap for the QML shell: engine, context objects, thumbnail service, and a headless grab mode for checking the layout."""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

QML_DIR = Path(__file__).resolve().parent / "qml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m image_triage.qml_shell", description=__doc__)
    parser.add_argument("--folder", help="photo folder to open")
    parser.add_argument("--light", action="store_true", help="start in the light theme")
    parser.add_argument("--size", default="1440x900", help="window size (default 1440x900)")
    parser.add_argument("--grab", metavar="PNG", help="render the window to PNG once thumbnails settle, then exit (for checks)")
    parser.add_argument("--grab-wait", type=float, default=4.0, help="seconds to wait before grabbing (default 4)")
    parser.add_argument("--ui", default="", help="UiState settings applied at startup, e.g. 'tab=faces;insp=false;inspTab=2' (for headless checks)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

    from PySide6.QtCore import QCoreApplication, QTimer, QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuick import QQuickWindow

    from .. import memtrace
    from .photos import PhotoModel
    from .theme import Theme
    from .thumbs import ThumbService

    QCoreApplication.setOrganizationName("Image Triage")
    QCoreApplication.setApplicationName("Image Triage")
    app = QGuiApplication(sys.argv[:1])
    memtrace.start(app)

    theme = Theme(light=args.light, parent=app)
    photos = PhotoModel(parent=app)
    engine = QQmlApplicationEngine()
    thumbs = ThumbService(parent=app)
    context = engine.rootContext()
    context.setContextProperty("theme", theme)
    context.setContextProperty("photos", photos)
    context.setContextProperty("thumbs", thumbs)
    context.setContextProperty("startup", args.ui)
    engine.addImportPath(str(QML_DIR))
    width, height = (int(part) for part in args.size.lower().split("x"))
    engine.load(QUrl.fromLocalFile(str(QML_DIR / "Main.qml")))
    roots = engine.rootObjects()
    if not roots:
        return 1
    window = roots[0]
    window.setWidth(width)
    window.setHeight(height)
    if args.folder:
        photos.loadFolder(args.folder)

    if args.grab:
        def grab() -> None:
            image = window.grabWindow()
            image.save(args.grab)
            print(f"grabbed {image.width()}x{image.height()} -> {args.grab}; {photos.shown} photos")
            app.quit()

        QTimer.singleShot(int(args.grab_wait * 1000), grab)
    code = app.exec()
    memtrace.stop()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
