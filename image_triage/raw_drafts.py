"""Unsaved RAW drafts on disk, so they survive a crash or a restart.

A draft is what the in-app RAW editor holds for a photo between the first slider change and Done. It is kept in the
application's data folder (never beside the photo: the card may be read-only, and a draft is not an edit other tools
should see), one small file per photo, written off the interface thread with only the newest state of each photo.

A stored draft is tied to the photo's size and date and to the saved recipe it was started from. If either has
changed when the photo is opened again, the draft is not applied (it would silently undo a newer saved edit).
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from pathlib import Path

VERSION = 1
MAX_BYTES = 64 * 1024 * 1024  # sixteen full-size raster masks stay far below this; anything larger is not a draft


def default_root() -> Path:
    from .scan_cache import app_data_root

    return app_data_root() / "raw_drafts"


def _key(path: str) -> str:
    return hashlib.sha256(os.path.normcase(os.path.abspath(path)).encode("utf-8", "surrogatepass")).hexdigest()[:32]


class DraftStore:
    """``put`` and ``discard`` return at once; a single writer thread does the file work, newest state per photo.
    ``signature`` is how the photo's identity is read (``photocraft_bridge.file_signature``)."""

    def __init__(self, root: Path | None = None, *, signature=None):
        self._root = root
        self._signature = signature
        self._lock = threading.Condition()
        self._pending: dict[str, tuple | None] = {}  # path -> (original, draft), or None to remove the file
        self._thread: threading.Thread | None = None
        self._busy = False
        self._closed = False
        self.last_error = ""

    def root(self) -> Path:
        if self._root is None:
            self._root = default_root()
        return self._root

    def file_for(self, path: str) -> Path:
        return self.root() / (_key(path) + ".json")

    def _identity(self, path: str):
        if self._signature is None:
            from .photocraft_bridge import file_signature

            self._signature = file_signature
        return self._signature(path)

    def put(self, path: str, original: dict, draft: dict) -> None:
        self._queue(path, (original, draft))

    def discard(self, path: str) -> None:
        self._queue(path, None)

    def _queue(self, path, state):
        with self._lock:
            if self._closed:
                return
            self._pending[path] = state
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="raw-draft-writer", daemon=True)
                self._thread.start()
            self._lock.notify_all()

    def _run(self):
        while True:
            with self._lock:
                while not self._pending and not self._closed:
                    self._lock.wait()
                if not self._pending:
                    return
                path, state = next(iter(self._pending.items()))
                del self._pending[path]
                self._busy = True
            try:
                self._write(path, state)
                self.last_error = ""
            except (OSError, ValueError, TypeError) as error:
                # A draft that cannot be kept must not interrupt editing; the quit prompt still protects it.
                self.last_error = str(error) or type(error).__name__
            finally:
                with self._lock:
                    self._busy = False
                    self._lock.notify_all()

    def _write(self, path, state):
        target = self.file_for(path)
        if state is None:
            target.unlink(missing_ok=True)
            return
        original, draft = state
        data = json.dumps({"version": VERSION, "path": path, "signature": self._identity(path),
                           "original": original, "draft": draft}, ensure_ascii=False).encode("utf-8")
        if len(data) > MAX_BYTES:
            raise ValueError("The draft is too large to keep")
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, scratch = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(handle, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(scratch, target)
        except BaseException:
            Path(scratch).unlink(missing_ok=True)
            raise

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait until everything queued is on disk. False if that took longer than ``timeout``."""
        with self._lock:
            return self._lock.wait_for(lambda: not self._pending and not self._busy, timeout)

    def close(self, timeout: float = 5.0) -> bool:
        done = self.flush(timeout)
        with self._lock:
            self._closed = True
            self._lock.notify_all()
        return done

    def read(self, path: str, saved: dict):
        """The draft to restore for ``path`` whose saved recipe settings are ``saved``.

        Returns ``(draft, "")`` to restore, ``(None, "")`` when there is nothing, and ``(None, reason)`` when a draft
        exists but no longer belongs to this photo or this saved edit; the file is kept in that case until the photo is
        saved or cancelled, so nothing is destroyed by merely opening it.
        """
        target = self.file_for(path)
        try:
            if target.stat().st_size > MAX_BYTES:
                return None, "Unsaved edits from an earlier session could not be read."
            data = json.loads(target.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None, ""
        except (OSError, ValueError):
            return None, "Unsaved edits from an earlier session could not be read."
        if (not isinstance(data, dict) or data.get("version") != VERSION or not isinstance(data.get("draft"), dict)
                or not isinstance(data.get("original"), dict) or data.get("path") != path):
            return None, "Unsaved edits from an earlier session could not be read."
        if data.get("signature") != self._identity(path):
            return None, "The photo changed since its unsaved edits were made, so they were not restored."
        if data["original"] != saved:
            return None, "The saved edit changed since these unsaved edits were made, so they were not restored."
        if data["draft"] == saved:
            return None, ""
        return data["draft"], ""

    def paths(self) -> list[str]:
        """The photos that have a stored draft (for the quit prompt and for tests)."""
        found = []
        try:
            entries = sorted(self.root().glob("*.json"))
        except OSError:
            return found
        for entry in entries:
            try:
                data = json.loads(entry.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and isinstance(data.get("path"), str):
                found.append(data["path"])
        return found
