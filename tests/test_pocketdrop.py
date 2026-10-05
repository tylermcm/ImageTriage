from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PIL import Image
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from image_triage.app_identity import user_settings
from image_triage.edit_storage import editor_session_path
from image_triage.models import ImageRecord
from image_triage.photo_terminal.session import SCHEMA_NAME, SCHEMA_VERSION
from image_triage.pocketdrop import _bridge
from image_triage.handoff_controller import HandoffController
from tests.harness import controller_over

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
    user_settings().clear()
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
        user_settings().clear()


IMAGE_SIZE = (80, 60)


def _write_source_image(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", IMAGE_SIZE, color=(120, 130, 140)).save(path)


def _write_session(image_path: Path, *, operations: list[dict] | None = None) -> Path:
    session_path = editor_session_path(image_path)
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(
        json.dumps(
            {
                "version": SCHEMA_VERSION,
                "schema": SCHEMA_NAME,
                "coordinateSpaces": [
                    {
                        "id": "space-source-full",
                        "sourceWidth": IMAGE_SIZE[0],
                        "sourceHeight": IMAGE_SIZE[1],
                        "cropInEffect": None,
                    }
                ],
                "assets": {"dir": "assets", "bitmapMasks": []},
                "operations": operations or [],
                "masks": [],
            }
        )
    )
    return session_path


def _record(path: Path) -> ImageRecord:
    return ImageRecord(path=str(path), name=path.name, size=path.stat().st_size, modified_ns=path.stat().st_mtime_ns)


def _fake_self(apply_edits: bool) -> SimpleNamespace:
    # pocketdrop_send_path_for only reads window._apply_edits_to_pocketdrop, so
    # a bare namespace stands in for the real MainWindow instance.
    window = SimpleNamespace(_apply_edits_to_pocketdrop=apply_edits)
    controller_over(HandoffController, window, "_handoff")
    return window


def test_setting_off_sends_original_path_unchanged(tmp_path) -> None:
    image_path = tmp_path / "IMG_0001.jpg"
    _write_source_image(image_path)
    _write_session(image_path, operations=[{"id": "op-1", "type": "adjust.exposure", "enabled": True, "params": {"exposure": 1.8}}])

    result = _fake_self(False)._handoff.pocketdrop_send_path_for(_record(image_path))

    assert result == str(image_path)


def test_setting_on_with_real_sidecar_sends_a_temp_rendered_path(tmp_path) -> None:
    image_path = tmp_path / "IMG_0002.jpg"
    _write_source_image(image_path)
    _write_session(image_path, operations=[{"id": "op-1", "type": "adjust.exposure", "enabled": True, "params": {"exposure": 1.8}}])

    result = _fake_self(True)._handoff.pocketdrop_send_path_for(_record(image_path))

    assert result != str(image_path)
    assert os.path.isfile(result)


def test_setting_on_without_sidecar_sends_original_path(tmp_path) -> None:
    image_path = tmp_path / "IMG_0003.jpg"
    _write_source_image(image_path)

    result = _fake_self(True)._handoff.pocketdrop_send_path_for(_record(image_path))

    assert result == str(image_path)
