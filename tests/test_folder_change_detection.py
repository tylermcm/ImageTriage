"""WI-8.2 / audit P9: noticing changes in a folder that cannot be watched (network / removable drives).

``QFileSystemWatcher`` does not observe shares, and the app deliberately installs no watcher on a
network or removable drive. Two things used to follow: opening such a folder served the saved listing
without looking at the folder at all, and nothing ever refreshed it, so files added elsewhere stayed
invisible until the user pressed Refresh.

Now the folder's own modified time (which changes when entries are added, removed or renamed) is
recorded next to the saved listing, read *before* the listing is taken:

* opening a folder serves the saved listing only while that time still matches; a changed folder, or
  a listing saved before the time was recorded, is rescanned (saved listing shown first, as on
  local drives); an unreachable folder still gets the saved listing;
* returning to the app re-reads the time on a worker thread and queues the usual watcher refresh when
  it differs from the one the on-screen listing was taken at.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QRunnable, QThreadPool, Qt
from PySide6.QtWidgets import QApplication

from image_triage.catalog import CatalogRepository
from image_triage.catalog.schema import CATALOG_MIGRATIONS, LATEST_CATALOG_SCHEMA_VERSION
from image_triage.models import ImageRecord, SortMode
from image_triage.scanner import FolderModifiedCheckTask, FolderScanTask, folder_modified_ns
from tests.harness import make_jpegs, open_folder, pump_until


def _record(folder: Path, name: str) -> ImageRecord:
    return ImageRecord(path=str(folder / name), name=name, size=10, modified_ns=1)


def _bump_folder_mtime(folder: Path, seconds: int = 5) -> int:
    """Move a folder's modified time forward by a clearly different amount (file creation may land
    inside the filesystem's timestamp granularity)."""
    stat = os.stat(folder)
    new_ns = stat.st_mtime_ns + seconds * 1_000_000_000
    os.utime(folder, ns=(stat.st_atime_ns, new_ns))
    return new_ns


# --------------------------------------------------------------------------- catalog column
def test_schema_has_the_folder_mtime_migration_last() -> None:
    assert LATEST_CATALOG_SCHEMA_VERSION == 8
    version, sql = CATALOG_MIGRATIONS[-1]
    assert version == 8 and "dir_mtime_ns" in sql and "ADD COLUMN" in sql  # additive, never a rebuild


def test_a_catalog_from_before_the_column_migrates_in_place_and_keeps_its_listings() -> None:
    with tempfile.TemporaryDirectory(prefix="image_triage_catalog_") as temp_dir:
        db_path = Path(temp_dir) / "catalog.sqlite3"
        folder = Path(temp_dir) / "shots"
        folder.mkdir()
        # Build the database exactly as version 7 left it, with a saved listing in it.
        connection = sqlite3.connect(db_path)
        connection.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        for version, sql in CATALOG_MIGRATIONS:
            if version <= 7:
                connection.executescript(sql)
                connection.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
        connection.commit()
        columns = [row[1] for row in connection.execute("PRAGMA table_info(catalog_folders)")]
        assert "dir_mtime_ns" not in columns
        connection.close()

        repository = CatalogRepository(db_path)
        # Reading migrates (and an old listing has no recorded time).
        assert repository.load_folder_dir_mtime(str(folder)) is None
        assert repository.save_folder_records(str(folder), [_record(folder, "a.jpg")])
        assert [r.name for r in repository.load_folder_records(str(folder))] == ["a.jpg"]
        assert repository.load_folder_dir_mtime(str(folder)) is None

        connection = sqlite3.connect(db_path)
        assert "dir_mtime_ns" in [row[1] for row in connection.execute("PRAGMA table_info(catalog_folders)")]
        assert sorted(row[0] for row in connection.execute("SELECT version FROM schema_migrations")) == list(range(1, 9))
        connection.close()


