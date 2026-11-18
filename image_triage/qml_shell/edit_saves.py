"""RAW edits saved in the background, like a sidecar: the original is never touched, and the edit is kept in the hidden edits folder.

Leaving a photo, closing the viewer or pressing Done hands the photo's edit here and the editor carries on at once. Saving means
developing the photo with its settings and publishing the settings with that picture (the grid and the viewer show it), which takes
seconds, so it runs on one worker thread, one photo after another, oldest first. Until it lands, the photo's draft stays in the
draft store, so a crash or a quit loses nothing (it comes back the next time the photo is edited).

A photo handed over again before its first save landed is saved on top of it: each save is told what the one before it will have
written. An export waits for the saves of its photos (``wait_for``), so a file written with the edits always has the latest ones.
"""
from __future__ import annotations

import queue
import threading
from copy import deepcopy
from os.path import abspath, normcase

from PySide6.QtCore import QObject, Signal


def _key(path: str) -> str:
    return normcase(abspath(path))


class EditSaves(QObject):
    landed = Signal(str, object, object, str)        # path, the draft saved, the published render (or None), error

    def __init__(self, save, parent=None):
        """``save(path, original, draft)`` publishes one edit and returns the render; slow, and run on the worker thread."""
        super().__init__(parent)
        self._save = save
        self._jobs: queue.Queue = queue.Queue()
        self._lock = threading.Condition()
        self._pending: dict[str, int] = {}             # path key -> saves queued or running
        self._latest: dict[str, dict] = {}             # path key -> the draft its newest queued save writes
        self._saved: dict[str, dict] = {}              # path key -> the last draft this app saved (the recipe now on disk)
        threading.Thread(target=self._run, name="raw-edit-saves", daemon=True).start()

    def submit(self, path: str, original: dict, draft: dict) -> bool:
        """Save ``draft`` for ``path``, whose saved recipe is ``original`` (or what a save still queued for it will have written)."""
        key = _key(path)
        with self._lock:
            base = self._latest.get(key) if self._pending.get(key) else self._saved.get(key, original)
            base = original if base is None else base
            if draft == base:
                return False                           # nothing new to save
            self._pending[key] = self._pending.get(key, 0) + 1
            self._latest[key] = deepcopy(draft)
        self._jobs.put((path, deepcopy(base), deepcopy(draft)))
        return True

    def pending(self, path: str) -> bool:
        with self._lock:
            return bool(self._pending.get(_key(path)))

    def latest(self, path: str):
        """The draft the photo's saves will leave on disk, once they land (None when none is queued)."""
        with self._lock:
            key = _key(path)
            return deepcopy(self._latest[key]) if self._pending.get(key) else None

    def saved(self, path: str):
        """The draft this app last saved for ``path`` (None when none was)."""
        with self._lock:
            found = self._saved.get(_key(path))
            return deepcopy(found) if found is not None else None

    def wait_for(self, paths, timeout: float | None = None, cancelled=None) -> bool:
        """Block until no save of ``paths`` is queued or running (an export calls this from its worker). False on timeout or cancel."""
        keys = {_key(p) for p in paths}
        import time

        end = None if timeout is None else time.monotonic() + timeout
        with self._lock:
            while any(self._pending.get(k) for k in keys):
                if cancelled is not None and cancelled():
                    return False
                left = 0.25 if end is None else min(0.25, end - time.monotonic())
                if left <= 0:
                    return False
                self._lock.wait(left)
        return True

    def _run(self):
        while True:
            path, original, draft = self._jobs.get()
            result, error = None, ""
            try:
                result = self._save(path, original, draft)
            except Exception as exc:
                error = str(exc) or "The edit could not be saved."
            key = _key(path)
            with self._lock:
                left = self._pending.get(key, 1) - 1
                if left > 0:
                    self._pending[key] = left
                else:
                    self._pending.pop(key, None)
                    self._latest.pop(key, None)
                if not error:
                    self._saved[key] = deepcopy(draft)
                self._lock.notify_all()
            try:
                self.landed.emit(path, draft, result, error)
            except RuntimeError:
                pass                                   # the application is closing


_instance: EditSaves | None = None


def instance() -> EditSaves:
    """The application's one queue (the editor hands edits to it, exports wait on it)."""
    global _instance
    if _instance is None:
        from .raw_editor import RawEditor

        _instance = EditSaves(lambda path, original, draft: RawEditor._work("save", path, original, draft, ""))
    return _instance


def wait_for(paths, timeout: float | None = None, cancelled=None) -> bool:
    """Wait for the background saves of ``paths``; nothing to wait for when the queue was never started."""
    return True if _instance is None else _instance.wait_for(paths, timeout, cancelled)
