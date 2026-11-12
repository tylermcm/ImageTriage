"""Debounced photo search; results never dispatch a command."""
from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal, Slot

from .. import path_policy

_NO_INDEX = "Searching filenames. Run analysis with Semantic search on to search photo contents."


class SearchModel(QObject):
    _located = Signal(int, str, object)             # the folder's index was looked for off the interface thread: (token, folder, its path or the error)

    def __init__(self, photos, parent=None, *, task_factory=None, runtime_loader=None, db_path=None):
        super().__init__(parent)
        self.photos = photos
        self._task_factory = task_factory
        self._runtime_loader = runtime_loader
        self._db_path = db_path
        self._token = 0
        self._task = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._start)
        self._located.connect(self._launch)
        photos.searchRequested.connect(self._edited)
        photos.folderChanged.connect(self.refresh)

    def _cancel(self):
        self._token += 1
        self._timer.stop()
        if self._task is not None:
            self._task.cancel()
            self._task = None

    @Slot(str)
    def _edited(self, text):
        self._cancel()
        if text.strip():
            # Only the status line: the grid was already filtered by name when the text changed, and filtering it a second time here
            # (with nothing new to show) reset the whole list twice per keystroke.
            self.photos.set_search_status("Searching photos…")
            self._timer.start()

    @Slot()
    def refresh(self):
        self._edited(self.photos.searchText)

    def _locate(self, folder):
        """The folder's search index, None when it has none, or the error. It resolves the folder and asks whether a file exists."""
        from ..aiculler_workflow import aiculler_db_path, build_aiculler_workflow_paths
        try:
            db = self._db_path(folder) if self._db_path else aiculler_db_path(build_aiculler_workflow_paths(folder))
            return db if db.exists() else None
        except Exception as error:
            return error

    def _start(self):
        folder = self.photos.folder_dir()
        if not folder or not self.photos.searchText.strip():
            return
        token = self._token
        if path_policy.is_plain_local(folder):
            self._launch(token, folder, self._locate(folder))        # a local disk answers at once
        else:
            # A share may take seconds to answer (or twenty to say it is asleep): it is asked on a thread of its own.
            threading.Thread(target=lambda: self._located.emit(token, folder, self._locate(folder)), name="qml-search-index", daemon=True).start()

    @Slot(int, str, object)
    def _launch(self, token, folder, db):
        from ..semantic_runtime import default_search_runtime
        from ..unified_search_task import UnifiedSearchTask
        if token != self._token or folder != self.photos.folder_dir():
            return
        if isinstance(db, Exception):
            self._failed(folder, token, str(db))
            return
        try:
            if db is None:
                self.photos.search_results((), _NO_INDEX)
                return
            runtime = (self._runtime_loader or default_search_runtime)()
            count = sum(1 + len(row.get("companions", ())) for row in self.photos.all_rows())
            task = (self._task_factory or UnifiedSearchTask)(folder=folder, token=self._token,
                db_path=db, runtime=runtime, query_text=self.photos.searchText,
                min_confidence=0, limit=max(1, count))
            task.signals.finished.connect(self._finished)
            task.signals.failed.connect(self._failed)
            self._task = task
            QThreadPool.globalInstance().start(task)
        except Exception as error:
            self._failed(folder, self._token, str(error))

    @Slot(str, int, object)
    def _finished(self, folder, token, result):
        if token != self._token or folder != self.photos.folder_dir():
            return
        self._task = None
        status = _NO_INDEX if result.get("indexed_count") == 0 else "Photo contents and filenames"
        self.photos.search_results(result.get("path_keys", ()), status)

    @Slot(str, int, str)
    def _failed(self, folder, token, message):
        if token != self._token or folder != self.photos.folder_dir():
            return
        self._task = None
        self.photos.search_results((), "Searching filenames. Photo-content search is unavailable: " + message)

    @Slot()
    def shutdown(self):
        self._cancel()
