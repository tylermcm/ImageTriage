"""Starting up and being launched: the start folder, opening a launch target, quick view, startup focus and window-state fixes, and restarting for development. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import os
import subprocess
import sys

from PySide6.QtCore import QModelIndex, QObject, QTimer
from PySide6.QtWidgets import QMessageBox
from pathlib import Path

from . import path_policy
from .formats import IMAGE_SUFFIXES, suffix_for_path
from .models import ImageRecord
from .records_view_controller import _memory_path_key
from .scanner import normalize_filesystem_path

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .window import MainWindow

_logger = logging.getLogger(__name__)


class StartupController(QObject):
    """Starting up and being launched: the start folder, opening a launch target, quick view, startup focus and window-state fixes, and restarting for development. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self._window = window

    def restart_app_for_development(self) -> None:
        if not self._window._preview_ctl.prepare_photocraft_close():
            return
        self._window._preview_ctl.shutdown_photocraft()
        self._window.statusBar().showMessage("Restarting Image Triage...")
        self._window._settings_ctl.remember_current_folder_view_state()
        self._window._settings_ctl.save_window_state()
        self._window._settings.sync()

        # Free everything holding the GPU BEFORE the replacement launches, or the
        # two instances fight over CUDA (the replacement's mask/index work then
        # queues for tens of seconds). Cancelling the index tasks also unblocks
        # shutdown: their QThreadPools waitForDone() on teardown, so a running
        # AuraFace/TinyCLIP pass would otherwise keep this process (and its GPU
        # session) alive for minutes — the "reload leaves a zombie" bug.
        try:
            self._window._records_view.suspend_background_indexing()
        except Exception:
            _logger.exception("Failed to suspend background indexing before dev restart")

        args = [sys.executable, "-m", "image_triage", *sys.argv[1:]]
        cwd = Path(__file__).resolve().parent.parent
        creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            subprocess.Popen(
                args,
                cwd=str(cwd),
                close_fds=True,
                creationflags=creation_flags,
            )
        except OSError as exc:
            QMessageBox.warning(self._window, "Restart Failed", f"Could not restart Image Triage.\n\n{exc}")
            self._window.statusBar().showMessage("Restart failed.")
            return
        # Guarantee this instance actually exits. close() alone is unreliable
        # here — a running background task, the mask worker, or a closeEvent
        # prompt can keep the event loop (and the GPU) alive. State is already
        # saved and the worker shut down, so a hard exit is safe for a dev reload.
        self._window.close()
        os._exit(0)

    def clear_startup_focus(self) -> None:
        focused = self._window.focusWidget()
        if focused is not None and focused is not self._window:
            focused.clearFocus()
        combo = getattr(self._window, "topbar_path_combo", None)
        line_edit = combo.lineEdit() if combo is not None and hasattr(combo, "lineEdit") else None
        if line_edit is not None:
            line_edit.deselect()
        # The content grid claims focus a tick after show; re-run once to clear it
        # so nothing is focused/highlighted on boot.
        if not getattr(self, "_startup_focus_recheck_done", False):
            self._startup_focus_recheck_done = True
            QTimer.singleShot(0, self.clear_startup_focus)

    def apply_startup_window_state_fixup(self) -> None:
        if self._window._startup_window_state == "fullscreen":
            if self._window.isMaximized():
                self._window.showNormal()
            if not self._window.isFullScreen():
                self._window.showFullScreen()
                return
            if os.name == "nt":
                self._window.showNormal()
                self._window.showFullScreen()
            return

        if self._window._startup_window_state != "maximized":
            return

        if self._window.isFullScreen():
            self._window.showNormal()
        # Ask Windows rather than Qt, whose state can say maximized while the
        # frameless window is only stretched. No restore-then-maximize toggle:
        # showMaximized goes through Windows now, and a toggle just redraws the
        # shell at the restored size for a frame.
        if getattr(self._window, "_custom_frame", False):
            try:
                zoomed = bool(ctypes.windll.user32.IsZoomed(int(self._window.winId())))  # type: ignore[attr-defined]
            except (AttributeError, OSError):
                zoomed = self._window.isMaximized()
        else:
            zoomed = self._window.isMaximized()
        if not zoomed:
            self._window.showMaximized()

    def open_launch_target(self, target: str, *, chunked_restore: bool = False) -> bool:
        normalized = self._window._normalize_for_gui(target)
        if not normalized:
            return False
        if self._window._is_slow_source_folder(normalized) or not path_policy.is_plain_local(normalized):
            # The share cannot be asked on the GUI thread, so tell a file from a folder by its name and
            # let the scan worker report a path that is not there.
            is_file = suffix_for_path(normalized) in IMAGE_SUFFIXES
            folder = os.path.normpath(str(Path(normalized).parent)) if is_file else normalized
            self._window._navigation.select_folder(
                folder,
                sync_tree=False,
                chunked_restore=chunked_restore,
                preferred_record_path=normalized if is_file else None,
            )
            self._window.folder_tree.clearSelection()
            self._window.folder_tree.setCurrentIndex(QModelIndex())
            return True
        if os.path.isdir(normalized):
            self._window._navigation.select_folder(normalized, sync_tree=False, chunked_restore=chunked_restore)
            self._window.folder_tree.clearSelection()
            self._window.folder_tree.setCurrentIndex(QModelIndex())
            return True
        if os.path.isfile(normalized):
            folder = normalize_filesystem_path(str(Path(normalized).parent))
            if folder and os.path.isdir(folder):
                self._window._navigation.select_folder(
                    folder,
                    sync_tree=False,
                    chunked_restore=chunked_restore,
                    preferred_record_path=normalized,
                )
                self._window.folder_tree.clearSelection()
                self._window.folder_tree.setCurrentIndex(QModelIndex())
                return True
        self._window.statusBar().showMessage(f"Launch target not found: {normalized}")
        return False

    def record_and_index_for_loaded_path(self, path: str) -> tuple[int, ImageRecord] | None:
        target_key = _memory_path_key(path)
        if not target_key:
            return None
        for index, record in enumerate(self._window._records):
            if record.is_folder:
                continue
            if any(_memory_path_key(candidate) == target_key for candidate in record.stack_paths):
                return index, record
        return None

    def maybe_open_startup_quick_view(self) -> bool:
        target = self._window._pending_quick_view_path
        if not self._window._quick_view_mode or not target or self._window.preview.isVisible():
            return False
        match = self.record_and_index_for_loaded_path(target)
        if match is None:
            return False
        index, record = match
        self._window._quick_view_source_overrides[record.path] = target
        self._window.grid.set_current_index(index)
        self._window._pending_quick_view_path = ""
        self._window._preview_ctl.open_preview(index)
        return True

    def show_main_window_after_quick_view_failure(self) -> None:
        if not self._window._quick_view_mode:
            return
        target = self._window._pending_quick_view_path
        self._window._quick_view_mode = False
        self._window._pending_quick_view_path = ""
        self._window.show()
        self._window.raise_()
        self._window.activateWindow()
        if target:
            self._window.statusBar().showMessage(f"Could not open {Path(target).name} in the quick viewer.")

    def finish_quick_view_attempt_if_ready(self) -> None:
        if (
            self._window._quick_view_mode
            and self._window._pending_quick_view_path
            and not self._window._scan_in_progress
            and not self._window._records_view.records_view_chunk_active()
        ):
            self.show_main_window_after_quick_view_failure()