def test_saving_records_the_time_and_an_edit_without_a_time_keeps_the_old_one() -> None:
    with tempfile.TemporaryDirectory(prefix="image_triage_catalog_") as temp_dir:
        repository = CatalogRepository(Path(temp_dir) / "catalog.sqlite3")
        folder = Path(temp_dir) / "shots"
        folder.mkdir()
        assert repository.load_folder_dir_mtime(str(folder)) is None  # no listing at all

        repository.save_folder_records(str(folder), [_record(folder, "a.jpg")], dir_mtime_ns=111)
        assert repository.load_folder_dir_mtime(str(folder)) == 111
        # The app's own edits to a saved listing (moves, flags) pass no time and must not erase it.
        repository.save_folder_records(str(folder), [], source="window")
        assert repository.load_folder_dir_mtime(str(folder)) == 111
        # A real listing replaces it.
        repository.save_folder_records(str(folder), [_record(folder, "b.jpg")], dir_mtime_ns=222)
        assert repository.load_folder_dir_mtime(str(folder)) == 222


# --------------------------------------------------------------------------- scan task
class _Run:
    """One FolderScanTask run against a temp folder and catalog, collecting what it emitted."""

    def __init__(self, folder: Path, repository: CatalogRepository, *, live_records=None, prefer_cached_only=True) -> None:
        self.cached: list[tuple[list[ImageRecord], str]] = []
        self.finished: list[tuple[list[ImageRecord], str]] = []
        self.scan_calls = 0
        self.task = FolderScanTask(str(folder), token=1, sort_mode=SortMode.NAME, prefer_cached_only=prefer_cached_only)
        self.task.signals.cached.connect(lambda _f, _t, records, source: self.cached.append((list(records), source)))
        self.task.signals.finished.connect(lambda _f, _t, records, source: self.finished.append((list(records), source)))

        def fake_scan(_folder):
            self.scan_calls += 1
            return list(live_records or [])

        with patch("image_triage.scanner.CatalogRepository", return_value=repository), patch(
            "image_triage.scanner.scan_folder", side_effect=fake_scan
        ), patch.dict(os.environ, {"IMAGE_TRIAGE_USE_CATALOG_CACHE": "1"}, clear=False):
            self.task.run()
            QThreadPool.globalInstance().waitForDone(5000)


@pytest.fixture
def shots():
    with tempfile.TemporaryDirectory(prefix="image_triage_shots_") as temp_dir:
        folder = Path(temp_dir) / "shots"
        folder.mkdir()
        yield folder, CatalogRepository(Path(temp_dir) / "catalog.sqlite3")


def test_a_saved_listing_is_served_without_scanning_while_the_folder_is_unchanged(shots) -> None:
    folder, repository = shots
    saved = [_record(folder, "cached_01.jpg")]
    repository.save_folder_records(str(folder), saved, dir_mtime_ns=folder_modified_ns(str(folder)))
    run = _Run(folder, repository)
    assert run.finished == [(saved, "catalog")]
    assert run.scan_calls == 0 and run.cached == []
    assert run.task.dir_mtime_ns == folder_modified_ns(str(folder))


def test_a_changed_folder_shows_the_saved_listing_then_rescans_and_records_the_new_time(shots) -> None:
    folder, repository = shots
    saved = [_record(folder, "cached_01.jpg")]
    repository.save_folder_records(str(folder), saved, dir_mtime_ns=folder_modified_ns(str(folder)))
    new_time = _bump_folder_mtime(folder)
    live = [_record(folder, "cached_01.jpg"), _record(folder, "new_02.jpg")]
    run = _Run(folder, repository, live_records=live)
    assert run.scan_calls == 1
    assert run.cached == [(saved, "catalog")]  # the saved listing still appears first
    assert run.finished == [(live, "live")]
    assert run.task.dir_mtime_ns == new_time
    assert repository.load_folder_dir_mtime(str(folder)) == new_time
    assert [r.name for r in repository.load_folder_records(str(folder))] == ["cached_01.jpg", "new_02.jpg"]


def test_a_listing_saved_before_the_time_was_recorded_is_rescanned_once(shots) -> None:
    folder, repository = shots
    repository.save_folder_records(str(folder), [_record(folder, "cached_01.jpg")])  # no time: unknown
    live = [_record(folder, "cached_01.jpg")]
    first = _Run(folder, repository, live_records=live)
    assert first.scan_calls == 1 and first.finished == [(live, "live")]
    assert repository.load_folder_dir_mtime(str(folder)) == folder_modified_ns(str(folder))
    second = _Run(folder, repository, live_records=live)
    assert second.scan_calls == 0 and second.finished == [(live, "catalog")]  # now vouched for


