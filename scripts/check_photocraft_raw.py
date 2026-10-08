"""Real Windows RAW-smart handoff smoke using a sensor DNG or Nikon NEF.

Generate the fixture with PhotoCraft's raw_smart_fixture example first.
Run: python scripts/check_photocraft_raw.py --executable EXE --raw FIXTURE.dng --output DIR
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
import threading

import psutil
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QApplication
from image_triage import photocraft_bridge as bridge
from image_triage.models import ImageRecord
from image_triage.preview import FullScreenPreview, PreviewEntry
from image_triage.preview_controller import PreviewController
from image_triage.scanner import normalized_path_key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True)
    parser.add_argument("--raw", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    raw = output / ("first" + Path(args.raw).suffix)
    second = output / ("second" + Path(args.raw).suffix)
    shutil.copyfile(args.raw, raw)
    shutil.copyfile(args.raw, second)
    image = QImage(120, 80, QImage.Format.Format_RGB32)
    image.fill(QColor("blue"))
    ordinary = output / "third.png"
    assert image.save(str(ordinary))
    paths = [raw, second, ordinary]
    hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    app = QApplication.instance() or QApplication([])
    parent = QObject()
    controller = PreviewController(parent)
    preview = FullScreenPreview()
    updates, messages = [], []
    controller._window = SimpleNamespace(_preview=preview, statusBar=lambda: SimpleNamespace(showMessage=lambda *m: messages.append(m)),
                                        grid=SimpleNamespace(update_items=updates.append))
    controller._photocraft_executable = str(Path(args.executable).resolve())
    preview.photocraft_edit_requested.connect(controller.handle_photocraft_edit_requested)
    preview.photocraft_host_resized.connect(controller.resize_photocraft)
    preview.photocraft_close_guard = controller.prepare_photocraft_preview_close
    preview.set_photocraft_available(True)
    records = [ImageRecord(str(p), p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in paths]
    thumb = QPixmap.fromImage(image)

    def wait_for(predicate, timeout=180):
        deadline = time.monotonic() + timeout
        while not predicate():
            app.processEvents()
            if time.monotonic() > deadline:
                raise AssertionError(f"RAW handoff timed out: {messages}")
            time.sleep(0.01)
        app.processEvents()

    def select(index):
        start = time.perf_counter()
        preview.show_entries([PreviewEntry(records[index], records[index].path)])
        preview.set_browse_context(3, index, lambda _: thumb)
        future = controller._photocraft_future
        wait_for(lambda: future.done() and preview.photocraft_edit_active() and controller._photocraft is not None
                 and controller._photocraft.current_source == normalized_path_key(str(paths[index])))
        return round((time.perf_counter() - start) * 1000, 1)

    def call(method, params=None):
        return controller._photocraft_executor.submit(controller._photocraft.control.call, method, params).result(timeout=180)

    def inspect():
        return call("engine.execute", {"command":"layer.rawDevelopment.inspect"})

    proc = None
    timings = {}
    peak = [0]
    stop = threading.Event()
    def memory_sample():
        while not stop.wait(.1):
            active = controller._photocraft
            if active is not None:
                try:
                    peak[0] = max(peak[0], psutil.Process(active.process.pid).memory_info().rss)
                except psutil.Error:
                    pass
    monitor = threading.Thread(target=memory_sample, daemon=True)
    monitor.start()
    try:
        timings["coldRawOpenMs"] = select(0)
        proc = controller._photocraft
        assert Path(proc.executable).resolve() == Path(args.executable).resolve(), "candidate failed; validation must not use a fallback editor"
        pid, hwnd = proc.process.pid, proc.hwnd
        assert inspect()["sensorBacked"] is True
        call("ui.rawDevelopment", {"open":True})
        settings = inspect()["settings"]
        initial_exposure = settings["exposure"]
        edited_exposure = -1.0 if initial_exposure != -1.0 else -1.5
        # Rapid slider requests coalesce to the latest settings.
        settings["exposure"] = -0.2
        call("ui.rawDevelopment", {"settings":settings})
        settings["exposure"] = -0.5
        call("ui.rawDevelopment", {"settings":settings})
        settings["exposure"] = edited_exposure
        call("ui.rawDevelopment", {"settings":settings})
        wait_for(lambda: call("ui.rawDevelopment").get("previewReady"))
        call("ui.screenshot", {"path":"raw-development.png", "focus":False})
        # Periodic autosave must keep the draft open; filmstrip navigation commits it.
        controller._photocraft_executor.submit(controller._autosave_photocraft).result(timeout=180)
        assert call("ui.rawDevelopment")["open"]
        timings["rawToRawMs"] = select(1)
        assert inspect()["settings"]["exposure"] == initial_exposure
        assert (controller._photocraft.process.pid, controller._photocraft.hwnd) == (pid, hwnd)
        timings["cachedReturnMs"] = select(0)
        assert inspect()["settings"]["exposure"] == edited_exposure
        call("ui.menu.invoke", {"id":"file.save"})
        controller._photocraft_executor.submit(controller._save_active_photocraft).result(timeout=180)
        app.processEvents()
        assert updates and bridge.rendered_preview_path(str(raw)).is_file()
        timings["rawToPngMs"] = select(2)
        timings["pngToRawMs"] = select(0)
        assert inspect()["settings"]["exposure"] == edited_exposure
        call("ui.menu.invoke", {"id":"file.close"})
        select(0)
        assert inspect()["settings"]["exposure"] == edited_exposure
        assert (controller._photocraft.process.pid, controller._photocraft.hwnd) == (pid, hwnd)
        # Closing the host must settle an open draft just like filmstrip navigation.
        call("ui.rawDevelopment", {"open": True})
        settings["exposure"] = -0.75
        call("ui.rawDevelopment", {"settings": settings})
        preview.close()
        controller.shutdown_photocraft()
        assert proc.process.poll() is not None and not proc.token_file.exists()
        # New process restores the on-disk RAW-backed project, not just warm memory.
        select(0)
        assert Path(controller._photocraft.executable).resolve() == Path(args.executable).resolve(), "restart used a fallback editor"
        assert inspect()["settings"]["exposure"] == -0.75
        preview.close()
        controller.shutdown_photocraft()
        assert controller._photocraft is None
        assert not list(output.rglob("*__photocraft_source.tiff"))
        assert not list(output.rglob("*.sensor.dng")), "temporary sensor transfer was not removed"
        assert all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in hashes.items())
        timings.update(executable=proc.executable, peakEditorRssMiB=round(peak[0]/1048576, 1), sensorBacked=True, draftStashedOnSwitch=True, draftStashedOnClose=True, nativeSave=True, tabReopen=True, restartRestored=True,
                       sameProcessAndWindow=True, originalsUnchanged=True, noTiffConversion=True, gridUpdated=True, cleanup=True)
        (output / "raw-handoff.json").write_text(json.dumps(timings, indent=2), encoding="utf-8")
        print(json.dumps(timings, indent=2), flush=True)
    finally:
        stop.set()
        monitor.join(timeout=1)
        controller.shutdown_photocraft()
        controller._photocraft_executor.shutdown(wait=True)
        preview.photocraft_close_guard = None
        preview.close()


if __name__ == "__main__":
    main()
