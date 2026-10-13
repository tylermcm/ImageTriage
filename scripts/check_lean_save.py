"""Opt-in live check of the lean save, with the real hosted editor and a real photo.

Run: python scripts/check_lean_save.py --executable PATH --image PHOTO --output DIR [--close immediately]

While a photo is open the autosave writes the project and the screen-sized JPEG and leaves the full-size render (the heaviest encode of a save)
out. The check edits the photo and autosaves, then either leaves it alone until the render appears (the default: the autosave writes it after a
pause in editing) and closes the popout, or closes the popout at once (``--close immediately``: closing must not wait for the render; the
editor, kept running outside the popout, writes it afterwards). It times how long closing holds the UI thread, checks that the editor
is still up and answering after the close, reopens the photo without a new editor, and times quitting.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from image_triage import photocraft_bridge as bridge
from image_triage.models import ImageRecord
from image_triage.preview import FullScreenPreview, PreviewEntry
from image_triage.preview_controller import PreviewController
from image_triage.app_identity import user_settings
from image_triage.cache import EditStateMemo
from _check_settings import use_throwaway_settings
from _desktop import place
from image_triage.scanner import normalized_path_key
from image_triage.ui.native_image_layer import CAMERA_RAW_FIRST_KEY, EDITOR_ON_DEMAND_KEY


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--close", choices=("after-pause", "immediately"), default="after-pause")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    shutil.rmtree(output, ignore_errors=True)
    output.mkdir(parents=True)
    photo = output / Path(args.image).name
    shutil.copyfile(args.image, photo)
    app = QApplication.instance() or QApplication([])
    use_throwaway_settings()  # never the profile the app uses: a check that dies must not leave settings pinned there
    settings = user_settings()
    settings.setValue(CAMERA_RAW_FIRST_KEY, False)  # the ordinary editor on the photo, as the handoff check does
    settings.setValue(EDITOR_ON_DEMAND_KEY, False)
    parent = QObject()
    controller = PreviewController(parent)
    preview = FullScreenPreview()
    place(preview)  # on the checks' own virtual desktop, behind other windows, never taking the keyboard
    statuses = []
    controller._window = SimpleNamespace(
        _preview=preview,
        statusBar=lambda: SimpleNamespace(showMessage=lambda *message: statuses.append(message)),
        grid=SimpleNamespace(update_items=lambda *_: None),
        thumbnail_manager=SimpleNamespace(edit_states=EditStateMemo()),
        _records_view=SimpleNamespace(resume_background_indexing=lambda: None),
        _winner_ladder_state=None,
        _quick_view_mode=False,
        _preview_navigation_dirty=False,
    )
    controller._photocraft_executable = str(Path(args.executable).resolve())
    preview.photocraft_edit_requested.connect(controller.handle_photocraft_edit_requested)
    preview.photocraft_host_resized.connect(controller.resize_photocraft)
    preview.photocraft_close_guard = controller.prepare_photocraft_preview_close
    preview.closed.connect(controller.handle_preview_closed)
    preview.set_photocraft_available(True)
    stat = photo.stat()
    record = ImageRecord(str(photo), photo.name, stat.st_size, stat.st_mtime_ns)

    def wait_for(predicate, what, timeout=240):
        deadline = time.monotonic() + timeout
        while not predicate():
            app.processEvents()
            if time.monotonic() > deadline:
                raise AssertionError(f"timed out waiting for {what}: {statuses}")
            time.sleep(0.01)
        app.processEvents()

    def run(fn, *args, timeout=300):
        # Never block this thread: the embedded editor shares its host's input queue, so a host that is not pumping events stalls it.
        future = controller._photocraft_executor.submit(fn, *args)
        wait_for(future.done, f"{getattr(fn, '__name__', fn)}", timeout)
        return future.result()

    results: dict = {"image": photo.name, "executable": controller._photocraft_executable}
    try:
        preview.show_entries([PreviewEntry(record, record.path)])
        preview.set_browse_context(1, 0, lambda _index: QPixmap())
        opened = controller._photocraft_future
        wait_for(lambda: opened.done() and preview.photocraft_edit_active() and controller._photocraft is not None
                 and controller._photocraft.current_source == normalized_path_key(record.path), "the editor to open the photo")
        proc = controller._photocraft
        assert getattr(proc.control, "stash_without_preview_supported", False) is True, "this editor cannot save without the full-size render"
        time.sleep(4)  # the full-size redraw after the open
        run(proc.control.execute, "image.adjustments.invert")
        time.sleep(4)
        edits = bridge.edit_root_for(photo.parent)
        png, display, project = bridge.rendered_preview_path(str(photo)), bridge.display_render_path(str(photo)), bridge.sidecar_pcraft_path(str(photo))

        started = time.perf_counter()
        run(controller._autosave_photocraft)
        wait_for(lambda: (run(controller._poll_photocraft_stashes, proc) or True) and not proc.pending_stashes, "the autosave to finish")
        results["autosave_s"] = round(time.perf_counter() - started, 1)
        results["after_autosave"] = {"project": project.is_file(), "display_jpeg": display.is_file(), "full_size_png": png.is_file()}
        assert project.is_file() and display.is_file(), "the autosave wrote neither the project nor the screen-sized JPEG"
        assert not png.is_file(), "the autosave wrote the full-size render"
        assert bridge.render_is_behind(str(photo)) and str(proc.current_sidecar) in proc.preview_missing

        if args.close == "after-pause":
            # The autosave timer ticks every 1.5 s while the editor is up; after a pause in editing it writes the render.
            paused = time.perf_counter()
            while not png.is_file() and time.perf_counter() - paused < 180:
                run(controller._autosave_photocraft)
                wait_for(lambda: (run(controller._poll_photocraft_stashes, proc) or True) and not proc.pending_stashes, "a save to finish")
                pump_until = time.monotonic() + 1.5
                while time.monotonic() < pump_until:
                    app.processEvents()
                    time.sleep(0.01)
            results["render_written_after_pause_s"] = round(time.perf_counter() - paused, 1)
            assert png.is_file() and not bridge.render_is_behind(str(photo)), "the render was not written after a pause in editing"
            assert str(proc.current_sidecar) not in proc.preview_missing

        closing = time.perf_counter()
        preview.close()
        results["close_held_the_ui_thread_ms"] = round((time.perf_counter() - closing) * 1000)
        results["after_close"] = {"full_size_png": png.is_file(), "render_behind": bridge.render_is_behind(str(photo)), "editor_running": proc.process.poll() is None}
        assert proc.process.poll() is None and controller._photocraft is proc, "closing the popout quit the editor"
        started = time.perf_counter()
        run(proc.fast_lane().call, "ui.viewport", timeout=20)
        results["answer_after_close_ms"] = round((time.perf_counter() - started) * 1000)
        if args.close == "immediately":
            # What the close did not wait for is written by the parked editor.
            wait_for(lambda: png.is_file() and not bridge.render_is_behind(str(photo)), "the full-size render after the close", 180)
            results["render_written_after_close_s"] = round(time.perf_counter() - closing, 1)
        wait_for(lambda: controller._photocraft_future is None or controller._photocraft_future.done(), "the after-close save to settle", 180)
        assert not bridge.render_is_behind(str(photo)), "the full-size render is behind the project after the close"

        # Reopen: the same editor, no cold start.
        pid = proc.process.pid
        reopening = time.perf_counter()
        preview.show_entries([PreviewEntry(record, record.path)])
        preview.set_browse_context(1, 0, lambda _index: QPixmap())
        wait_for(lambda: preview.photocraft_edit_active() and controller._photocraft is not None
                 and controller._photocraft.current_source == normalized_path_key(record.path)
                 and (controller._photocraft_future is None or controller._photocraft_future.done()), "the editor to show the photo again")
        results["reopen_ms"] = round((time.perf_counter() - reopening) * 1000)
        assert controller._photocraft.process.pid == pid, "reopening started a new editor"
        preview.close()
        wait_for(lambda: controller._photocraft_future is None or controller._photocraft_future.done(), "the second close to settle", 180)

        quitting = time.perf_counter()
        controller.shutdown_photocraft()
        results["quit_ms"] = round((time.perf_counter() - quitting) * 1000)
        assert proc.process.poll() is not None, "the editor did not exit when asked to quit"
        assert results["quit_ms"] < 2500, "quitting ran into its timeout"
        results["written_mb"] = {path.name.replace(photo.name, "*"): round(path.stat().st_size / 1048576, 1) for path in sorted(edits.iterdir()) if path.is_file()}
        results["ok"] = True
    finally:
        controller.shutdown_photocraft()
        preview.photocraft_close_guard = None
        preview.close()  # before the worker goes: closing queues work on it
        controller._photocraft_executor.shutdown(wait=True)
    (output / "lean-save.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
