"""Run from the Image Triage checkout. These assertions confirm the audited bugs.

Uses fake editor controls and temporary images; does not launch an editor.
"""
from pathlib import Path
import sys
import tempfile
import threading
import types
import os
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
package = types.ModuleType("tests")
package.__path__ = [str(ROOT / "tests")]
sys.modules["tests"] = package
import tests.conftest  # sandbox Qt settings, AppData and logs before application imports

from PySide6.QtCore import QObject, QSize
from PySide6.QtGui import QImage, QColor
from PySide6.QtWidgets import QApplication
from image_triage import photocraft_bridge as bridge
from image_triage import preview_controller as module
from image_triage.edit_storage import session_has_edits
from image_triage.edit_render_headless import render_edited_image
from image_triage.image_resize import _LoadedResizeImage, apply_edits_if_present
from image_triage.cache import ThumbnailKey, MemoryThumbnailCache, edit_state_for
from image_triage.thumbnails import ThumbnailRequest, ThumbnailTask
from queue import SimpleQueue

app = QApplication.instance() or QApplication([])

def controller():
    parent = QObject()
    ctl = module.PreviewController(parent)
    ctl._parent_keepalive = parent
    return ctl

def cleanup(ctl):
    ctl._photocraft = None
    for name in ("_photocraft_save_timer", "_photocraft_dwell_timer", "_photocraft_rest_timer", "_photocraft_resize_timer"):
        getattr(ctl, name).stop()
    ctl._photocraft_executor.shutdown(wait=True)
    ctl._photocraft_preview_executor.shutdown(wait=True)

def image(path, width, height, color):
    value = QImage(width, height, QImage.Format.Format_RGB32)
    value.fill(QColor(color))
    assert value.save(str(path))
    return value

with tempfile.TemporaryDirectory(prefix="image-triage-audit-repro-") as folder:
    root = Path(folder)
    source = root / "frame.png"
    original = image(source, 6000, 4000, "blue")
    edits = bridge.sidecar_pcraft_path(str(source)).parent
    edits.mkdir()
    image(bridge.display_render_path(str(source)), 2560, 1707, "red")
    bridge.recipe_path(str(source)).write_text('{"version":1,"cameraRaw":{"exposure":1}}')
    loaded = _LoadedResizeImage(image=original, exif_bytes=None, icc_profile=None)
    assert not session_has_edits(source)
    exported = apply_edits_if_present(str(source), loaded)
    assert exported.image.pixelColor(0, 0).blue() == 255
    print("CONFIRMED: recipe-only edits are absent from convert/resize/workflow exports")

    stat = source.stat()
    key = ThumbnailKey(str(source), stat.st_mtime_ns, stat.st_size, 64, 64,
        edit_state=edit_state_for(source))
    disk = Mock()
    disk.load.return_value = None
    results = SimpleQueue()
    ThumbnailTask(ThumbnailRequest(key, str(source), QSize(64, 64)),
        MemoryThumbnailCache(), disk, results).run()
    _, _, thumb = results.get()
    assert key.edit_state != (0, 0) and thumb.pixelColor(0, 0).blue() == 255
    print("CONFIRMED: the thumbnail worker caches the original despite an existing recipe render and an edited cache key")

    bridge.sidecar_pcraft_path(str(source)).write_bytes(b"project existence is sufficient for this path")
    exported = apply_edits_if_present(str(source), loaded)
    assert exported.image.pixelColor(0, 0).red() > 240
    assert exported.image.size() != original.size()
    print(f"CONFIRMED: full export uses a 2560x1707 JPEG enlarged to {exported.image.width()}x{exported.image.height()}, instead of the 6000x4000 source")

    display = bridge.display_render_path(str(source))
    png = bridge.rendered_preview_path(str(source))
    image(display, 64, 64, "blue")  # earlier autosave
    image(png, 64, 64, "red")  # subsequent native Save, which writes no display JPEG
    os.utime(display, (1000, 1000))
    os.utime(png, (1010, 1010))
    assert bridge.saved_render_path(str(source)) == display
    assert QImage(str(bridge.saved_render_path(str(source)))).pixelColor(0, 0).blue() > 240
    print("CONFIRMED: a native Save's newer PNG is hidden by the earlier display JPEG when saves are within 120 seconds")

    ctl = controller()
    try:
        control = Mock()
        control.document_revision.return_value = 2  # development completed at 1; a user edit advanced to 2
        def call(method, params=None):
            if method == "ui.cameraRaw":
                return {"open": False}
            if method == "jobs.list":
                return {"jobs": []}
            return {"pending": True, "job": 1}
        control.call.side_effect = call
        proc = bridge.PhotoCraftProcess(process=Mock(), control=control, hwnd=1,
            token_file=root / "token", read_root=root, write_root=edits,
            camera_raw_first=True, camera_raw_rebaseline=True)
        proc.source_path = str(root / "early-edit.NEF")
        proc.current_sidecar = edits / "early-edit.NEF.pcraft"
        proc.opened_revision = 1
        ctl._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=False, lean=True)
        ctl._save_photocraft_sidecar_if_dirty(proc)  # navigation/close after the baseline poll
        assert proc.opened_revision == 2
        assert not any(c.args[0] == "app.stash" for c in control.call.call_args_list)
        print("CONFIRMED: the first edit before the rebaseline poll is never stashed, even on leaving")
    finally:
        cleanup(ctl)

    ctl = controller()
    entered, release = threading.Event(), threading.Event()
    late = Mock()
    def launch(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return late
    try:
        with patch.object(module.photocraft_bridge, "launch_photocraft", launch):
            future = ctl._photocraft_executor.submit(ctl._prestart_photocraft, str(root))
            assert entered.wait(5)
            ctl.shutdown_photocraft()
            release.set()
            future.result(5)
        assert ctl._photocraft is late and not late.shutdown.called
        print("CONFIRMED: shutdown returns while prestart runs; the editor is published after shutdown")
    finally:
        release.set()
        cleanup(ctl)
