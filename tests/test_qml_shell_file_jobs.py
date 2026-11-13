"""Delete, move and copy run on a worker: the window is not held, progress shows, Cancel stops between photos, failures are named,
one operation at a time, and the undo step holds exactly what moved."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QSettings, QStandardPaths
from PySide6.QtGui import QGuiApplication

from image_triage.qml_shell import decision_bridge as bridge_module
from image_triage.qml_shell import file_actions as fa
from image_triage.qml_shell.decision_bridge import DecisionBridge
from image_triage.qml_shell.file_actions import FileActions
from image_triage.qml_shell.file_jobs import FileJobs
from image_triage.qml_shell.menu_model import ToastModel
from image_triage.qml_shell.photos import PhotoModel
from image_triage.qml_shell.transfer_model import TransferModel


@pytest.fixture(scope="module")
def qapp():
    return QCoreApplication.instance() or QGuiApplication([])


def wait_for(app, condition, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.005)
    return False


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    QStandardPaths.setTestModeEnabled(True)
    monkeypatch.setenv("IMAGE_TRIAGE_APPDATA", str(tmp_path / "appdata"))
    session = "qml-jobs-" + tmp_path.name
    monkeypatch.setattr(bridge_module, "session_id", lambda: session)
    monkeypatch.setattr(fa, "session_id", lambda: session)
    monkeypatch.setattr(FileActions, "_delete_mode", lambda self: fa.SAFE_TRASH)
    from image_triage.safe_trash import SafeTrash

    monkeypatch.setattr(SafeTrash, "recycle_root_for_folder", lambda self, folder=None: tmp_path / "trash")
    monkeypatch.setattr(SafeTrash, "is_temporary_storage_folder", lambda self, folder=None: False)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(FileActions, "_settings", lambda self: settings)
    yield
    QStandardPaths.setTestModeEnabled(False)


@pytest.fixture
def world(qapp, tmp_path):
    shoot = tmp_path / "shoot"
    shoot.mkdir()
    for name in ("a", "b", "c"):
        (shoot / f"{name}.jpg").write_bytes(b"\xff\xd8\xff\xd9" + name.encode())
    (shoot / "a.nef").write_bytes(b"raw-a")                       # a's RAW companion
    photos = PhotoModel(parent=qapp)
    toasts = ToastModel(parent=qapp)
    bridge = DecisionBridge(photos, toasts=toasts, parent=qapp)
    jobs = FileJobs(parent=qapp, show_after_ms=30)
    files = FileActions(photos, toasts, parent=qapp, jobs=jobs)
    transfers = TransferModel(photos, memory=True, parent=qapp, toasts=toasts)
    transfers.follow(jobs)
    photos.loadFolder(str(shoot))
    assert wait_for(qapp, lambda: not photos.loading and photos.total == 3)
    world = SimpleNamespace(shoot=shoot, photos=photos, toasts=toasts, bridge=bridge, files=files, jobs=jobs, transfers=transfers, tmp=tmp_path)
    yield world
    files.shutdown()


def rows(photos, *stems):
    return [next(i for i in range(photos.shown) if photos.get(i)["name"].startswith(stem)) for stem in stems]


def titles(toasts):
    return [item["title"] for item in toasts.items]


def test_copy_surfaces_destination_name_warning_off_thread(world, qapp, monkeypatch):
    from image_triage import file_ops
    threads = []
    def warning(name, directory):
        threads.append(threading.current_thread())
        return "This name cannot be opened on Windows."
    monkeypatch.setattr(file_ops, "name_warning", warning)
    target = world.tmp / "delivery"
    target.mkdir()
    world.files.copyRows(rows(world.photos, "a"), str(target))
    assert wait_for(qapp, lambda: not world.files.busy)
    assert threads and all(t is not threading.main_thread() for t in threads)
    assert "Some names may not work on Windows" in titles(world.toasts)
    assert (target / "a.nef").exists()


class Gate:
    """Stands in for `move_paths`: says which thread it ran on and holds each photo until the test lets it through."""

    def __init__(self, monkeypatch, name="move_paths", fail=()):
        self.real = getattr(fa, name)
        self.threads, self.seen, self.fail = [], [], tuple(fail)
        self.entered, self.release = threading.Semaphore(0), threading.Semaphore(0)
        monkeypatch.setattr(fa, name, self)

    def __call__(self, paths, destination):
        self.threads.append(threading.current_thread())
        self.seen.append(sorted(p.replace("\\", "/").rsplit("/", 1)[-1] for p in paths))
        self.entered.release()
        assert self.release.acquire(timeout=10)
        if any(name in self.fail for name in self.seen[-1]):
            raise OSError("disk is full")
        return self.real(paths, destination)

    def let(self, count=1):
        for _ in range(count):
            self.release.release()


def test_the_files_move_on_a_worker_and_the_slot_returns_at_once(qapp, world, monkeypatch):
    w, dest = world, world.tmp / "album"
    gate = Gate(monkeypatch)
    w.files.moveRows(rows(w.photos, "a", "b", "c"), str(dest))
    # the slot is back and nothing has been applied: the photos are still listed, still on disk, and no undo step exists yet
    assert w.files.busy and w.photos.shown == 3 and not w.photos.canUndo
    assert gate.entered.acquire(timeout=10)
    assert (w.shoot / "a.jpg").exists() and (w.shoot / "b.jpg").exists()
    gate.let(3)
    assert w.files.waitForIdle() and not w.files.busy
    assert gate.threads and all(thread is not threading.main_thread() for thread in gate.threads)
    assert w.photos.shown == 0 and {p.name for p in dest.iterdir()} >= {"a.jpg", "a.nef", "b.jpg", "c.jpg"}
    assert titles(w.toasts)[-1] == "Moved 3 photos"


def test_progress_shows_in_the_transfer_card_with_done_total_and_the_current_name(qapp, world, monkeypatch):
    w = world
    gate = Gate(monkeypatch)
    w.files.moveRows(rows(w.photos, "a", "b", "c"), str(w.tmp / "album"))
    assert gate.entered.acquire(timeout=10)
    gate.let(1)
    assert gate.entered.acquire(timeout=10)                       # the first photo is done and the second is in hand
    assert wait_for(qapp, lambda: w.transfers.active and w.jobs.view()["done"] == 1)
    card = w.transfers.card
    assert card["state"] == "progress" and card["headline"] == "Moving 3 photos" and card["pct"] == 33
    assert card["name"] == "b.jpg" and card["left"] == "2" and card["canCancel"] is True
    assert card["route"].endswith("album") and str(w.shoot) in card["route"]
    assert w.jobs.progress["total"] == 3 and w.jobs.progress["done"] == 1
    gate.let(2)
    assert w.files.waitForIdle()
    assert not w.transfers.active and w.transfers.card["state"] == ""


def test_a_small_operation_never_shows_the_card(qapp, tmp_path):
    shoot = tmp_path / "shoot"
    shoot.mkdir()
    (shoot / "a.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    photos, toasts = PhotoModel(parent=qapp), ToastModel(parent=qapp)
    jobs = FileJobs(parent=qapp)                                  # the real delay
    files = FileActions(photos, toasts, parent=qapp, jobs=jobs)
    transfers = TransferModel(photos, memory=True, parent=qapp, toasts=toasts)
    transfers.follow(jobs)
    photos.loadFolder(str(shoot))
    assert wait_for(qapp, lambda: not photos.loading and photos.total == 1)
    shown = []
    transfers.cardChanged.connect(lambda: shown.append(transfers.active))
    files.deleteRow(0)
    assert files.waitForIdle()
    wait_for(qapp, lambda: False, 0.4)                            # past the delay: a late timer must not show it either
    assert photos.shown == 0 and not any(shown)


def test_cancel_stops_between_photos_and_undo_holds_exactly_what_moved(qapp, world, monkeypatch):
    w, dest = world, world.tmp / "album"
    gate = Gate(monkeypatch)
    w.files.moveRows(rows(w.photos, "a", "b", "c"), str(dest))
    assert gate.entered.acquire(timeout=10)                       # the first photo (a.nef + a.jpg) is in hand
    w.transfers.cancel() if w.transfers.active else w.files.cancel()
    assert w.jobs.view()["cancelling"]
    gate.let(1)                                                   # it is finished, not abandoned
    assert w.files.waitForIdle()
    assert gate.seen == [["a.jpg", "a.nef"]]                      # the others were never started
    assert (dest / "a.nef").exists() and (dest / "a.jpg").exists() and not (w.shoot / "a.nef").exists() and not (w.shoot / "a.jpg").exists()
    assert (w.shoot / "b.jpg").exists() and (w.shoot / "c.jpg").exists() and w.photos.shown == 2
    assert "Stopped after moving 1 of 3" in titles(w.toasts)
    assert w.photos.undo() == "move of a.nef"                     # one step, with the one photo that moved
    assert (w.shoot / "a.nef").exists() and (w.shoot / "a.jpg").exists() and not list(dest.iterdir())
    assert not w.photos.canUndo


def test_a_second_request_while_one_runs_is_refused_and_says_so(qapp, world, monkeypatch):
    w, dest = world, world.tmp / "album"
    gate = Gate(monkeypatch)
    w.files.moveRows(rows(w.photos, "b"), str(dest))
    assert gate.entered.acquire(timeout=10)
    w.files.deleteRows(rows(w.photos, "c"))
    assert titles(w.toasts)[-1] == "Another file operation is still running" and w.toasts.items[-1]["kind"] == "warn"
    assert w.files.trashPaths([w.photos.get(rows(w.photos, "c")[0])["path"]], "") == 0
    gate.let(1)
    assert w.files.waitForIdle()
    assert (dest / "b.jpg").exists() and (w.shoot / "c.jpg").exists() and not (w.tmp / "trash").exists()
    assert w.photos.shown == 2


def test_a_photo_that_fails_is_named_at_the_end_and_the_rest_still_go(qapp, world, monkeypatch):
    w, dest = world, world.tmp / "album"
    gate = Gate(monkeypatch, fail=("b.jpg",))
    gate.let(3)
    w.files.moveRows(rows(w.photos, "a", "b", "c"), str(dest))
    assert w.files.waitForIdle()
    assert (dest / "a.nef").exists() and (dest / "c.jpg").exists() and (w.shoot / "b.jpg").exists()
    assert [w.photos.get(i)["name"] for i in range(w.photos.shown)] == ["b.jpg"]
    error = next(item for item in w.toasts.items if item["kind"] == "error")
    assert error["title"] == "1 of 3 photos could not be moved" and "b.jpg" in error["body"] and "disk is full" in error["body"]
    assert titles(w.toasts)[-1] == "Moved 2 photos"
    assert w.photos.undo() == "move of 2 photos"
    assert (w.shoot / "a.nef").exists() and (w.shoot / "a.jpg").exists() and (w.shoot / "c.jpg").exists() and not list(dest.iterdir())


def test_quitting_while_it_runs_finishes_the_photo_in_hand_and_carries_its_decision(qapp, world, monkeypatch):
    w, dest = world, world.tmp / "album"
    w.photos.setRating(rows(w.photos, "a")[0], 5)
    w.bridge.flush()
    gate = Gate(monkeypatch)
    w.files.moveRows(rows(w.photos, "a", "b", "c"), str(dest))
    assert gate.entered.acquire(timeout=10)
    threading.Timer(0.2, gate.let, args=(3,)).start()             # the photo in hand takes a moment longer than the quit
    w.files.shutdown()                                            # what `aboutToQuit` calls
    assert not w.files.busy and len(gate.seen) == 1 and gate.seen[0][:2] == ["a.jpg", "a.nef"]     # only the photo in hand
    assert (dest / "a.nef").exists() and (dest / "a.jpg").exists() and (w.shoot / "b.jpg").exists() and (w.shoot / "c.jpg").exists()
    other = PhotoModel(parent=qapp)
    DecisionBridge(other, parent=qapp)
    other.loadFolder(str(dest))
    assert wait_for(qapp, lambda: not other.loading and other.total == 1)
    assert other.get(0)["rating"] == 5                            # handed over before the quit went on


def test_one_photo_failing_in_the_safe_trash_is_put_back_whole_and_the_others_go(qapp, world, monkeypatch):
    w = world
    real = fa.shutil.move

    def flaky(source, target):
        if str(source).endswith("a.jpg") and "trash" in str(target):
            raise OSError("share went away")
        return real(source, target)

    monkeypatch.setattr(fa.shutil, "move", flaky)
    w.files.deleteRows(rows(w.photos, "a", "b", "c"))
    assert w.files.waitForIdle()
    monkeypatch.setattr(fa.shutil, "move", real)
    assert (w.shoot / "a.nef").exists() and (w.shoot / "a.jpg").exists()          # together, where they were
    trash = {p.name for p in (w.tmp / "trash").iterdir() if not p.name.startswith(".")}
    assert trash == {"b.jpg", "c.jpg"} and w.photos.shown == 1
    error = next(item for item in w.toasts.items if item["kind"] == "error")
    assert error["title"] == "1 photo could not be moved to the recycle bin" and "share went away" in error["body"]
    assert w.photos.undo() == "move of 2 photos to the recycle bin"
    assert (w.shoot / "b.jpg").exists() and (w.shoot / "c.jpg").exists()


def test_an_undo_that_only_partly_works_keeps_the_rest_for_another_try(qapp, world):
    w = world
    w.files.deleteRows(rows(w.photos, "b", "c"))
    assert w.files.waitForIdle()
    held = (w.tmp / "held.jpg")
    (w.tmp / "trash" / "c.jpg").rename(held)                      # one of the two cannot come back for now
    failed = []
    w.photos.undoFailed.connect(failed.append)
    assert w.photos.undo() == ""
    assert failed == ["move of 2 photos to the recycle bin"] and w.photos.canUndo
    assert (w.shoot / "b.jpg").exists() and not (w.shoot / "c.jpg").exists()      # the one that could, did
    assert w.toasts.items[-1]["title"] == "Could not bring them back" and "c.jpg" in w.toasts.items[-1]["body"]
    held.rename(w.tmp / "trash" / "c.jpg")
    assert w.photos.undo() == "move of 2 photos to the recycle bin"
    assert (w.shoot / "c.jpg").exists() and not w.photos.canUndo


def test_restoring_a_bundle_is_all_or_nothing(tmp_path, monkeypatch):
    from image_triage.file_ops import FileMove

    home, away = tmp_path / "home", tmp_path / "away"
    home.mkdir(); away.mkdir()
    for name in ("a.nef", "a.jpg"):
        (away / name).write_bytes(name.encode())
    moves = [FileMove(str(home / "a.nef"), str(away / "a.nef")), FileMove(str(home / "a.jpg"), str(away / "a.jpg"))]
    real = fa.shutil.move

    def flaky(source, target):
        if str(target).endswith("a.jpg") and "home" in str(target):
            raise OSError("no room")
        return real(source, target)

    monkeypatch.setattr(fa.shutil, "move", flaky)
    with pytest.raises(OSError):
        fa._restore(moves)
    assert sorted(p.name for p in away.iterdir()) == ["a.jpg", "a.nef"] and not list(home.iterdir())      # not a RAW here and a JPEG there


def test_a_work_function_that_raises_ends_the_operation_and_frees_the_worker(qapp):
    jobs = FileJobs(parent=qapp)
    said, done = [], []
    assert jobs.start("Breaking", lambda ctx: 1 / 0, done.append, failed=said.append)
    assert jobs.wait(5000) and not jobs.busy
    assert not done and said and "division" in said[0]
    assert jobs.start("Working", lambda ctx: "ok", done.append)
    assert jobs.wait(5000) and done == ["ok"]