def test_an_unreachable_folder_still_gets_its_saved_listing(shots) -> None:
    folder, repository = shots
    saved = [_record(folder, "cached_01.jpg")]
    repository.save_folder_records(str(folder), saved, dir_mtime_ns=123)
    with patch("image_triage.scanner.folder_modified_ns", return_value=None):
        run = _Run(folder, repository)
    assert run.finished == [(saved, "catalog")] and run.scan_calls == 0


def test_a_local_style_open_still_scans_and_records_the_time(shots) -> None:
    folder, repository = shots
    live = [_record(folder, "live_01.jpg")]
    run = _Run(folder, repository, live_records=live, prefer_cached_only=False)
    assert run.scan_calls == 1 and run.finished == [(live, "live")]
    assert repository.load_folder_dir_mtime(str(folder)) == folder_modified_ns(str(folder))


def test_folder_modified_ns_reads_the_folder_and_gives_none_when_it_is_missing(tmp_path) -> None:
    assert folder_modified_ns(str(tmp_path)) == os.stat(tmp_path).st_mtime_ns
    assert folder_modified_ns(str(tmp_path / "gone")) is None


def test_the_check_task_reports_the_modified_time_through_its_signal(tmp_path) -> None:
    app = QApplication.instance() or QApplication([])
    task = FolderModifiedCheckTask(str(tmp_path), 42)
    seen: list[tuple[str, int, object]] = []
    task.signals.checked.connect(lambda folder, token, modified: seen.append((folder, token, modified)))
    task.run()
    app.processEvents()
    assert seen == [(str(tmp_path), 42, os.stat(tmp_path).st_mtime_ns)]


# --------------------------------------------------------------------------- window
class _StubCheckTask(QRunnable):
    """Stands in for FolderModifiedCheckTask so the guards can be tested without a worker thread."""

    created: list[tuple[str, int]] = []

    def __init__(self, folder: str, token: int) -> None:
        super().__init__()
        type(self).created.append((folder, token))
        self.signals = SimpleNamespace(checked=SimpleNamespace(connect=lambda *args, **kwargs: None))
        self.setAutoDelete(False)

    def run(self) -> None:  # never reports back
        return None


@pytest.fixture
def window(dialogs):
    from tests.test_preview_lazy_build import _fresh_window

    with _fresh_window() as win:
        try:
            yield win
        finally:
            win._folder_watch_refresh_timer.stop()  # a queued refresh must not fire during disposal


def _ready_for_a_check(win, folder: Path) -> None:
    """A window showing ``folder`` as if it were on a network drive and fully loaded."""
    win._is_slow_source_folder = lambda _folder=None: True
    win._current_folder = str(folder)
    win._scope_kind = "folder"
    win._scan_in_progress = False
    win._watch_current_folder_enabled = True
    win._folder_dir_mtime_ns = 1000
    win._folder_check_task = None
    win._folder_check_token = -1
    win._folder_check_last_started = 0.0


@pytest.mark.parametrize(
    "spoil",
    [
        pytest.param(lambda w: setattr(w, "_watch_current_folder_enabled", False), id="watching-switched-off"),
        pytest.param(lambda w: setattr(w, "_scope_kind", "collection"), id="not-a-folder-scope"),
        pytest.param(lambda w: setattr(w, "_current_folder", ""), id="no-folder"),
        pytest.param(lambda w: setattr(w, "_scan_in_progress", True), id="scan-running"),
        pytest.param(lambda w: setattr(w, "_folder_dir_mtime_ns", None), id="no-baseline-yet"),
        pytest.param(lambda w: setattr(w, "_is_slow_source_folder", lambda _f=None: False), id="local-drive-has-a-watcher"),
    ],
)
def test_activation_checks_nothing_unless_every_condition_holds(window, tmp_path, monkeypatch, spoil) -> None:
    _StubCheckTask.created.clear()
    monkeypatch.setattr("image_triage.scan_controller.FolderModifiedCheckTask", _StubCheckTask)
    _ready_for_a_check(window, tmp_path)
    spoil(window)
    window._scan.check_folder_changed_on_activation()
    assert _StubCheckTask.created == [] and window._folder_check_task is None


