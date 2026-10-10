r"""Opt-in live check of the Camera Raw workspace on real raw files (the recipe workflow).

    python scripts/check_camera_raw_recipe.py --executable C:\path\photocraft.exe --output C:\temp\crr \
        --raw C:\raws\a.NEF --raw C:\raws\b.NEF [--unsupported C:\raws\odd.NEF]

Runs the real popout and controller against a real PhotoCraft launched with ``--raw-workspace``:

1. a raw opens in Camera Raw (the open-time dialog), developed from sensor data;
2. adjusting it and moving on keeps the settings as the photo's recipe, writes a render of them, and
   develops and saves nothing;
3. coming back opens the raw in Camera Raw with the recipe;
4. Open develops the raw (the full editor); developing it is not mistaken for editing it;
5. editing in the full editor saves a document and a render;
6. closing that document returns to the raw in Camera Raw with its recipe, and Open continues the document;
7. a raw the editor cannot develop from sensor data (``--unsupported``) takes the retained-RAW route.
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

from PySide6.QtCore import QObject  # noqa: E402
from PySide6.QtGui import QImage, QPixmap  # noqa: E402
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
    import logging

    logging.basicConfig(level=logging.INFO, format="LOG %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--raw", action="append", required=True)
    parser.add_argument("--unsupported")
    parser.add_argument("--look", type=float, default=0.0, help="seconds spent looking at each photo before moving on (lets the next raw be prepared)")
    parser.add_argument("--trace", action="store_true", help="print every slow control call")
    parser.add_argument("--browse-only", action="store_true", help="stop after the browsing steps (1-3)")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    if args.trace:
        original_call = bridge.PhotoCraftControl.call
        origin = time.perf_counter()

        def traced(self, method, params=None):
            began = time.perf_counter()
            try:
                return original_call(self, method, params)
            finally:
                took = (time.perf_counter() - began) * 1000
                if took > 60:
                    print(f"CALL t={(began - origin):7.2f}s {method:<22} {took:8.0f} ms", flush=True)

        bridge.PhotoCraftControl.call = traced
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
        _records=[],
    )
    controller._photocraft_executable = str(Path(args.executable).resolve())
    preview.photocraft_edit_requested.connect(controller.handle_photocraft_edit_requested)
    preview.photocraft_host_resized.connect(controller.resize_photocraft)
    preview.photocraft_prioritize_requested.connect(controller.prioritize_photocraft_open)
    preview.native_layer_painted.connect(controller._handle_native_layer_painted)
    preview.photocraft_close_guard = controller.prepare_photocraft_preview_close
    preview.set_photocraft_available(True)

    photos = [*args.raw, *([args.unsupported] if args.unsupported else [])]
    records, thumbnails = [], []
    for source in photos:
        path = output / Path(source).name
        if not path.exists():
            shutil.copyfile(source, path)
        stat = path.stat()
        records.append(ImageRecord(str(path), path.name, stat.st_size, stat.st_mtime_ns))
        thumbnails.append(QPixmap(120, 80))

    def wait_for(predicate, what, timeout=120.0):
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

    probe_state: dict = {}

    def fast():
        """The script's own control connection. The controller's workers use the process's two, and a connection
        carries one request at a time: polling on theirs from this thread would interleave with their requests."""
        p = proc()
        if probe_state.get("port") != p.port:
            probe = bridge.PhotoCraftControl(p.port, p.token)
            probe.read_root = p.read_root
            probe_state.update(port=p.port, probe=probe)
        return probe_state["probe"]

    def documents():
        return fast().execute("session.inspect").get("documents", [])

    def camera_raw():
        return fast().call("ui.cameraRaw")

    def select(index, *, dialog=True):
        started = time.perf_counter()
        controller._begin_navigation_timing(records[index].path)
        preview.show_entries([PreviewEntry(records[index], records[index].path)])
        preview.set_browse_context(len(records), index, lambda item: thumbnails[item])
        wait_for(lambda: proc() is not None and proc().current_source == normalized_path_key(records[index].path), f"photo {index} to open")
        opened_at = time.perf_counter()
        if dialog:
            wait_for(lambda: camera_raw().get("open") and camera_raw().get("target") == "openRaw", f"Camera Raw on photo {index}")
        total = round((time.perf_counter() - started) * 1000)
        jobs = [j for j in fast().call("jobs.list").get("jobs", []) if j.get("command") == "app.open"]
        print(f"BREAKDOWN photo {index}: total {total} ms; document open {round((opened_at - started) * 1000)} ms "
              f"(editor's app.open job {round(jobs[-1].get('elapsedMs', 0)) if jobs else '?'} ms); dialog up {round((time.perf_counter() - opened_at) * 1000)} ms after", flush=True)
        return total

    def render_red(path: Path) -> int:
        image = QImage(str(path))
        assert not image.isNull(), f"cannot read {path}"
        return image.pixelColor(image.width() // 2, image.height() // 2).red()

    def mem(label):
        import subprocess

        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command", f"[math]::Round((Get-Process -Id {proc().process.pid}).WorkingSet64/1MB)"],
                                 capture_output=True, text=True).stdout.strip()
            print(f"MEM {label}: {out} MB", flush=True)
        except Exception as error:
            print("MEM failed", error)

    controller._window._records = records
    first = Path(records[0].path)

    def look():
        if args.look:
            end = time.monotonic() + args.look
            while time.monotonic() < end:
                app.processEvents()
                time.sleep(0.02)
    results: dict = {"photos": [Path(p).name for p in photos]}
    try:
        # 1. A raw opens in Camera Raw, developed from its sensor data.
        results["first_raw_in_camera_raw_ms"] = select(0)
        mem("after first raw in Camera Raw")
        look()
        assert proc().camera_raw_first
        state = camera_raw()
        assert state["target"] == "openRaw" and state["changed"] is False, state
        assert len(documents()) == 1
        wait_for(lambda: fast().call("ui.viewport").get("cameraRaw", {}).get("view"), "Camera Raw's view")
        fast().call("ui.screenshot", {"path": "1-camera-raw.png", "focus": False})

        # 2. Adjust and move on: the recipe is kept, a render of it is written, nothing is developed or saved.
        fast().call("ui.cameraRaw", {"set": {"exposure": 1.0, "contrast": 25}})
        assert camera_raw()["changed"] is True
        results["second_raw_in_camera_raw_ms"] = select(1)
        mem("after second raw")
        look()
        recipe_file = bridge.recipe_path(str(first))
        wait_for(recipe_file.is_file, "the recipe to be written")
        recipe = json.loads(recipe_file.read_text(encoding="utf-8"))
        assert recipe["version"] == 1 and recipe["cameraRaw"]["exposure"] == 1.0, recipe
        display = bridge.display_render_path(str(first))
        wait_for(lambda: display.is_file() and display.stat().st_size > 0, "the recipe's render")
        results["recipe_render_red"] = render_red(display)
        assert not bridge.sidecar_pcraft_path(str(first)).exists(), "browsing past a photo saved a document"
        assert len(documents()) == 1

        # 3. Back to the first raw: Camera Raw opens with the recipe.
        results["return_to_first_ms"] = select(0)
        mem("after return to first")
        state = camera_raw()
        assert state["recipe"]["cameraRaw"]["exposure"] == 1.0, state
        assert state["changed"] is False

        if args.browse_only:
            controller.nav_timer.flush()
            print(format_summary(summarize(record.as_dict() for record in controller.nav_timer.finished)))
            return

        # 4. Open develops the raw (the full editor); that is not editing it.
        opened = fast().call("ui.cameraRaw", {"commit": True})
        assert opened["open"] is False
        wait_for(lambda: not any(j.get("state") == "running" for j in fast().call("jobs.list").get("jobs", [])), "the develop to finish", timeout=180)
        time.sleep(controller._photocraft_save_timer.interval() / 1000 * 2 + 0.5)
        app.processEvents()
        assert not bridge.sidecar_pcraft_path(str(first)).exists(), "Open alone saved a document"
        fast().call("ui.screenshot", {"path": "2-full-editor.png", "focus": False})
        mem("after Open developed")

        # 5. Editing in the full editor saves a document.
        fast().execute("image.adjustments.invert")
        sidecar = bridge.sidecar_pcraft_path(str(first))
        try:
            wait_for(sidecar.is_file, "the full editor's document to be saved", timeout=45)
        except AssertionError:
            p = proc()
            print("EXITCODE", hex(p.process.poll() & 0xFFFFFFFF) if p.process.poll() is not None else "running", flush=True)
            try:
                fresh = bridge.PhotoCraftControl(p.port, p.token, timeout=20)
                print("DIAG-fresh", json.dumps({
                    "revision": fresh.document_revision(), "camera_raw": fresh.call("ui.cameraRaw"),
                    "jobs": fresh.call("jobs.list").get("jobs", [])[-5:],
                }, default=str)[:900], flush=True)
            except Exception as error:
                print("DIAG-fresh failed:", error, flush=True)
            print("DIAG-host", json.dumps({
                "opened_revision": p.opened_revision, "rebaseline": p.camera_raw_rebaseline, "current_sidecar": str(p.current_sidecar),
                "current_source": p.current_source, "pending": list(p.pending_stashes), "errors": p.stash_errors,
                "save_timer_active": controller._photocraft_save_timer.isActive(),
                "future_done": controller._photocraft_future.done() if controller._photocraft_future else None,
            }, default=str), flush=True)
            raise

        # 6. Closing it returns to the raw in Camera Raw, with its recipe; Open continues the saved document.
        # Close only once the save has landed and the document is clean (a dirty one would ask first).
        wait_for(lambda: documents() and not documents()[0].get("dirty") and not proc().pending_stashes, "the save to settle", timeout=120)
        fast().call("ui.menu.invoke", {"id": "file.close"})
        assert not documents(), "the document did not close"
        try:
            wait_for(lambda: camera_raw().get("open") and camera_raw().get("target") == "openRaw", "Camera Raw to return after closing", timeout=40)
        except AssertionError:
            p = proc()
            print("DIAG6", json.dumps({
                "current_source": p.current_source, "current_sidecar": str(p.current_sidecar), "source_path": p.source_path,
                "current_path": controller._photocraft_current_path, "documents": documents(), "camera_raw": camera_raw(),
                "dwell_active": controller._photocraft_dwell_timer.isActive(), "generation": controller._photocraft_generation,
                "future_done": controller._photocraft_future.done() if controller._photocraft_future else None,
                "visible": preview.isVisible(), "rebaseline": p.camera_raw_rebaseline,
            }, default=str)[:1200], flush=True)
            raise
        state = camera_raw()
        assert state["recipe"]["cameraRaw"]["exposure"] == 1.0
        fast().call("ui.screenshot", {"path": "3-back-in-camera-raw.png", "focus": False})
        continued = fast().call("ui.cameraRaw", {"commit": True})
        assert continued["result"].get("continued"), continued
        wait_for(lambda: not any(j.get("state") == "running" for j in fast().call("jobs.list").get("jobs", [])), "the saved document to open", timeout=120)
        results["open_continues_saved_document"] = True

        # 7. A raw the editor cannot develop from sensor data takes the retained-RAW route.
        if args.unsupported:
            index = len(records) - 1
            results["unsupported_raw_ms"] = select(index, dialog=False)
            wait_for(lambda: len(documents()) == 1, "the fallback document")
            assert not camera_raw().get("open"), "the fallback route must not open Camera Raw"
            results["unsupported_raw_fallback"] = True

        controller.nav_timer.flush()
        results["navigation"] = summarize(record.as_dict() for record in controller.nav_timer.finished)
        results["ok"] = True
        (output / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(json.dumps({k: v for k, v in results.items() if k != "navigation"}, indent=2))
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
