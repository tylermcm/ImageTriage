"""Opt-in Windows smoke check of the real hosted editor using synthetic photos.

Run: python scripts/check_photocraft_handoff.py --executable PATH --output DIR
The output folder receives the fixtures, layered edits, screenshots and timings.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wintypes
import hashlib
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject, QEvent, QPointF
from PySide6.QtGui import QColor, QEnterEvent, QImage, QPixmap
from PySide6.QtWidgets import QApplication

from image_triage import photocraft_bridge as bridge
from image_triage.edit_render_headless import render_edited_image
from image_triage.models import ImageRecord
from image_triage.preview import FullScreenPreview, PreviewEntry
from image_triage.preview_controller import PreviewController
from image_triage.scanner import normalized_path_key


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", help="Override normal executable discovery")
    parser.add_argument("--output", required=True)
    parser.add_argument("--megapixels", type=int, default=1)
    parser.add_argument("--skip-menu-save", action="store_true", help="Benchmark an earlier hosted binary's switch path")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    parent = QObject()
    controller = PreviewController(parent)
    preview = FullScreenPreview()
    statuses, grid_updates = [], []
    controller._window = SimpleNamespace(
        _preview=preview,
        statusBar=lambda: SimpleNamespace(showMessage=lambda *message: statuses.append(message)),
        grid=SimpleNamespace(update_items=grid_updates.append),
    )
    if args.executable:
        controller._photocraft_executable = str(Path(args.executable).resolve())
    print(f"Hosted executable: {controller._photocraft_executable}", flush=True)
    preview.photocraft_edit_requested.connect(controller.handle_photocraft_edit_requested)
    preview.photocraft_host_resized.connect(controller.resize_photocraft)
    preview.photocraft_close_guard = controller.prepare_photocraft_preview_close
    preview.set_photocraft_available(True)
    records, thumbnails, originals = [], [], {}
    width, height = (6000, 4000) if args.megapixels >= 24 else (1200, 800)
    for name, color in (("first.png", "red"), ("second.png", "blue")):
        path = output / name
        image = QImage(width, height, QImage.Format.Format_RGB32)
        image.fill(QColor(color))
        assert image.save(str(path))
        thumbnails.append(QPixmap.fromImage(image.scaled(120, 80)))
        stat = path.stat()
        records.append(ImageRecord(str(path), name, stat.st_size, stat.st_mtime_ns))
        originals[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()

    def wait_for(predicate, timeout=120):
        deadline = time.monotonic() + timeout
        while not predicate():
            app.processEvents()
            if time.monotonic() > deadline:
                raise AssertionError(f"handoff timed out: {statuses}")
            time.sleep(0.01)
        app.processEvents()

    timings = {}
    class HostEvents(QObject):
        hides = 0
        def eventFilter(self, watched, event):
            if event.type() == QEvent.Type.Hide:
                self.hides += 1
            return False
    host_events = HostEvents()
    initial_visibility = []
    find_window = bridge._find_window_for_pid
    def observed_window(pid, **kwargs):
        hwnd = find_window(pid, **kwargs)
        if hwnd:
            visible = ctypes.windll.user32.IsWindowVisible
            visible.argtypes = [wintypes.HWND]
            get_rect = ctypes.windll.user32.GetWindowRect
            get_rect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
            rect = wintypes.RECT()
            get_rect(hwnd, ctypes.byref(rect))
            metrics = ctypes.windll.user32.GetSystemMetrics
            left, top, width, height = [metrics(index) for index in (76, 77, 78, 79)]
            on_desktop = rect.right > left and rect.bottom > top and rect.left < left + width and rect.top < top + height
            initial_visibility.append(bool(visible(hwnd)) and on_desktop)
        return hwnd
    bridge._find_window_for_pid = observed_window
    def select(index):
        start = time.perf_counter()
        preview.show_entries([PreviewEntry(records[index], records[index].path)])
        assert preview.isFullScreen()
        if not preview.photocraft_edit_active():
            assert preview._filmstrip.isHidden(), "filmstrip flashed during editor startup"
        preview.set_browse_context(2, index, lambda item: thumbnails[item])
        queued_ms = (time.perf_counter() - start) * 1000
        opened = controller._photocraft_future
        wait_for(lambda: opened.done() and preview.photocraft_edit_active() and controller._photocraft is not None
                 and controller._photocraft.current_source == normalized_path_key(records[index].path))
        assert preview._filmstrip.isVisible()
        assert preview.geometry() == preview.screen().geometry(), "popout did not cover the entire screen"
        return {"selection_ms": queued_ms, "ready_ms": (time.perf_counter() - start) * 1000}

    proc = None
    try:
        timings["cold_open"] = select(0)
        assert initial_visibility[-1] is False, "PhotoCraft flashed a standalone window"
        preview._photocraft_host.installEventFilter(host_events)
        proc = controller._photocraft
        timings["launch_stages"] = proc.launch_timings
        pid = proc.process.pid
        get_parent = ctypes.windll.user32.GetParent
        get_parent.argtypes = [wintypes.HWND]
        get_parent.restype = wintypes.HWND
        assert get_parent(proc.hwnd) == preview.photocraft_host_hwnd(), "editor was not embedded"
        controller._photocraft_executor.submit(proc.control.execute, "image.adjustments.invert").result(timeout=120)
        timings["switch"] = select(1)
        assert controller._photocraft.process.pid == pid
        timings["return"] = select(0)
        assert controller._photocraft.process.pid == pid
        assert host_events.hides == 0, "filmstrip navigation hid the editor host"
        navigation_hides = host_events.hides
        wait_for(lambda: bridge.rendered_preview_path(records[0].path).is_file())
        controller._photocraft_executor.submit(controller._save_active_photocraft).result(timeout=120)
        app.processEvents()
        edited = render_edited_image(records[0].path)
        assert edited is not None and edited.pixelColor(0, 0).green() > 240
        assert grid_updates, "the main app never received a saved-render update"
        documents = controller._photocraft_executor.submit(proc.control.execute, "session.inspect").result(timeout=10)
        assert len(documents["documents"]) == 1
        # Exercise PhotoCraft's own Save command and its host binding.
        if not args.skip_menu_save:
            controller._photocraft_executor.submit(proc.control.execute, "image.adjustments.invert").result(timeout=120)
            controller._photocraft_executor.submit(proc.control.call, "ui.menu.invoke", {"id": "file.save"}).result(timeout=120)
            wait_for(lambda: controller._photocraft_future is None or controller._photocraft_future.done())
            controller._photocraft_executor.submit(controller._save_active_photocraft).result(timeout=120)
        # Closing PhotoCraft's document tab must not strand the filmstrip.
        for index in (0, 1):
            controller._photocraft_executor.submit(proc.control.call, "ui.menu.invoke", {"id": "file.close"}).result(timeout=120)
            session = controller._photocraft_executor.submit(proc.control.execute, "session.inspect").result(timeout=10)
            assert not session["documents"], "PhotoCraft's document did not close"
            if index == 0:
                preview._handle_studio_filmstrip_selected(0)
                reopened = controller._photocraft_future
                wait_for(lambda: reopened.done() and controller._photocraft.current_source == normalized_path_key(records[0].path))
            else:
                timings["reopen_other_after_close"] = select(index)
            session = controller._photocraft_executor.submit(proc.control.execute, "session.inspect").result(timeout=10)
            assert len(session["documents"]) == 1
            assert controller._photocraft.process.pid == pid, "reopening restarted PhotoCraft"
        assert host_events.hides == 0
        navigation_hides = host_events.hides
        for theme in ("pro", "proMedium", "studio", "studioLight", "classic"):
            controller._photocraft_executor.submit(proc.control.ui_set, theme=theme).result(timeout=10)
            colors = controller._photocraft_executor.submit(proc.control.call, "ui.theme").result(timeout=10)
            controller._photocraft_executor.submit(controller._sync_photocraft_theme, proc).result(timeout=10)
            app.processEvents()
            assert preview._filmstrip._editor_palette["panel"] == colors["panel"]
            assert preview._filmstrip._editor_palette["accent"] == colors["accent"]
            assert preview._mockup_status_bar.isHidden()
            strip_image = preview._filmstrip.grab().toImage()
            assert strip_image.pixelColor(2, strip_image.height() - 1).name() == colors["panel"]
            handle = preview._filmstrip._handle
            handle.leaveEvent(QEvent(QEvent.Type.Leave))
            handle_image = handle.grab().toImage()
            assert handle_image.pixelColor(2, (handle_image.height() - 2) // 2).name() == colors["dock"]
            handle.enterEvent(QEnterEvent(QPointF(2, 2), QPointF(2, 2), QPointF(2, 2)))
            handle_image = handle.grab().toImage()
            assert handle_image.pixelColor(2, (handle_image.height() - 2) // 2).name() == colors["accent"]
            handle.leaveEvent(QEvent(QEvent.Type.Leave))
            controller._photocraft_executor.submit(proc.control.call, "ui.screenshot", {"path": f"theme-{theme}.png", "focus": False}).result(timeout=120)
            # Let Qt paint after delivering the theme signal. Capturing the
            # desktop rectangle includes the foreign native editor child;
            # capturing only Qt's window can return its old backing surface.
            painted = time.monotonic() + 0.15
            while time.monotonic() < painted:
                app.processEvents()
                time.sleep(0.01)
            preview.screen().grabWindow(0, preview.x(), preview.y(), preview.width(), preview.height()).save(str(output / f"popout-{theme}.png"))
        timings["filmstrip_themes_checked"] = 5
        controller._photocraft_executor.submit(proc.control.call, "ui.screenshot", {"path": "hosted-editor.png", "focus": False}).result(timeout=120)
        preview.grab().save(str(output / "popout.png"))
        for path, digest in originals.items():
            assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == digest
        preview.close()
        controller.shutdown_photocraft()
        assert proc.process.poll() is not None and not proc.token_file.exists()
        timings["grid_save_notifications"] = len(grid_updates)
        timings["originals_unchanged"] = True
        timings["process_cleaned_up"] = True
        timings["native_parent_verified"] = True
        timings["menu_save_checked"] = not args.skip_menu_save
        timings["reopen_after_tab_close_checked"] = True
        timings["hidden_start_verified"] = True
        timings["editor_hides_during_navigation"] = navigation_hides
        (output / "timings.json").write_text(json.dumps(timings, indent=2), encoding="utf-8")
        print(json.dumps(timings, indent=2))
    finally:
        bridge._find_window_for_pid = find_window
        controller.shutdown_photocraft()
        controller._photocraft_executor.shutdown(wait=True)
        preview.photocraft_close_guard = None
        preview.close()


if __name__ == "__main__":
    main()
