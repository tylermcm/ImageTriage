"""Debounced photo search; results never dispatch a command."""
from __future__ import annotations

from PySide6.QtCore import QObject, QThreadPool, QTimer, Slot


class SearchModel(QObject):
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
            self.photos.search_results((), "Searching photos…")
            self._timer.start()

    @Slot()
    def refresh(self):
        self._edited(self.photos.searchText)

    def _start(self):
        from ..aiculler_workflow import aiculler_db_path, build_aiculler_workflow_paths
        from ..semantic_runtime import default_search_runtime
        from ..unified_search_task import UnifiedSearchTask
        folder = self.photos.folder_dir()
        if not folder or not self.photos.searchText.strip():
            return
        try:
            db = self._db_path(folder) if self._db_path else aiculler_db_path(build_aiculler_workflow_paths(folder))
            if not db.exists():
                self.photos.search_results((), "Searching filenames. Run analysis with Semantic search on to search photo contents.")
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
        status = ("Searching filenames. Run analysis with Semantic search on to search photo contents."
                  if result.get("indexed_count") == 0 else "Photo contents and filenames")
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
