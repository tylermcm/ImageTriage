from __future__ import annotations

import time
import unittest
from queue import SimpleQueue
import tempfile
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

import image_triage.preview as preview_module
from image_triage.metadata import CaptureMetadata
from image_triage.preview import FullScreenPreview, PreviewRequest, PreviewTask


class _FakePool:
    def __init__(self) -> None:
        self.started: list[tuple[PreviewTask, int]] = []

    def start(self, task, priority: int = 0) -> None:
        self.started.append((task, priority))


def _ensure_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class PreviewTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _ensure_app()

    def test_emits_ready_before_metadata(self) -> None:
        original_load_image = preview_module.load_image_for_display
        original_load_metadata = preview_module.load_capture_metadata
        image = QImage(32, 24, QImage.Format.Format_RGB32)
        image.fill(0x112233)
        events: list[str] = []

        def load_image(*_args, **_kwargs):
            events.append("image")
            return image, None

        def load_metadata(_path: str):
            events.append("metadata_started")
            time.sleep(0.01)
            events.append("metadata_done")
            return CaptureMetadata(path=_path, camera="Test Camera")

        preview_module.load_image_for_display = load_image
        preview_module.load_capture_metadata = load_metadata
        queue = SimpleQueue()
        try:
            PreviewTask(
                PreviewRequest(
                    path="C:/temp/frame.nef",
                    token=1,
                    slot=0,
                    target_size=QSize(800, 600),
                    load_metadata=True,
                ),
                queue,
            ).run()
        finally:
            preview_module.load_image_for_display = original_load_image
            preview_module.load_capture_metadata = original_load_metadata

        first = queue.get_nowait()
        second = queue.get_nowait()
        self.assertEqual("ready", first[0])
        self.assertEqual("metadata", second[0])
        self.assertEqual(["image", "metadata_started", "metadata_done"], events)

    def test_preloads_include_metadata_for_first_neighbors_by_default(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_preview_preload_") as temp_dir:
            paths = []
            for index in range(12):
                path = Path(temp_dir) / f"frame_{index:04d}.jpg"
                path.write_bytes(b"jpeg")
                paths.append(str(path))
            preview = FullScreenPreview()
            fake_pool = _FakePool()
            preview._pool = fake_pool

            preview.preload_paths(paths)

            self.assertEqual(10, len(fake_pool.started))
            metadata_flags = [task.request.load_metadata for task, _priority in fake_pool.started]
            self.assertEqual([True, True], metadata_flags[:2])
            self.assertTrue(all(not flag for flag in metadata_flags[2:]))
            preview.close()

    def test_preload_batch_size_limits_or_disables_preloads(self) -> None:
        with tempfile.TemporaryDirectory(prefix="image_triage_preview_preload_") as temp_dir:
            paths = []
            for index in range(12):
                path = Path(temp_dir) / f"frame_{index:04d}.jpg"
                path.write_bytes(b"jpeg")
                paths.append(str(path))
            preview = FullScreenPreview()
            fake_pool = _FakePool()
            preview._pool = fake_pool

            preview.set_preload_batch_size(4)
            preview.preload_paths(paths)
            self.assertEqual(4, len(fake_pool.started))

            fake_pool.started.clear()
            preview.set_preload_batch_size(0)
            preview.preload_paths(paths)
            self.assertEqual(0, len(fake_pool.started))
            preview.close()

    def test_preload_paths_do_not_stat_candidates_on_gui_thread(self) -> None:
        original_file_signature = preview_module._file_signature
        preview = FullScreenPreview()
        fake_pool = _FakePool()
        preview._pool = fake_pool

        def fail_file_signature(_path: str):
            raise AssertionError("preload path signature should not run on the GUI thread")

        preview_module._file_signature = fail_file_signature
        try:
            preview.set_preload_batch_size(3)
            preview.preload_paths([f"//server/share/frame_{index:04d}.nef" for index in range(5)])
        finally:
            preview_module._file_signature = original_file_signature
            preview.close()

        self.assertEqual(3, len(fake_pool.started))
        self.assertTrue(all(task.request.source_signature is None for task, _priority in fake_pool.started))

    def test_active_preview_can_reuse_signatureless_preload_cache(self) -> None:
        preview = FullScreenPreview()
        image = QImage(32, 24, QImage.Format.Format_RGB32)
        image.fill(0x223344)
        target_size = QSize(960, 720)
        path = "C:/temp/frame.nef"
        preload_key = preview._preview_cache_key(
            path,
            None,
            target_size,
            prefer_embedded=True,
            fits_display_settings=None,
        )
        preview._cache_preview_image(preload_key, image)

        cached, active_key = preview._cached_preview_image_with_fallback(
            path,
            (123, 456),
            target_size,
            prefer_embedded=True,
            fits_display_settings=None,
        )

        self.assertIsNotNone(cached)
        self.assertEqual(cached.pixelColor(0, 0).rgb(), image.pixelColor(0, 0).rgb())
        self.assertNotEqual(preload_key, active_key)
        self.assertIsNotNone(preview._cached_preview_image(active_key))
        preview.close()


if __name__ == "__main__":
    unittest.main()
