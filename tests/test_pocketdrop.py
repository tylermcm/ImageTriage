from __future__ import annotations

import os
import time
import urllib.request

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from image_triage.pocketdrop import _bridge

needs_native = pytest.mark.skipif(
    not _bridge.library_path().is_file(), reason="pocketdrop.dll not built (native/pocketdrop/build_windows.bat)"
)


def _app() -> QApplication:
    QCoreApplication.setOrganizationName("ImageTriageTests")
    QCoreApplication.setApplicationName("PocketDrop")
    return QApplication.instance() or QApplication([])


def _pump(app: QApplication, seconds: float, until=lambda: False) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and not until():
        app.processEvents()
        time.sleep(0.02)


@needs_native
def test_native_library_matches_the_bindings() -> None:
    lib = _bridge.load_library()
    assert lib.pd_abi_version() == _bridge.ABI_VERSION


@needs_native
def test_panel_serves_the_phone_page_and_takes_files(tmp_path) -> None:
    from image_triage.pocketdrop import PocketDropPanel

    app = _app()
    QSettings().clear()
    panel = PocketDropPanel()
    assert panel.available, panel.error
    view = panel.view
    panel.resize(385, 750)
    panel.show()
    try:
        _pump(app, 5, until=lambda: view.started)
        assert view.started

        # The QR link, as a phone would open it.
        QGuiApplication.clipboard().clear()
        _pump(app, 5, until=lambda: (view._lib.pd_copy_link(view._host), QGuiApplication.clipboard().text())[1])
        link = QGuiApplication.clipboard().text()
        assert link.startswith("http://")
        with urllib.request.urlopen(link, timeout=5) as response:
            assert response.status == 200
            page = response.read().decode("utf-8", "replace")
        assert "PocketDrop" in page

        shared = tmp_path / "photo.txt"
        shared.write_text("hello from image triage", encoding="utf-8")
        panel.add_paths([str(shared)])
        _pump(app, 1)
        image = view.grab().toImage()
        assert not image.isNull() and image.width() > 0
    finally:
        panel.shutdown()
        QSettings().clear()
