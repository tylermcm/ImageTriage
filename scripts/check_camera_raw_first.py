r"""Opt-in live check of the Camera Raw-first workflow with the real hosted editor.

    python scripts/check_camera_raw_first.py --executable C:\path\photocraft.exe --output C:\temp\crf

Runs the real popout and controller against a real PhotoCraft launched with ``--raw-workspace`` and checks
the whole loop on two synthetic photos:

1. a photo opens in Camera Raw, inside the embedded window;
2. adjusting it and moving to the next photo keeps the adjustment (saved render brighter than the original),
   and the next photo is in Camera Raw too;
3. coming back to the first photo restores the adjustment and Camera Raw opens again;
4. OK leaves the full editor on the same, still-open document;
5. closing that document returns to the photo in Camera Raw.

It uses the user's desktop (the popout is full screen) and the output folder for fixtures and screenshots.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from image_triage import photocraft_bridge as bridge  # noqa: E402
from image_triage.app_identity import user_settings  # noqa: E402
from image_triage.models import ImageRecord  # noqa: E402
from image_triage.nav_timing import format_summary, summarize  # noqa: E402
from image_triage.preview import FullScreenPreview, PreviewEntry  # noqa: E402
from image_triage.preview_controller import PreviewController  # noqa: E402
from image_triage.scanner import normalized_path_key  # noqa: E402
from image_triage.ui.native_image_layer import CAMERA_RAW_FIRST_KEY  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    settings = user_settings()
    previous_setting = settings.value(CAMERA_RAW_FIRST_KEY, False, bool)
    settings.setValue(CAMERA_RAW_FIRST_KEY, True)
    parent = QObject()
    controller = PreviewController(parent)
    preview = FullScreenPreview()
    statuses = []
    controller._window = SimpleNamespace(
        _preview=preview,
        statusBar=lambda: SimpleNamespace(showMessage=lambda *message: statuses.append(message)),
        grid=SimpleNamespace(update_items=lambda *_: None),
    )
    controller._photocraft_executable = str(Path(args.executable).resolve())
    preview.photocraft_edit_requested.connect(controller.handle_photocraft_edit_requested)
    preview.photocraft_host_resized.connect(controller.resize_photocraft)
    preview.photocraft_prioritize_requested.connect(controller.prioritize_photocraft_open)
    preview.native_layer_painted.connect(controller._handle_native_layer_painted)
    preview.photocraft_close_guard = controller.prepare_photocraft_preview_close
    preview.set_photocraft_available(True)

    records, thumbnails = [], []
    for name, color in (("first.png", "#404040"), ("second.png", "blue")):
        path = output / name
        image = QImage(1200, 800, QImage.Format.Format_RGB32)
        image.fill(QColor(color))
        assert image.save(str(path))
        thumbnails.append(QPixmap.fromImage(image.scaled(120, 80)))
        stat = path.stat()
        records.append(ImageRecord(str(path), name, stat.st_size, stat.st_mtime_ns))

    def wait_for(predicate, what, timeout=90.0):
        deadline = time.monotonic() + timeout
        while True:
            app.processEvents()
            value = predicate()
            if value:
                return value
            if time.monotonic() > deadline:
                raise AssertionError(f"timed out waiting for {what}: {statuses}")
            time.sleep(0.02)

    def proc():
        return controller._photocraft

    def fast():
        return proc().fast_lane()

    def documents():
        return fast().execute("session.inspect").get("documents", [])

    def camera_raw():
        return fast().call("ui.cameraRaw")

    def select(index):
        started = time.perf_counter()
        controller._begin_navigation_timing(records[index].path)
        preview.show_entries([PreviewEntry(records[index], records[index].path)])
        preview.set_browse_context(2, index, lambda item: thumbnails[item])
        wait_for(lambda: proc() is not None and proc().current_source == normalized_path_key(records[index].path), f"photo {index} to open")
        wait_for(lambda: camera_raw().get("open"), f"Camera Raw on photo {index}")
        return round((time.perf_counter() - started) * 1000)

    def pixel(png: Path) -> int:
        image = QImage(str(png))
        return image.pixelColor(image.width() // 2, image.height() // 2).red()

    results = {}
    try:
        results["first_photo_in_camera_raw_ms"] = select(0)
        assert proc().camera_raw_first, "the editor was not launched in the Camera Raw workspace"
        assert len(documents()) == 1
        view = wait_for(lambda: fast().call("ui.viewport").get("cameraRaw", {}).get("view"), "Camera Raw's view")
        results["camera_raw_view"] = view
        fast().call("ui.screenshot", {"path": "1-camera-raw.png", "focus": False})

        # Adjust, then move on: the adjustment must be kept.
        fast().call("ui.cameraRaw", {"set": {"exposure": 2.0}})
        assert camera_raw()["changed"] is True
        results["second_photo_in_camera_raw_ms"] = select(1)
        assert len(documents()) == 1, "the first photo's document was not replaced"
        first_render = bridge.rendered_preview_path(records[0].path)
        wait_for(lambda: first_render.is_file() and first_render.stat().st_size > 0, "the first photo's saved render")
        wait_for(lambda: not proc().pending_stashes, "the first photo's save to finish")
        kept = pixel(first_render)
        assert kept > 0x40 + 20, f"the adjustment was lost when moving on (red={kept}, original=64)"
        results["first_photo_saved_red"] = kept

        # Come back: restored with the adjustment, and Camera Raw is open again.
        results["return_to_first_ms"] = select(0)
        assert camera_raw()["changed"] is False, "returning should open a fresh dialog on the stored photo"
        assert len(documents()) == 1

        # OK leaves the full editor on the same document.
        committed = fast().call("ui.cameraRaw", {"commit": True})
        assert committed["open"] is False
        wait_for(lambda: len(documents()) == 1, "the document to stay open")
        time.sleep(1.0)
        assert camera_raw()["open"] is False, "Camera Raw reopened after being closed"
        fast().call("ui.screenshot", {"path": "2-full-editor.png", "focus": False})

        # Closing the document goes back to the photo in Camera Raw.
        fast().call("ui.menu.invoke", {"id": "file.close"})
        assert not documents()
        wait_for(lambda: camera_raw().get("open") and len(documents()) == 1, "Camera Raw to return after closing", timeout=30)
        results["closing_returns_to_camera_raw"] = True
        fast().call("ui.screenshot", {"path": "3-back-in-camera-raw.png", "focus": False})

        controller.nav_timer.flush()
        results["navigation"] = summarize(record.as_dict() for record in controller.nav_timer.finished)
        results["ok"] = True
        (output / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(json.dumps(results, indent=2))
        print(format_summary(results["navigation"]))
    finally:
        settings.setValue(CAMERA_RAW_FIRST_KEY, previous_setting)
        try:
            preview.photocraft_close_guard = None
            controller.shutdown_photocraft()
        finally:
            controller._photocraft_executor.shutdown(wait=True)
            preview.close()


if __name__ == "__main__":
    main()
