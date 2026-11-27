"""The quick viewer: a photo opened from File Explorer (or Finder, or a file manager) shows at once, without the library.

Starting the whole app for one photo took 2-3 seconds: every model, the full QML shell and a read of the folder. This window loads one
small QML file and decodes the photo at screen size on a worker thread while the window is being made, so the photo is up in a fraction
of that. The arrow keys go through the folder's other photos (read after the first is shown, the neighbours decoded ahead); Library
opens the full app on the photo and this window steps aside once it is up.

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


def full_app_command(photo: str) -> list[str]:
    """How to start the library on ``photo``: the same executable, told the folder and the photo (``main.py`` then starts the library)."""
    folder = os.path.dirname(photo)
    if getattr(sys, "frozen", False):
        return [sys.executable, "--folder", folder, "--select", photo]
    return [sys.executable, "-m", "image_triage", "--folder", folder, "--select", photo]


def run(photo: str) -> int | None:
    """Show ``photo`` in the quick viewer and return the exit code; None when the library should start instead (the viewer is off)."""
    if not enabled():
        return None
    mark("start")
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtGui import QGuiApplication, QIcon

    QCoreApplication.setOrganizationName("Image Triage")
    QCoreApplication.setApplicationName("Image Triage")
    QGuiApplication.setDesktopFileName("image-triage")
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QGuiApplication(sys.argv[:1])
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
    viewer.attach(window)
    mark("window made")
    code = app.exec()
    viewer.shutdown()
    return code
