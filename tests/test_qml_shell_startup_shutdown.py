"""Starting and stopping the shell, and what its models may ask the disk on the interface thread."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QThreadPool
from PySide6.QtGui import QGuiApplication

from image_triage import path_policy
from image_triage.app_identity import user_settings
from image_triage.drive_list_model import DriveListModel
from image_triage.qml_shell import app as shell_app
from image_triage.qml_shell import diagnostics, library_nav, people_model, photo_jobs, pocketdrop_model, search_model
from image_triage.qml_shell.library_nav import LibraryNavModel
from image_triage.qml_shell.people_model import PeopleModel
from image_triage.qml_shell.photo_jobs import LatestPhotoJobs
from image_triage.qml_shell.photos import PhotoModel
from image_triage.qml_shell.pocketdrop_model import PocketDropModel
from image_triage.qml_shell.search_model import SearchModel
from image_triage.qml_shell.single_instance import InstanceServer, send_to_running

ROOT = Path(__file__).resolve().parents[1]
MAIN = threading.main_thread()


@pytest.fixture(scope="module")
def qapp():
    return QCoreApplication.instance() or QGuiApplication([])


def until(app, done, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not done():
        app.processEvents()
        time.sleep(0.005)
    assert done()


@pytest.fixture
def clean_settings():
    keys = (library_nav.RECENT_KEY, library_nav.LAST_FOLDER_KEY, library_nav.RESTORE_POSITION_KEY, library_nav.FOLDER_STATE_KEY, library_nav.WATCH_KEY)
    settings = user_settings()
    for key in keys:
        settings.remove(key)
    yield
    for key in keys:
        settings.remove(key)


@pytest.fixture
def shoot(tmp_path):
    from PIL import Image

    folder = tmp_path / "Photos" / "Shoot"
    folder.mkdir(parents=True)
    for index in range(4):
        Image.new("RGB", (32, 24), (index * 50, 80, 120)).save(folder / f"IMG_{index:02d}.jpg")
    return folder


def make_nav(qapp, root):
    photos = PhotoModel(parent=qapp)
    nav = LibraryNavModel(photos, drives=DriveListModel(roots_provider=lambda: [], probe=lambda p: (False, "", None)),
                          drive_root=lambda path: str(root), parent=qapp)
    return nav, photos


# -- where the person was, and the photo that was opened ------------------------------------------------------------------------

def test_the_position_is_kept_for_a_folder_opened_through_a_link(qapp, shoot, tmp_path, clean_settings):
    link = tmp_path / "Link"
    try:
        link.symlink_to(shoot, target_is_directory=True)
    except OSError:
        pytest.skip("links cannot be made here")
    nav, photos = make_nav(qapp, tmp_path)
    nav.openFolder(str(link))
    until(qapp, lambda: not photos.loading and photos.total == 4)
    third = photos.row_at(2)["path"]
    assert os.path.dirname(third) != str(link)               # the scanner lists the folder under the path it resolves to
    nav.savePosition(third)
    states = json.loads(user_settings().value(library_nav.FOLDER_STATE_KEY, "", str))
    assert states[library_nav._key(str(link))] == {"current": third}
    again, photos_again = make_nav(qapp, tmp_path)
    rows = []
    again.positionRestored.connect(rows.append)
    again.openFolder(str(link))
    until(qapp, lambda: rows)
    assert rows == [2]
    again.savePosition(photos_again.row_at(1)["path"])
    more = []
    again.positionRestored.connect(more.append)
    again.refreshFolder()                                     # reading it again stays on the photo, through the link too
    until(qapp, lambda: more)
    assert more[-1] == 1


def test_a_photo_of_another_folder_is_still_not_this_folders_position(qapp, shoot, tmp_path, clean_settings):
    other = tmp_path / "Photos" / "Other"
    other.mkdir()
    nav, photos = make_nav(qapp, tmp_path)
    nav.openFolder(str(shoot))
    until(qapp, lambda: not photos.loading and photos.total == 4)
    nav.savePosition(str(other / "IMG_00.jpg"))
    assert user_settings().value(library_nav.FOLDER_STATE_KEY, "", str) == ""


def test_opening_a_photo_opens_its_folder_on_that_photo(qapp, shoot, tmp_path, clean_settings):
    nav, photos = make_nav(qapp, tmp_path)
    nav.openFolder(str(shoot))
    until(qapp, lambda: not photos.loading and photos.total == 4)
    nav.savePosition(photos.row_at(1)["path"])               # where the person was last time
    again, photos_again = make_nav(qapp, tmp_path)
    rows = []
    again.positionRestored.connect(rows.append)
    again.open_file(str(shoot / "IMG_03.jpg"))
    until(qapp, lambda: rows)
    assert again.openPath == str(shoot) and rows == [3]      # the photo that was opened, not the remembered one


def test_opening_a_photo_through_a_link_finds_it_by_name(qapp, shoot, tmp_path, clean_settings):
    link = tmp_path / "Link"
    try:
        link.symlink_to(shoot, target_is_directory=True)
    except OSError:
        pytest.skip("links cannot be made here")
    nav, photos = make_nav(qapp, tmp_path)
    rows = []
    nav.positionRestored.connect(rows.append)
    nav.open_file(str(link / "IMG_02.jpg"))
    until(qapp, lambda: rows)
    assert rows == [2]


# -- nothing is asked of the disk on the interface thread -----------------------------------------------------------------------

def test_the_inspector_jobs_look_at_the_file_on_their_worker(qapp, tmp_path, monkeypatch):
    path = tmp_path / "a.jpg"
    path.write_bytes(b"one")
    asked = []
    real = os.stat

    class Os:
        @staticmethod
        def stat(target, *args, **kwargs):
            asked.append(threading.current_thread())
            return real(target, *args, **kwargs)

    monkeypatch.setattr(photo_jobs, "os", Os)
    loads = []
    jobs = LatestPhotoJobs(lambda p: loads.append(p) or {"value": len(loads)}, parent=qapp)
    assert jobs.get(str(path)) == {"ready": False}
    jobs.request(str(path))
    until(qapp, lambda: jobs.get(str(path))["ready"])
    for _again in range(5):                                  # every change of the selection asks both
        jobs.request(str(path))
        assert jobs.get(str(path)) == {"ready": True, "value": 1}
    until(qapp, lambda: not jobs._pending)
    assert loads == [str(path)] and asked and MAIN not in asked
    path.write_bytes(b"edited in place")
    os.utime(path, ns=(time.time_ns(), time.time_ns() + 5_000_000_000))
    jobs.request(str(path))
    until(qapp, lambda: jobs.get(str(path)).get("value") == 2)       # the worker saw the new time and read the photo again
    path.unlink()
    jobs.request(str(path))
    until(qapp, lambda: jobs.get(str(path)) == {"ready": False})
    assert MAIN not in asked
    jobs.shutdown(0)


def rows(folder, count=3):
    return [{"path": str(folder / f"IMG_{i}.jpg"), "name": f"IMG_{i}.jpg", "raw": False, "rating": 0, "decision": "", "ai": 0, "ext": "JPG", "companions": ()}
            for i in range(count)]


def test_a_keystroke_in_the_search_box_filters_the_grid_once(qapp, tmp_path):
    photos = PhotoModel(parent=qapp)
    photos.setRows(rows(tmp_path), str(tmp_path))
    search = SearchModel(photos, db_path=lambda folder: tmp_path / "missing.sqlite", parent=qapp)
    resets = []
    photos.modelReset.connect(lambda: resets.append(1))
    photos.setSearch("IMG_1")
    assert len(resets) == 1 and photos.shown == 1 and photos.searchStatus == "Searching photos…"
    search.shutdown()


def test_the_search_index_of_a_share_is_looked_for_off_the_interface_thread(qapp, tmp_path, monkeypatch):
    photos = PhotoModel(parent=qapp)
    photos.setRows(rows(tmp_path), str(tmp_path))
    asked = []

    def db_path(folder):
        asked.append(threading.current_thread())
        return tmp_path / "missing.sqlite"

    search = SearchModel(photos, db_path=db_path, parent=qapp)
    monkeypatch.setattr(search_model.path_policy, "is_plain_local", lambda path: False)       # a network share
    photos.setSearch("IMG_1")
    search._timer.stop()
    search._start()
    until(qapp, lambda: "filenames" in photos.searchStatus)
    assert asked and MAIN not in asked
    photos.setSearch("IMG_2")                                # an answer for a query that has been replaced is dropped
    search._timer.stop()
    search._launch(search._token - 1, str(tmp_path), None)
    assert photos.searchStatus == "Searching photos…"
    search.shutdown()


def test_the_face_index_is_read_off_the_interface_thread_and_follows_the_folder(qapp, tmp_path, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    reads = []

    def read(db):
        reads.append(threading.current_thread())
        return [{"id": "1", "name": "Ada", "count": 4, "clusters": [1], "ignored": False, "hue": 40}] if db == first / "db" else []

    monkeypatch.setattr(PeopleModel, "_db_path", staticmethod(lambda folder: Path(folder) / "db"))
    monkeypatch.setattr(people_model, "_read_people", read)
    photos = PhotoModel(parent=qapp)
    people = PeopleModel(photos, parent=qapp)
    assert not people.hasIndex and reads == []               # no folder: nothing is read
    photos.setRows(rows(first), str(first))
    until(qapp, lambda: people.stats()["named"] == 1)
    assert people.hasIndex and MAIN not in reads
    info = people.indexInfo()
    assert info["meter"] is False and info["value"] == 0.0 and info["text"] == "1 person found in this folder"
    assert people.indexShare == -1.0 and people.indexText == info["text"]        # no share is known: no full bar is claimed
    revision = people.revision
    photos.setRows(rows(second), str(second))
    assert people.stats()["named"] == 0 and not people.hasIndex and people.revision > revision      # the last folder's people go at once
    until(qapp, lambda: len(reads) == 2)
    count = len(reads)
    people.reload()                                          # what an analysis that has just finished asks for
    until(qapp, lambda: len(reads) == count + 1)
    assert MAIN not in reads


def test_the_sample_face_index_keeps_its_meter(qapp):
    people = PeopleModel(PhotoModel(parent=qapp), memory=True, parent=qapp)
    assert people.indexInfo() == {"meter": True, "value": 0.86, "text": "1,104 of 1,284 photos scanned", "pct": "86%"}
    assert people.indexShare == 0.86 and people.indexPct == "86%"


def test_the_library_picker_measures_files_off_the_interface_thread(qapp, tmp_path, monkeypatch):
    photos = PhotoModel(parent=qapp)
    made = rows(tmp_path, 5)
    for row in made:
        Path(row["path"]).write_bytes(b"x" * 3000)
        row["decision"] = "win"
    photos.setRows(made, str(tmp_path))
    measured = []
    real = pocketdrop_model._file_size
    monkeypatch.setattr(pocketdrop_model, "_file_size", lambda path: measured.append(threading.current_thread()) or real(path))
    model = PocketDropModel(photos, memory=False, parent=qapp)
    model.refreshLibrary(0)
    entries = {e["key"]: e for bucket in model.library.values() for e in bucket}
    assert entries["win"]["sub"] == "5 photos" and entries["win"]["count"] == 5
    model.addLibrary("win")                                  # added before the size is known
    until(qapp, lambda: "size" in entries["win"])
    assert entries["win"]["size"] == 15000 and entries["win"]["sub"].startswith("5 photos · ")
    assert model._groups[0]["size"] == 15000 and model.rows[0]["sub"] == entries["win"]["sub"]
    assert measured and MAIN not in measured
    assert len(measured) == 5                                # a photo in two entries (the selection and the winners) is measured once


# -- starting -------------------------------------------------------------------------------------------------------------------

def test_a_failed_start_is_said_and_logged(qapp, tmp_path, monkeypatch, capsys):
    log = tmp_path / "logs" / "shell.log"
    monkeypatch.setattr(diagnostics, "log_path", lambda: log)
    monkeypatch.setitem(diagnostics._state, "file", None)
    shown = []
    monkeypatch.setattr(diagnostics, "_message_box", lambda title, text: shown.append((title, text)))
    hidden = []

    def run(args, state):
        state["hide_splash"] = lambda: hidden.append(True)
        raise RuntimeError("no display driver")

    monkeypatch.setattr(shell_app, "_run", run)
    previous = sys.excepthook, threading.excepthook
    try:
        assert shell_app.main([]) == 1
    finally:
        sys.excepthook, threading.excepthook = previous
        from PySide6.QtCore import qInstallMessageHandler

        qInstallMessageHandler(None)
        handle = diagnostics._state["file"]
        if handle is not None:
            handle.close()
    assert hidden == [True]                                  # the splash is closed before the message
    assert len(shown) == 1 and shown[0][0] == "Image Triage"
    assert "could not start" in shown[0][1] and "no display driver" in shown[0][1] and str(log) in shown[0][1]
    assert "no display driver" in capsys.readouterr().err
    text = log.read_text(encoding="utf-8")
    assert text.index("shell started") < text.index("fatal: Image Triage could not start.")      # the log was open before anything else ran


def test_a_headless_run_reports_without_a_message_window(qapp, monkeypatch, capsys):
    shown = []
    monkeypatch.setattr(diagnostics, "_message_box", lambda title, text: shown.append(text))
    monkeypatch.setattr(shell_app, "_run", lambda args, state: (_ for _ in ()).throw(RuntimeError("broken")))
    assert shell_app.main(["--grab", "out.png"]) == 1
    assert shown == [] and "broken" in capsys.readouterr().err


def test_an_interface_that_does_not_load_ends_with_a_message(tmp_path):
    broken = tmp_path / "qml"
    broken.mkdir()
    (broken / "Main.qml").write_text("import QtQuick\nWindow { nonsense: 1 }\n", encoding="utf-8")
    code = ("import sys\nfrom pathlib import Path\nfrom image_triage.qml_shell import app\n"
            "app.QML_DIR = Path(sys.argv[1])\nraise SystemExit(app.main(['--mock', sys.argv[2]]))\n")
    result = subprocess.run([sys.executable, "-c", code, str(broken), str(tmp_path)], cwd=ROOT, capture_output=True, text=True, timeout=120,
                            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
    assert result.returncode == 1, result.stdout + result.stderr
    assert "could not load its interface" in result.stderr and "nonsense" in result.stderr


def test_scripted_scenes_are_only_for_the_sample_page():
    ui = "tab=faces;cullScene=cu-done;ctxScene=ctx;refScene=hlp-start;pdMode=anywhere;txState=progress;vwScene=view;hoRecipe=x;insp=false"
    assert shell_app.startup_settings(ui, mock=True, grab=False) == ui
    assert shell_app.startup_settings(ui, mock=True, grab=True) == ui
    assert shell_app.startup_settings(ui, mock=False, grab=True) == "tab=faces;insp=false"      # a render of a real folder: the layout only
    assert shell_app.startup_settings(ui, mock=False, grab=False) == ""                         # a normal launch: nothing


def test_the_window_goes_back_where_it_was_when_its_monitor_is_still_there():
    screens = [("MAIN", (0, 0, 1920, 1040)), ("SIDE", (1920, 0, 2560, 1400))]
    stored = shell_app.window_place("SIDE", (2100, 80, 1500, 900), False)
    assert shell_app.saved_window_place(json.loads(json.dumps(stored)), screens) == ("SIDE", (2100, 80, 1500, 900), False)
    assert shell_app.saved_window_place(dict(stored, maximized=True), screens) == ("SIDE", (2100, 80, 1500, 900), True)
    assert shell_app.saved_window_place(stored, screens[:1]) is None                 # the monitor was unplugged: as before, maximised on the main one
    assert shell_app.saved_window_place(dict(stored, x=9000), screens) is None        # the layout changed: it would be off the monitor
    assert shell_app.saved_window_place(dict(stored, y=-400), screens) is None        # its top bar would be out of reach
    assert shell_app.saved_window_place(dict(stored, width=80), screens) is None
    assert shell_app.saved_window_place(None, screens) is None and shell_app.saved_window_place({"screen": "MAIN"}, screens) is None
    assert shell_app.window_place("MAIN", None, True) is None                         # never shown un-maximised: nothing to remember yet


def test_a_second_launch_hands_its_photo_to_the_running_app(qapp):
    name = f"ImageTriage-test-{os.getpid()}"
    assert send_to_running({"folder": "/photos"}, name=name, timeout_ms=300) is False       # nobody is running
    server = InstanceServer(name=name, parent=qapp)
    assert server.listen()
    got = []
    server.received.connect(got.append)
    message = {"folder": "/photos/shoot", "select": "/photos/shoot/IMG_0001.jpg"}
    # The second launch is another process, as it is in use (its waits would hold this one's interpreter if it were a thread here).
    code = ("import json, sys\nfrom PySide6.QtCore import QCoreApplication\nfrom image_triage.qml_shell.single_instance import send_to_running\n"
            "app = QCoreApplication([])\nraise SystemExit(0 if send_to_running(json.loads(sys.argv[2]), name=sys.argv[1], timeout_ms=10000) else 3)\n")
    client = subprocess.Popen([sys.executable, "-c", code, name, json.dumps(message)], cwd=ROOT)
    until(qapp, lambda: client.poll() is not None, seconds=30)
    assert client.returncode == 0 and got == [message]
    server.close()
    assert send_to_running(message, name=name, timeout_ms=300) is False                     # it has quit: the next launch starts normally
    again = InstanceServer(name=name, parent=qapp)
    assert again.listen()                                    # and can listen where the last one did
    again.close()


# -- stopping -------------------------------------------------------------------------------------------------------------------

def test_quitting_stops_the_transfer_and_drops_what_is_queued(qapp):
    class Runner:
        running = True
        cancelled = 0

        def cancel(self):
            self.cancelled += 1

    class Service:
        waits: list = []

        def shutdown(self, wait_ms):
            self.waits.append(wait_ms)

    pool = QThreadPool()
    pool.setMaxThreadCount(1)
    release, ran = threading.Event(), []
    for index in range(6):
        pool.start(lambda index=index: (ran.append(index), release.wait(5)))
    until(qapp, lambda: ran == [0])
    runner, service = Runner(), Service()
    started = time.monotonic()
    shell_app.stop_background(runner, (pool,), (service,), wait_ms=300)
    assert time.monotonic() - started < 2.0                  # the wait for what is running is bounded
    assert runner.cancelled == 1 and len(service.waits) == 1 and service.waits[0] <= 300
    release.set()
    assert pool.waitForDone(5000) and ran == [0]             # the five that had not started never ran


def test_viewer_folders_left_by_an_earlier_run_are_removed(tmp_path):
    old, fresh, other = tmp_path / "imagetriage-viewer-old", tmp_path / "imagetriage-viewer-fresh", tmp_path / "something-else"
    for folder in (old, fresh, other):
        folder.mkdir()
        (folder / "picture.jpg").write_bytes(b"x")
    stale = time.time() - 3600
    os.utime(old, (stale, stale))
    os.utime(other, (stale, stale))
    assert shell_app.sweep_viewer_temp(str(tmp_path)) == [str(old)]
    assert not old.exists() and fresh.exists() and other.exists()        # one in use beside us, and anything that is not ours, stay
