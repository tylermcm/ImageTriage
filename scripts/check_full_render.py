"""Opt-in native checks of hidden full-size rendering and shutdown during launch.

Uses a copied RAW fixture and a private editor profile. Never opens the owner's
project or changes the owner's preferences. Requires an explicit candidate exe.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import shutil
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication
import rawpy

from image_triage import full_render, photocraft_bridge as bridge
from image_triage.edited_export import bit_depth, edited_image_for_export
from _check_settings import use_throwaway_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", required=True)
    parser.add_argument("--raw", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    app = QApplication.instance() or QApplication([])
    use_throwaway_settings()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    source = Path(args.raw).resolve()
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    photo = output / source.name
    shutil.copy2(source, photo)
    root = bridge.edit_root_for(output)
    root.mkdir(exist_ok=True)
    bridge.recipe_path(str(photo)).write_text(json.dumps({"version": 1, "cameraRaw": {"exposure": 1.0, "contrast": 25}, "identity": False}))
    with rawpy.imread(str(photo)) as raw:
        sizes = raw.sizes
        expected = (getattr(sizes, "crop_width", 0) or sizes.width, getattr(sizes, "crop_height", 0) or sizes.height)
        if sizes.flip in (5, 6):
            expected = expected[::-1]
    processes = []

    def launch(folder):
        proc = bridge.launch_photocraft(None, read_root=folder, write_root=str(root), executable=args.executable, idle=True)
        processes.append(proc)
        assert Path(proc.process.args[0]).resolve() == Path(args.executable).resolve(), "candidate fallback was used"
        assert not ctypes.windll.user32.IsWindowVisible(proc.hwnd), "hidden startup showed a window"
        return proc

    service = full_render.FullRenderService(lambda: None, launch=launch)
    results = {}
    try:
        full_render.register(service)
        edited = edited_image_for_export(str(photo))
        assert edited is not None
        size = (edited.image.width(), edited.image.height())
        assert size == expected, (size, expected)
        assert bit_depth(edited.image) == 16 and edited.icc_profile
        assert all(proc.process.poll() is not None for proc in processes)
        assert not list(root.glob("*.photocraft.export-*")), "temporary export was not removed"
        results["hidden_recipe_render"] = {"size": size, "depth": bit_depth(edited.image), "icc": True}

        cancelling = full_render.FullRenderService(lambda: None, launch=launch)
        cancelled_replies = []
        renderer = threading.Thread(target=lambda: cancelled_replies.append(cancelling.request(str(photo))))
        renderer.start()
        deadline = time.monotonic() + 60
        while cancelling._running is None and renderer.is_alive() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert cancelling._running is not None, "render finished before its running job could be cancelled"
        cancelling.shutdown()
        renderer.join(30)
        assert not renderer.is_alive() and cancelled_replies[0].outcome == full_render.FAILED
        assert all(proc.process.poll() is not None for proc in processes)
        assert not list(root.glob("*.photocraft.export-*")), "cancelled render left temporary pixels"
        results["shutdown_during_running_render"] = True

        # Hold a real launch just before its process is published to the service.
        entered, release = threading.Event(), threading.Event()

        def delayed_launch(folder):
            proc = launch(folder)
            entered.set()
            assert release.wait(30)
            return proc

        closing_service = full_render.FullRenderService(lambda: None, launch=delayed_launch)
        replies = []
        worker = threading.Thread(target=lambda: replies.append(closing_service.request(str(photo))))
        worker.start()
        assert entered.wait(60), "editor did not start"
        closer = threading.Thread(target=closing_service.shutdown)
        closer.start()
        deadline = time.monotonic() + 10
        while not closing_service._closing and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        release.set()
        worker.join(30)
        closer.join(30)
        assert not worker.is_alive() and not closer.is_alive()
        assert replies[0].outcome == full_render.FAILED
        assert all(proc.process.poll() is not None for proc in processes), "late launch survived shutdown"
        results["shutdown_during_real_launch"] = True
        assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
        results["original_unchanged"] = True
        results["ok"] = True
        (output / "results.json").write_text(json.dumps(results, indent=2))
        print(json.dumps(results, indent=2), flush=True)
    finally:
        service.shutdown()
        for proc in processes:
            if proc.process.poll() is None:
                proc.shutdown()


if __name__ == "__main__":
    main()
