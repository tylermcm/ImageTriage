from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QSize
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from image_triage.models import ImageRecord
from image_triage.imaging import FitsDisplaySettings
from image_triage.preview import FullScreenPreview, PreviewEntry, PreviewRequest


def _ensure_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _entry(path: str) -> PreviewEntry:
    record = ImageRecord(path=path, name=Path(path).name, size=123, modified_ns=1)
    return PreviewEntry(record=record, source_path=path)


class PreviewPollingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _ensure_app()

    def test_polling_backs_off_when_preview_window_is_inactive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.jpg"
            path.write_bytes(b"jpg")
            preview = FullScreenPreview()
            preview._entries = [_entry(str(path))]
            preview._source_entries = list(preview._entries)
            preview._source_versions = [(1, 1)]
            preview._pending_requests = 0
            preview._refresh_timer.setInterval(preview._refresh_interval_active_ms)

            with patch.object(preview, "isVisible", return_value=True), patch.object(
                preview,
                "isActiveWindow",
                return_value=False,
            ), patch(
                "image_triage.preview._file_signature",
                side_effect=AssertionError("inactive polling should not stat files"),
            ):
                preview._poll_source_updates()

            self.assertEqual(preview._refresh_timer.interval(), preview._refresh_interval_background_ms)
            preview.close()

    def test_stable_compare_mode_polls_focused_plus_round_robin_slot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = []
            for index in range(4):
                path = Path(temp_dir) / f"sample_{index}.jpg"
                path.write_bytes(b"jpg")
                paths.append(str(path))

            preview = FullScreenPreview()
            preview._entries = [_entry(path) for path in paths]
            preview._source_entries = list(preview._entries)
            preview._source_versions = [(1, 1) for _ in paths]
            preview._compare_mode = True
            preview._pending_requests = 0
            preview._stable_poll_cycles = 5
            preview._focused_slot = 0
            preview._poll_round_robin_slot = 1

            polled_paths: list[str] = []

            def _signature(path: str):
                polled_paths.append(path)
                return (1, 1)

            with patch.object(preview, "isVisible", return_value=True), patch.object(
                preview,
                "isActiveWindow",
                return_value=True,
            ), patch("image_triage.preview._file_signature", side_effect=_signature):
                preview._poll_source_updates()

            self.assertEqual(len(polled_paths), 2)
            self.assertEqual(set(polled_paths), {paths[0], paths[2]})
            preview.close()

    def test_edited_discovery_interval_expands_when_no_candidate_is_found(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.jpg"
            path.write_bytes(b"jpg")
            preview = FullScreenPreview()
            preview._entries = [_entry(str(path))]
            preview._source_entries = list(preview._entries)
            preview._source_versions = [(1, 1)]
            preview._pending_requests = 0
            preview._compare_mode = False
            preview._edited_discovery_requested = True
            preview._next_edited_discovery_at = 0.0

            with patch.object(preview, "isVisible", return_value=True), patch.object(
                preview,
                "isActiveWindow",
                return_value=True,
            ), patch(
                "image_triage.preview.time.monotonic",
                return_value=100.0,
            ), patch(
                "image_triage.preview.discover_edited_paths",
                return_value=(),
            ), patch(
                "image_triage.preview._file_signature",
                return_value=(1, 1),
            ):
                preview._poll_source_updates()

            self.assertFalse(preview._edited_discovery_requested)
            self.assertEqual(preview._next_edited_discovery_at, 117.0)
            preview.close()

    def test_fits_preview_cache_and_image_keys_track_stf_state(self) -> None:
        preview = FullScreenPreview()
        path = "C:/temp/sample.fits"
        preview._entries = [_entry(path)]
        preview._source_entries = list(preview._entries)
        preview._source_versions = [(1, 1)]
        preview._current_image_display_tokens = [("auto",)]

        auto_key = preview._preview_cache_key(path, (1, 1), QSize(800, 600), prefer_embedded=True)
        preview._fits_display_settings = FitsDisplaySettings(stf_preset_id="strong")
        strong_key = preview._preview_cache_key(path, (1, 1), QSize(800, 600), prefer_embedded=True)

        self.assertNotEqual(auto_key, strong_key)

        image_key = preview._image_cache_key(0, QImage(32, 24, QImage.Format.Format_RGB32))
        self.assertEqual(image_key[-1], ("auto",))
        preview.close()

    def test_single_entry_preview_does_not_seed_placeholder_image(self) -> None:
        preview = FullScreenPreview()
        path = "C:/temp/sample.nef"
        entry = _entry(path)
        preview._entries = [entry]
        preview._source_entries = [entry]
        placeholder = QImage(1200, 800, QImage.Format.Format_RGB32)
        placeholder.fill(0x112233)
        preview._current_images = [QImage()]
        preview._current_metadata = [None]
        preview._current_placeholder_flags = [False]
        preview._current_image_display_tokens = [()]
        preview._source_versions = [(1, 1)]
        preview._panes = [preview._panes[0]] if preview._panes else []
        preview._seed_entry_images_from_placeholders()

        self.assertTrue(preview._current_images[0].isNull())
        self.assertFalse(preview._current_placeholder_flags[0])
        preview.close()

    def test_single_entry_ready_result_replaces_loading_state_normally(self) -> None:
        preview = FullScreenPreview()
        path = "C:/temp/sample.nef"
        entry = _entry(path)
        preview._entries = [entry]
        preview._source_entries = [entry]
        loaded = QImage(1600, 900, QImage.Format.Format_RGB32)
        loaded.fill(0x445566)
        preview._current_images = [QImage()]
        preview._current_metadata = [None]
        preview._current_placeholder_flags = [False]
        preview._current_image_display_tokens = [()]
        preview._source_versions = [(1, 1)]
        preview._focused_slot = 0
        preview._load_token = 7
        preview._pending_requests = 1
        request = PreviewRequest(
            path=path,
            token=7,
            slot=0,
            target_size=QSize(1600, 900),
            source_signature=(2, 2),
            prefer_embedded=True,
            load_metadata=False,
        )
        preview._result_queue.put(("ready", request, loaded, None))

        with patch.object(preview, "_render_pane") as render_pane, patch.object(preview, "_update_analysis_panel") as update_panel:
            preview._drain_results()

        self.assertFalse(preview._current_images[0].isNull())
        self.assertEqual(preview._current_images[0].pixelColor(0, 0).rgb(), loaded.pixelColor(0, 0).rgb())
        self.assertFalse(preview._current_placeholder_flags[0])
        render_pane.assert_called_once()
        update_panel.assert_called_once()
        preview.close()


if __name__ == "__main__":
    unittest.main()
