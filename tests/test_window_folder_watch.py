from __future__ import annotations

import unittest
from unittest.mock import patch

from image_triage.window import MainWindow


class _StatusBarStub:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def showMessage(self, message: str) -> None:
        self.messages.append(message)


class _TimerStub:
    def __init__(self) -> None:
        self.start_calls: list[int] = []
        self.stopped = False

    def start(self, interval: int) -> None:
        self.start_calls.append(interval)

    def stop(self) -> None:
        self.stopped = True


class _WindowStub:
    def __init__(self, folder: str) -> None:
        self._watch_current_folder_enabled = True
        self._scope_kind = "folder"
        self._current_folder = folder
        self._scan_in_progress = False
        self._folder_watch_refresh_pending = False
        self._folder_watch_refresh_timer = _TimerStub()
        self._status = _StatusBarStub()
        self.load_calls: list[tuple[str, bool]] = []
        self.queued_delays: list[int] = []
        self.refresh_watch_calls = 0

    def statusBar(self) -> _StatusBarStub:
        return self._status

    def _queue_watched_folder_refresh(self, delay_ms: int = 900) -> None:
        self._folder_watch_refresh_pending = True
        self.queued_delays.append(delay_ms)

    def _load_folder(self, folder: str, *, force_refresh: bool = False, chunked_restore: bool = False) -> None:
        self.load_calls.append((folder, force_refresh))

    def _refresh_current_folder_watch(self) -> None:
        self.refresh_watch_calls += 1


class FolderWatchTests(unittest.TestCase):
    def test_handle_watched_folder_change_queues_refresh_for_current_folder(self) -> None:
        folder = r"X:\Shots\Set A"
        window = _WindowStub(folder)

        MainWindow._handle_watched_folder_changed(window, folder)

        self.assertTrue(window._folder_watch_refresh_pending)
        self.assertEqual([900], window.queued_delays)
        self.assertIn("refreshing", window.statusBar().messages[-1].casefold())

    def test_run_watched_folder_refresh_calls_force_refresh(self) -> None:
        folder = r"X:\Shots\Set B"
        window = _WindowStub(folder)
        window._folder_watch_refresh_pending = True

        with patch("image_triage.window.os.path.isdir", return_value=True):
            MainWindow._run_watched_folder_refresh(window)

        self.assertFalse(window._folder_watch_refresh_pending)
        self.assertEqual([(folder, True)], window.load_calls)
        self.assertIn("refreshing changed folder", window.statusBar().messages[-1].casefold())

    def test_run_watched_folder_refresh_requeues_while_scan_active(self) -> None:
        folder = r"X:\Shots\Set C"
        window = _WindowStub(folder)
        window._folder_watch_refresh_pending = True
        window._scan_in_progress = True

        MainWindow._run_watched_folder_refresh(window)

        self.assertTrue(window._folder_watch_refresh_pending)
        self.assertEqual([450], window._folder_watch_refresh_timer.start_calls)
        self.assertEqual([], window.load_calls)


if __name__ == "__main__":
    unittest.main()
