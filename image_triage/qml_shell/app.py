"""Bootstrap for the QML shell: engine, context objects, thumbnail service, and a headless grab mode for checking the layout."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

QML_DIR = Path(__file__).resolve().parent / "qml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m image_triage.qml_shell", description=__doc__)
    parser.add_argument("--folder", help="photo folder to open")
    parser.add_argument("--mock", metavar="DIR", help="open p01..p12.jpg from DIR with the HTML mockup's exact data (for pixel comparison)")
    parser.add_argument("--light", action="store_true", help="start in the light theme")
    parser.add_argument("--size", default="", help="window size in device-independent pixels (default: 1440x900, or 92%% of the screen if that is smaller)")
    parser.add_argument("--grab", metavar="PNG", help="render the window to PNG once thumbnails settle, then exit (for checks)")
    parser.add_argument("--grab-wait", type=float, default=4.0, help="seconds to wait before grabbing (default 4)")
    parser.add_argument("--set", default="", help="setting values applied after startup, 'id=value;id=value' (for headless checks of the settings window)")
    parser.add_argument("--hover", default="", help="park the pointer at 'x,y' (window coordinates) instead of off the grid, to check hover states headlessly")
    parser.add_argument("--click", default="", help="mouse clicks before the grab, 'x,y;x,y' in window coordinates (for headless checks)")
    parser.add_argument("--ui", default="", help="UiState settings applied at startup, e.g. 'tab=faces;insp=false;inspTab=2' (for headless checks)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

    from PySide6.QtCore import Q_ARG, QCoreApplication, QMetaObject, QTimer, QUrl
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6 import QtQuick as _QtQuick  # noqa: F401 - registers QQuickWindow so the root object is wrapped with grabWindow()

    from .. import memtrace
    from .batch_model import BatchModel
    from .bestof_model import BestOfModel
    from .collections_model import CollectionsModel
    from .people_model import PeopleModel
    from .filters_model import FiltersModel
    from .palette_model import PaletteModel
    from .photos import PhotoModel
    from .handoff_model import HandoffModel
    from .pocketdrop_model import PocketDropModel
    from .transfer_model import TransferModel
    from .settings_model import ACCENTS, SettingsModel
    from .theme import Theme
    from .thumbs import ThumbService

    QCoreApplication.setOrganizationName("Image Triage")
    QCoreApplication.setApplicationName("Image Triage")
    from PySide6.QtCore import Qt

    # fractional scales (125%, 150%, 175%) are used as they are, not rounded to whole numbers, so the layout is the same size in
    # device-independent pixels on every monitor; Qt 6 turns high-DPI scaling on by itself
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QGuiApplication(sys.argv[:1])
    from PySide6.QtGui import QFont

    base_font = QFont("Inter")
    base_font.setFamilies(["Inter", "Segoe UI", "Helvetica Neue", "Arial"])
    base_font.setPixelSize(13)
    base_font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)   # closer to the mockup's browser rendering
    if hasattr(base_font, "setFeature"):
        from PySide6.QtGui import QFont as _F
        base_font.setFeature(_F.Tag("tnum"), 1)   # font-variant-numeric: tabular-nums, as in the mockup
    app.setFont(base_font)
    memtrace.start(app)

    theme = Theme(light=args.light, parent=app)
    photos = PhotoModel(parent=app)
    settings = SettingsModel(memory=bool(args.mock), parent=app)
    palette = PaletteModel(memory=bool(args.mock), parent=app)
    adv_filters = FiltersModel(photos, memory=bool(args.mock), parent=app)
    batch = BatchModel(photos, memory=bool(args.mock), parent=app)
    handoff = HandoffModel(photos, memory=bool(args.mock), parent=app)
    transfers = TransferModel(photos, memory=bool(args.mock), parent=app)
    handoff.runRequested.connect(transfers.handoff)
    collections = CollectionsModel(photos, memory=bool(args.mock), parent=app)
    people = PeopleModel(photos, memory=bool(args.mock), parent=app)
    best_of = BestOfModel(photos, parent=app)
    pocket_drop = PocketDropModel(photos, memory=bool(args.mock), parent=app, collections=collections)
    app.aboutToQuit.connect(batch.cleanup)
    app.aboutToQuit.connect(pocket_drop.shutdown)

    def apply_theme() -> None:
        index = settings.accentIndex
        system_dark = app.styleHints().colorScheme() != Qt.ColorScheme.Light
        theme.applyMode(settings.themeMode, ACCENTS[index][1] if 0 <= index < len(ACCENTS) else -1, settings.contrast, system_dark)

    settings.previewChanged.connect(apply_theme)
    apply_theme()
    if args.light:
        theme.light = True
    engine = QQmlApplicationEngine()
    thumbs = ThumbService(parent=app)
    context = engine.rootContext()
    context.setContextProperty("theme", theme)
    context.setContextProperty("photos", photos)
    context.setContextProperty("settings", settings)
    context.setContextProperty("cmdPalette", palette)
    context.setContextProperty("advFilters", adv_filters)
    context.setContextProperty("batch", batch)
    context.setContextProperty("pocketDrop", pocket_drop)
    context.setContextProperty("collections", collections)
    context.setContextProperty("people", people)
    context.setContextProperty("bestOf", best_of)
    context.setContextProperty("handoff", handoff)
    context.setContextProperty("transfers", transfers)
    context.setContextProperty("thumbs", thumbs)
    context.setContextProperty("startup", args.ui)
    engine.addImportPath(str(QML_DIR))
    if args.size:
        width, height = (int(part) for part in args.size.lower().split("x"))
    else:
        # sizes are in device-independent pixels, so the same on a 1080p, 1440p or 4K screen at any scale; only a screen smaller than
        # the design size (e.g. 1280x720 at 100%, or 4K at 300%) shrinks the window
        area = app.primaryScreen().availableGeometry()
        width, height = min(1440, int(area.width() * 0.92)), min(900, int(area.height() * 0.92))
    engine.load(QUrl.fromLocalFile(str(QML_DIR / "Main.qml")))
    roots = engine.rootObjects()
    if not roots:
        return 1
    window = roots[0]
    pocket_drop.setWindowHandle(int(window.winId()))
    window.setWidth(width)
    window.setHeight(height)
    if not args.size:
        window.setPosition(area.x() + (area.width() - width) // 2, area.y() + (area.height() - height) // 2)
    QMetaObject.invokeMethod(window, "applyStartup", Q_ARG("QVariant", width))
    for pair in filter(None, args.set.split(";")):
        key, _, raw = pair.partition("=")
        settings.set(key, True if raw == "true" else False if raw == "false" else (float(raw) if "." in raw else int(raw)) if raw.lstrip("-").replace(".", "", 1).isdigit() else raw)
    if args.mock:
        photos.loadMock(args.mock)
    elif args.folder:
        photos.loadFolder(args.folder)

    if args.grab:
        def grab() -> None:
            image = window.grabWindow()
            image.save(args.grab)
            print(f"grabbed {image.width()}x{image.height()} -> {args.grab}; {photos.shown} photos")
            app.quit()

        from PySide6.QtCore import QPoint
        from PySide6.QtTest import QTest
        hx, hy = (int(v) for v in args.hover.split(",")) if args.hover else (3, 3)
        QTimer.singleShot(300, lambda: QTest.mouseMove(window, QPoint(hx, hy)))   # park the pointer off the grid so no cell shows hover
        QTimer.singleShot(int(args.grab_wait * 1000), grab)
        if args.click:
            from PySide6.QtCore import Qt

            points = [tuple(int(v) for v in part.split(",")) for part in args.click.split(";") if part.strip()]

            def press(index: int = 0) -> None:
                if index >= len(points):
                    return
                x, y = points[index]
                QTest.mouseMove(window, QPoint(x, y))
                QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, y))
                QTimer.singleShot(700, lambda: press(index + 1))

            QTimer.singleShot(int(args.grab_wait * 1000) - 2500, press)
    code = app.exec()
    memtrace.stop()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
