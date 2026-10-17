"""Exercise the native filmstrip through a real Image Triage MainWindow and editor.

All photos/settings are synthetic and private. The candidate is always explicit.
"""
from __future__ import annotations

import argparse
import faulthandler
import hashlib
import json
import os
import shutil
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    faulthandler.dump_traceback_later(45, repeat=True)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--raw", help="Optional public RAW fixture for recipe stash/restore validation")
    args = parser.parse_args()
    root = Path(args.output).resolve()
    photos = root / "photos"
    photos.mkdir(parents=True, exist_ok=True)
    (photos / "nested").mkdir(exist_ok=True)
    os.environ["IMAGE_TRIAGE_PHOTOCRAFT_EXE"] = str(Path(args.executable).resolve())
    os.environ["IMAGE_TRIAGE_NO_EDITOR_PRESTART"] = "1"
    os.environ["IMAGE_TRIAGE_LOG_DIR"] = str(root / "logs")
    # Live-reload checks edit private copies, never the owner's running design.
    for variable, name in [("PHOTOCRAFT_CAMERA_RAW_LAYOUT_FILE", "layout.json"), ("PHOTOCRAFT_THEME_FILE", "tokens.json")]:
        source = os.environ.get(variable)
        if source:
            shutil.copyfile(source, root / name)
            os.environ[variable] = str(root / name)
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication
    from _check_settings import use_throwaway_settings
    from _desktop import place
    from image_triage.app_identity import user_settings
    from image_triage.photocraft_bridge import PhotoCraftControl
    from image_triage.scanner import normalized_path_key
    from image_triage.ui.native_image_layer import CAMERA_RAW_FIRST_KEY, EDITOR_ON_DEMAND_KEY
    from image_triage.window import MainWindow

    QCoreApplication.setOrganizationName("Image Triage")
    QCoreApplication.setApplicationName("Image Triage")
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    use_throwaway_settings()
    settings = user_settings()
    settings.setValue(CAMERA_RAW_FIRST_KEY, True)
    settings.setValue(EDITOR_ON_DEMAND_KEY, False)
    hashes = {}
    first_name = "first.nef" if args.raw else "first.png"
    for name, color in [("first.png", "#404040"), ("second.png", "#303060")]:
        path = photos / name
        image = QImage(1200, 960, QImage.Format.Format_RGB32)
        image.fill(QColor(color))
        assert image.save(str(path))
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    if args.raw:
        (photos / "first.png").unlink()
        hashes.pop("first.png")
        shutil.copyfile(args.raw, photos / first_name)
        hashes[first_name] = hashlib.sha256((photos / first_name).read_bytes()).hexdigest()

    window = MainWindow()
    place(window)
    controller = window._preview_ctl
    close_save_errors = []
    controller._photocraft_close_save_failed.connect(lambda path, error: close_save_errors.append((path, error)))
    preview = window.preview
    place(preview)

    def wait(predicate, what, timeout=90):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        raise AssertionError(f"Timed out waiting for {what}")

    probe = None

    def fast():
        nonlocal probe
        if probe is None:
            proc = controller._photocraft
            probe = PhotoCraftControl(proc.port, proc.token)
            probe.read_root = proc.read_root
        return probe

    def strip():
        return fast().call("ui.filmstrip")

    def settle(seconds=0.6):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)

    result = {}
    proc = None
    try:
        window._scan.load_folder(str(photos))
        wait(lambda: len(window._records) == 3, "two photos and folder")
        indexes = {Path(record.path).name: index for index, record in enumerate(window._records)}
        first = indexes[first_name]
        window.grid.set_logical_selection([first], current_index=first)
        controller.open_preview(first)
        wait(lambda: controller._photocraft is not None and preview._native_filmstrip_active, "native strip handshake")
        proc = controller._photocraft
        assert Path(proc.executable).resolve() == Path(args.executable).resolve(), "candidate fallback"
        wait(lambda: strip().get("enabled") and strip().get("rect"), "native geometry")
        settle()  # The acknowledged detach queues a native child-window resize.
        assert strip()["total"] == 2, "folder was sent to native strip"
        assert preview._filmstrip.isHidden()
        assert preview._filmstrip.parentWidget().layout().indexOf(preview._filmstrip) == -1
        pid = proc.process.pid
        result["native_strip_detaches_retained_qt_strip"] = True
        result["folder_entries_excluded"] = True
        control = fast()
        control.call("ui.menu.invoke", {"id": "filter.cameraRaw", "params": {"ui": {"set": {"exposure": 1.0}}}})
        design = control.call("ui.inspect")["cameraRawDesign"]

        def geometry_ready():
            state = strip()
            viewport = control.call("ui.viewport")
            rect = state.get("rect") or {}
            expected = viewport["window"]["height"] / viewport["window"]["pixelsPerPoint"]
            bottom = rect.get("y", -10000) + rect.get("height", 0) + design["navigationHeight"] + design["footerHeight"]
            return (not window._scan_in_progress and state.get("enabled") and preview._native_filmstrip_active
                    and abs(bottom - expected) < 2 and not (viewport.get("cameraRaw") or {}).get("busy"))

        def click_photo(position, name):
            settle()
            wait(geometry_ready, "settled native geometry before a thumbnail click")
            rect = strip()["rect"]
            metrics = design["filmstrip"]
            x = rect["x"] + metrics["horizontalPadding"] + position * (metrics["cellWidth"] + metrics["gap"]) + metrics["cellWidth"] / 2
            control.call("ui.click", {"x": x, "y": rect["y"] + rect["height"] / 2})
            print(f"clicked {name} at {x}, {rect['y'] + rect['height'] / 2}", flush=True)
            wait(lambda: proc.current_source == normalized_path_key(str(photos / name)) and strip()["current"] == position, f"native click on {name}")
            assert window.grid.current_index() == indexes[name]
            assert controller._photocraft.process.pid == pid

        click_photo(1, "second.png")
        click_photo(0, first_name)
        if args.raw:
            wait(lambda: control.call("ui.cameraRaw").get("recipe", {}).get("cameraRaw", {}).get("exposure") == 1.0, "restored Camera Raw adjustment")
            result["raw_recipe_stash_restore"] = True
            print("RAW recipe restored", flush=True)
        result["native_click_grid_selection_and_stash_restore"] = True
        controller.handle_preview_rating_requested(str(photos / first_name), 3)
        wait(lambda: (strip().get("selected") or {}).get("rating") == 3, "host rating propagation")
        print("host rating propagated", flush=True)
        result["rating_propagation"] = True
        settle()
        wait(geometry_ready, "settled native geometry after annotation update")
        rect = strip()["rect"]
        x = design["navigationFitWidth"] + design["navigationZoomWidth"] + 1 + 8 + 18
        y = rect["y"] + rect["height"] + design["navigationHeight"] / 2
        control.call("ui.click", {"x": x, "y": y})
        print(f"collapse click {x}, {y}: {strip()}", flush=True)
        wait(lambda: strip()["collapsed"], "collapse click")
        assert preview._filmstrip.isHidden(), "collapse restored legacy strip"
        control.call("ui.key", {"key": "ArrowRight"})
        wait(lambda: proc.current_source == normalized_path_key(str(photos / "second.png")) and strip()["current"] == 1, "keyboard navigation while collapsed")
        settle()
        control.call("ui.key", {"key": "Home"})
        wait(lambda: proc.current_source == normalized_path_key(str(photos / first_name)) and strip()["current"] == 0, "Home while collapsed")
        settle()
        result["collapsed_keyboard_navigation"] = True
        control.call("ui.click", {"x": x, "y": y})
        wait(lambda: not strip()["collapsed"], "expand click")
        settle()
        result["native_collapse_expand"] = True
        rect = strip()["rect"]
        review_left = rect["width"] / 2 - 75
        review_y = rect["y"] + rect["height"] + design["navigationHeight"] / 2
        control.call("ui.click", {"x": review_left + 18 + 3 * 22, "y": review_y})
        wait(lambda: (strip().get("selected") or {}).get("rating") == 4, "native star to host rating")
        control.call("ui.click", {"x": review_left + 135, "y": review_y})
        wait(lambda: (strip().get("selected") or {}).get("rejected") is True, "native reject to host annotation")
        result["native_rating_and_reject_actions"] = True
        control.call("ui.menu.invoke", {"id": "filter.cameraRaw", "params": {"ui": {"set": {"exposure": 1.5}}}})
        settle()
        control.call("ui.click", {"x": rect["width"] - 29.5, "y": review_y})
        wait(lambda: strip().get("comparisonSplit") is True, "native split comparison")
        settle()
        control.call("ui.screenshot", {"path": "native-comparison.png", "focus": False})
        comparison = QImage(str(photos / ".image_triage_edits" / "native-comparison.png"))
        image_rect = control.call("ui.viewport")["cameraRaw"]["preview"]
        y_pixel = int(image_rect["y"] + image_rect["height"] / 2)
        left_pixel = comparison.pixelColor(int(image_rect["x"] + image_rect["width"] / 4), y_pixel)
        right_pixel = comparison.pixelColor(int(image_rect["x"] + image_rect["width"] * 3 / 4), y_pixel)
        if not args.raw:
            assert right_pixel.red() > left_pixel.red() + 10, "split comparison did not show original and adjusted pixels"
        settle()  # Finish screenshot readback before injecting another pointer action.
        control.call("ui.click", {"x": rect["width"] - 29.5, "y": review_y})
        wait(lambda: strip().get("comparisonSplit") is False, "leaving split comparison")
        result["native_before_after_split_pixels" if not args.raw else "native_before_after_split_toggle"] = True
        old_width = rect["width"]
        preview.showNormal()
        preview.resize(1500, 950)
        wait(lambda: strip()["rect"]["width"] != old_width, "native resize after host layout changes")
        assert preview._filmstrip.isHidden()
        result["host_resize_integration"] = True
        resized_width = strip()["rect"]["width"]
        preview.showFullScreen()
        wait(lambda: strip()["rect"]["width"] != resized_width and preview.isFullScreen(), "host fullscreen integration")
        settle()
        assert preview._filmstrip.isHidden()
        result["host_fullscreen_integration"] = True
        tokens = root / "tokens.json"
        if tokens.exists():
            original_tokens = tokens.read_bytes()
            try:
                changed = json.loads(original_tokens)
                changed["tab_strip"] = "#203020"
                tokens.write_text(json.dumps(changed), encoding="utf-8")
                settle(1.2)
                control.call("ui.screenshot", {"path": "live-theme.png", "focus": False})
                image = QImage(str(photos / ".image_triage_edits" / "live-theme.png"))
                scale = control.call("ui.viewport")["window"]["pixelsPerPoint"]
                rect = strip()["rect"]
                pixel = image.pixelColor(int(rect["width"] * scale * 0.75), int((rect["y"] + 10) * scale))
                assert pixel.name() == "#203020", f"native live theme did not reload: {pixel.name()}"
                result["native_live_theme_reload"] = True
            finally:
                tokens.write_bytes(original_tokens)
                settle(1.2)
        control.call("ui.screenshot", {"path": "native-filmstrip.png", "focus": False})
        result["screenshot"] = str(photos / ".image_triage_edits" / "native-filmstrip.png")
        result["executable"] = proc.executable
        preview.close()
        controller.shutdown_photocraft()
        app.processEvents()
        assert not close_save_errors, f"closing lost its save handoff: {close_save_errors}"
        assert proc.process.poll() is not None
        result["process_cleanup"] = True
        assert all(hashlib.sha256((photos / name).read_bytes()).hexdigest() == digest for name, digest in hashes.items())
        result["originals_unchanged"] = True
        result["ok"] = True
    finally:
        if probe is not None:
            probe.close()
        controller.shutdown_photocraft()
        preview.photocraft_close_guard = None
        preview.close()
        window.close()
        (root / "results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2), flush=True)
    faulthandler.cancel_dump_traceback_later()


if __name__ == "__main__":
    main()
