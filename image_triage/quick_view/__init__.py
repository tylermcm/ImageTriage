"""The quick viewer: a photo opened from File Explorer (or Finder, or a file manager) shows at once, without the library.

Starting the whole app for one photo took 2-3 seconds: every model, the full QML shell and a read of the folder. This window loads one
small QML file and decodes the photo at screen size on a worker thread while the window is being made, so the photo is up in a fraction
of that. The arrow keys go through the folder's other photos (read after the first is shown, the neighbours decoded ahead); Library
opens the full app on the photo and this window steps aside once it is up; Edit does the same with the photo opened in the library's
viewer, Edit open (the editor is loaded only then, never with the quick viewer).

``main.py`` routes a launch here when it names one photo, the viewer is on (Settings > Browsing, or ``IMAGE_TRIAGE_QUICK_VIEW=0``
to switch it off) and the library is not already running: a running app gets the photo as before, being already up. Nothing of the
library is imported on the way: this module and ``decode`` use Qt and the standard library, and ``decode`` imports the app's heavier
readers only for a photo that needs them.

Set ``IMAGE_TRIAGE_QUICK_VIEW_TIMING=1`` to print how long each step took (from ``IMAGE_TRIAGE_T0``, a launcher's clock, when set).
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ENV_SWITCH = "IMAGE_TRIAGE_QUICK_VIEW"
TIMING_ENV = "IMAGE_TRIAGE_QUICK_VIEW_TIMING"
SETTING_KEY = "browse/quick_viewer"
QML_FILE = Path(__file__).resolve().with_name("QuickView.qml")
FULL_APP_WAIT_S = 45.0             # how long the window waits for the library to come up before it gives up on it
_T0 = float(os.environ.get("IMAGE_TRIAGE_T0") or 0) or time.time()


def mark(step: str) -> None:
    if os.environ.get(TIMING_ENV):
        print(f"quick-view {step}: {(time.time() - _T0) * 1000:.0f} ms", file=sys.stderr, flush=True)


def enabled() -> bool:
    """Whether a photo opened from the file manager goes to the quick viewer: the setting, unless the environment says otherwise."""
    switch = os.environ.get(ENV_SWITCH, "").strip().lower()
    if switch in ("0", "false", "no", "off"):
        return False
    if switch in ("1", "true", "yes", "on"):
        return True
    try:
        from ..app_identity import user_settings

        value = user_settings().value(SETTING_KEY, True)
    except Exception:
        return True
    return str(value).strip().lower() not in ("0", "false", "no", "off")


EDITABLE_SUFFIXES = frozenset({".jpg", ".jpeg", ".jpe", ".png", ".tif", ".tiff", ".webp"})   # and RAW: what the viewer's Edit takes


def can_edit(photo: str) -> bool:
    """Whether the library's Edit takes ``photo``: a RAW, or a JPEG, TIFF, PNG or WebP (Viewer.qml's Edit RAW / Edit button)."""
    from ..formats import RAW_SUFFIXES, suffix_for_path

    suffix = suffix_for_path(photo)
    return suffix in RAW_SUFFIXES or suffix in EDITABLE_SUFFIXES


def full_app_command(photo: str, edit: bool = False) -> list[str]:
    """How to start the library on ``photo``: the same executable, told the folder and the photo (``main.py`` then starts the library);
    with ``edit``, the photo opens in the library's viewer with Edit open."""
    folder = os.path.dirname(photo)
    head = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "image_triage"]
    return [*head, "--folder", folder, "--select", photo, *(["--edit"] if edit else []), "--from-viewer"]


def application():
    """The Qt application, made the way the library makes it (``qml_shell.app`` takes this one when it already exists): a Mac
    launch makes it before it knows which of the two it starts, to hear which file Finder opened."""
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtGui import QGuiApplication

    existing = QGuiApplication.instance()
    if existing is not None:
        return existing
    QCoreApplication.setOrganizationName("Image Triage")
    QCoreApplication.setApplicationName("Image Triage")
    QGuiApplication.setDesktopFileName("image-triage")
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    return QGuiApplication(sys.argv[:1])


def run(photo: str) -> int | None:
    """Show ``photo`` in the quick viewer and return the exit code; None when the library should start instead (the viewer is off)."""
    if not enabled():
        return None
    mark("start")
    from PySide6.QtGui import QIcon

    app = application()
    mark("app")
    # the library already running takes the photo (it is up, so that is quick too), as a launch always did
    from ..qml_shell.single_instance import send_to_running

    if send_to_running({"folder": os.path.dirname(photo), "select": photo}, timeout_ms=1500):
        mark("handed to the running library")
        return 0
    from .viewer import QuickViewer

    viewer = QuickViewer(photo, app)
    viewer.start()                                        # the decode runs while the window is being made
    mark("decode started")
    from PySide6.QtQml import QQmlApplicationEngine

    icon = Path(__file__).resolve().parents[1] / "assets" / "app_icon-v5.ico"
    if icon.is_file():
        app.setWindowIcon(QIcon(str(icon)))
    engine = QQmlApplicationEngine()
    engine.addImageProvider("quick", viewer.provider)
    engine.rootContext().setContextProperty("viewer", viewer)
    engine.load(QML_FILE.as_uri())
    if not engine.rootObjects():
        return None                                       # the window could not be made: the library shows the photo instead
    window = engine.rootObjects()[0]
    place_window(window)
    viewer.attach(window)
    # macOS: a photo opened in Finder while this window is up comes to it as an event, not as another launch
    from ..file_open_events import install

    install(app).opened.connect(viewer.openPhoto)
    mark("window made")
    code = app.exec()
    viewer.shutdown()
    return code


def window_rect(area: tuple[int, int, int, int], width_share: float = 0.78, height_share: float = 0.82) -> tuple[int, int, int, int]:
    """The window's rectangle (x, y, width, height) on one monitor's usable ``area``: centred, this share of it."""
    x, y, width, height = area
    w, h = round(width * width_share), round(height * height_share)
    return x + (width - w) // 2, y + (height - h) // 2, w, h


def place_window(window) -> None:
    """Size the window to the monitor the pointer is on (the one the person double-clicked on), centre it there and show it."""
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QCursor, QGuiApplication

    screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
    if screen is not None:
        window.setScreen(screen)
        window.setGeometry(QRect(*window_rect(screen.availableGeometry().getRect())))
    window.show()
