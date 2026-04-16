"""Helpers for syncing standalone child windows with the Image Triage host."""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QTimer


_PARENT_PID_ENV = "IMAGE_TRIAGE_PARENT_PID"
_SYNC_FILE_ENV = "IMAGE_TRIAGE_SYNC_FILE"
_STILL_ACTIVE = 259


class HostSyncController(QObject):
    """Poll a small host-state file so standalone tools stay in sync with the parent app."""

    def __init__(
        self,
        *,
        on_appearance_mode_changed: Callable[[str], None],
        on_shutdown_requested: Callable[[], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_appearance_mode_changed = on_appearance_mode_changed
        self._on_shutdown_requested = on_shutdown_requested
        self._sync_path = self._resolve_sync_path()
        self._parent_pid = self._resolve_parent_pid()
        self._last_sync_mtime_ns = -1
        self._last_mode = ""
        self._shutdown_requested = False

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(750)
        self._poll_timer.timeout.connect(self._poll_host_state)
        self._poll_timer.start()
        self._poll_host_state()

    def _poll_host_state(self) -> None:
        """Check for parent shutdown and host appearance changes."""

        if self._shutdown_requested:
            return

        state = self._read_sync_state()
        if state is not None:
            if bool(state.get("shutdown_requested")):
                self._request_shutdown()
                return

            appearance_mode = str(state.get("appearance_mode", "")).strip().lower()
            if appearance_mode and appearance_mode != self._last_mode:
                self._last_mode = appearance_mode
                self._on_appearance_mode_changed(appearance_mode)

        if self._parent_pid is not None and not _process_exists(self._parent_pid):
            self._request_shutdown()

    def _request_shutdown(self) -> None:
        """Ask the child window to close once and stop polling."""

        if self._shutdown_requested:
            return
        self._shutdown_requested = True
        self._poll_timer.stop()
        self._on_shutdown_requested()

    def _read_sync_state(self) -> dict[str, object] | None:
        """Read the sync file only when it changes on disk."""

        if self._sync_path is None or not self._sync_path.exists():
            return None

        try:
            stat = self._sync_path.stat()
        except OSError:
            return None

        if stat.st_mtime_ns == self._last_sync_mtime_ns:
            return None

        self._last_sync_mtime_ns = stat.st_mtime_ns
        try:
            return json.loads(self._sync_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None

    def _resolve_sync_path(self) -> Path | None:
        raw = os.environ.get(_SYNC_FILE_ENV, "").strip()
        if not raw:
            return None
        return Path(raw).expanduser().resolve()

    def _resolve_parent_pid(self) -> int | None:
        raw = os.environ.get(_PARENT_PID_ENV, "").strip()
        if not raw:
            return None
        try:
            pid = int(raw)
        except ValueError:
            return None
        return pid if pid > 0 else None


def _process_exists(pid: int) -> bool:
    """Return whether a process ID is still alive."""

    if pid <= 0:
        return False

    if os.name == "nt":
        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return int(exit_code.value) == _STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)

    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