def test_activation_starts_one_check_and_debounces_focus_flaps(window, tmp_path, monkeypatch) -> None:
    _StubCheckTask.created.clear()
    monkeypatch.setattr("image_triage.scan_controller.FolderModifiedCheckTask", _StubCheckTask)
    _ready_for_a_check(window, tmp_path)
    window._scan.check_folder_changed_on_activation()
    assert _StubCheckTask.created == [(str(tmp_path), window._scan_token)]
    window._scan.check_folder_changed_on_activation()  # in flight for this scan: nothing new
    assert len(_StubCheckTask.created) == 1
    window._folder_check_task = None  # it came back...
    window._scan.check_folder_changed_on_activation()  # ...but five seconds have not passed
    assert len(_StubCheckTask.created) == 1
    window._folder_check_last_started -= 6.0
    window._scan.check_folder_changed_on_activation()
    assert len(_StubCheckTask.created) == 2


def test_a_different_modified_time_queues_the_watcher_refresh(window, tmp_path) -> None:
    _ready_for_a_check(window, tmp_path)
    window._folder_watch_refresh_pending = False
    window._folder_check_token = window._scan_token
    window._scan.handle_folder_modified_checked(str(tmp_path), window._scan_token, 2000)
    assert window._folder_watch_refresh_pending is True
    assert window._folder_check_task is None


@pytest.mark.parametrize(
    "case",
    ["same-time", "unreachable", "stale-scan", "scan-running", "other-folder"],
)
def test_no_refresh_when_nothing_changed_or_the_answer_is_stale(window, tmp_path, case) -> None:
    _ready_for_a_check(window, tmp_path)
    window._folder_watch_refresh_pending = False
    folder, token, modified = str(tmp_path), window._scan_token, 2000
    if case == "same-time":
        modified = 1000
    elif case == "unreachable":
        modified = None
    elif case == "stale-scan":
        token = window._scan_token - 1
    elif case == "scan-running":
        window._scan_in_progress = True
    elif case == "other-folder":
        folder = str(tmp_path / "elsewhere")
    window._scan.handle_folder_modified_checked(folder, token, modified)
    assert window._folder_watch_refresh_pending is False


def test_an_old_answer_does_not_clear_the_marker_of_a_newer_check(window, tmp_path) -> None:
    _ready_for_a_check(window, tmp_path)
    marker = object()
    window._folder_check_task = marker
    window._folder_check_token = window._scan_token
    window._scan.handle_folder_modified_checked(str(tmp_path), window._scan_token - 1, 2000)
    assert window._folder_check_task is marker


def test_returning_to_the_app_runs_the_check_and_other_state_changes_do_not(window, monkeypatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(window._scan, "check_folder_changed_on_activation", lambda: calls.append(1))
    window._scan.handle_application_state_changed(Qt.ApplicationState.ApplicationInactive)
    window._scan.handle_application_state_changed(Qt.ApplicationState.ApplicationSuspended)
    assert calls == []
    window._scan.handle_application_state_changed(Qt.ApplicationState.ApplicationActive)
    assert calls == [1]
    # and the signal really is wired to it
    QApplication.instance().applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
    assert calls == [1, 1]


def test_end_to_end_new_files_on_a_network_style_folder_appear_when_the_user_returns(window, tmp_path) -> None:
    folder = tmp_path / "nas_shoot"
    make_jpegs(folder, [f"IMG_{index}.jpg" for index in range(3)])
    window._is_slow_source_folder = lambda _folder=None: True  # behave as a network drive
    open_folder(window, folder, 3)
    baseline = window._folder_dir_mtime_ns
    assert baseline == folder_modified_ns(str(folder)), "the finished scan records the time it was taken at"

    # Nothing changed: coming back to the app does not refresh.
    window._folder_check_last_started = 0.0
    window._scan.check_folder_changed_on_activation()
    assert pump_until(lambda: window._folder_check_task is None, timeout=10)
    assert window._folder_watch_refresh_pending is False

    # Something else drops a file into the folder.
    make_jpegs(folder, ["IMG_new.jpg"])
    new_time = _bump_folder_mtime(folder)
    window._folder_check_last_started = 0.0
    window._scan.check_folder_changed_on_activation()
    assert window._folder_check_task is not None, "a worker-thread check was started"
    assert pump_until(lambda: len(window._records) == 4, timeout=30), "the new photo never appeared"
    assert window._folder_dir_mtime_ns == new_time or window._folder_dir_mtime_ns == folder_modified_ns(str(folder))
    assert window._folder_check_task is None
