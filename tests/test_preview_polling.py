from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from image_triage.models import ImageRecord
from image_triage.preview import FullScreenPreview, PreviewEntry


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


if __name__ == "__main__":
    unittest.main()

