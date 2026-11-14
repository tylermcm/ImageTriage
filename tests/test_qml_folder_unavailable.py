"""A folder that cannot be read: the grid says so, offers Retry and another folder, and opens it by itself when it is back."""
from __future__ import annotations

import threading
import time

import pytest
from PySide6.QtCore import QCoreApplication, QObject, QPointF, QStandardPaths, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from image_triage.qml_shell import decision_bridge as bridge_module
from image_triage.qml_shell import photos as photos_module
from image_triage.qml_shell.decision_bridge import DecisionBridge
from image_triage.qml_shell.menu_model import ToastModel
from image_triage.qml_shell.photos import PhotoModel


@pytest.fixture(scope="module")
def qapp():
    return QCoreApplication.instance() or QGuiApplication([])


def pump(app, seconds=0.2):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def wait_for(app, done, seconds=6.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        pump(app, 0.03)
        if done():
            return True
    return False


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    QStandardPaths.setTestModeEnabled(True)
    monkeypatch.setattr(bridge_module, "session_id", lambda: "qml-unavailable-" + tmp_path.name)
    monkeypatch.setattr(PhotoModel, "RECHECK_MS", 80)
    yield
    QStandardPaths.setTestModeEnabled(False)


def shoot_in(folder, names=("a", "b", "c")):
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / f"{name}.jpg").write_bytes(b"\xff\xd8\xff\xd9" + name.encode())
    return folder


def test_a_folder_that_is_not_there_is_an_error_not_an_empty_folder(qapp, tmp_path):
    photos = PhotoModel(parent=qapp)
    photos.loadFolder(str(tmp_path / "offline"))
    assert wait_for(qapp, lambda: not photos.loading)
    assert photos.loadError != "" and photos.total == 0 and photos.folderPath == str(tmp_path / "offline")


def test_it_is_read_by_itself_when_it_comes_back_and_asked_off_the_ui_thread(qapp, tmp_path, monkeypatch):
    asked_on = []
    real = photos_module._readable
    mine = str(tmp_path / "offline")                                    # (models of other tests may still be asking about their own folders)
    monkeypatch.setattr(photos_module, "_readable", lambda folder: (asked_on.append(threading.current_thread()) if folder == mine else None, real(folder))[1])
    photos = PhotoModel(parent=qapp)
    photos.loadFolder(mine)
    assert wait_for(qapp, lambda: not photos.loading and photos.loadError != "")
    pump(qapp, 0.4)
    assert asked_on and photos.loadError != "" and not photos.loading, "still away: it keeps asking, and nothing is read"
    shoot_in(tmp_path / "offline")
    assert wait_for(qapp, lambda: photos.total == 3 and photos.loadError == "")
    assert all(thread is not threading.main_thread() for thread in asked_on), "the folder is never asked about on the UI thread"
    count = len(asked_on)
    pump(qapp, 0.4)
    assert len(asked_on) == count, "once it is back the asking stops"


def test_one_question_at_a_time_while_a_share_does_not_answer(qapp, tmp_path, monkeypatch):
    release = threading.Event()
    asked = []
    mine = str(tmp_path / "offline")
    monkeypatch.setattr(photos_module, "_readable", lambda folder: (asked.append(folder), release.wait(5), False)[2] if folder == mine else False)
    photos = PhotoModel(parent=qapp)
    photos.loadFolder(mine)
    assert wait_for(qapp, lambda: len(asked) == 1)
    pump(qapp, 0.5)                                                     # several intervals pass while the first question hangs
    assert len(asked) == 1
    release.set()
    pump(qapp, 0.3)


def test_decisions_survive_the_folder_being_away(qapp, tmp_path):
    shoot = shoot_in(tmp_path / "shoot")
    photos = PhotoModel(parent=qapp)
    bridge = DecisionBridge(photos, toasts=ToastModel(parent=qapp), parent=qapp)
    photos.loadFolder(str(shoot))
    assert wait_for(qapp, lambda: not photos.loading and photos.total == 3)
    photos.setDecision(0, "win")
    photos.setRating(1, 4)
    bridge.flush()
    shoot.rename(tmp_path / "unplugged")
    photos.reload()
    assert wait_for(qapp, lambda: not photos.loading and photos.loadError != "")
    assert photos.total == 0
    bridge.flush()
    (tmp_path / "unplugged").rename(shoot)
    assert wait_for(qapp, lambda: photos.total == 3 and photos.loadError == "")
    assert photos.get(0)["decision"] == "win" and photos.get(1)["rating"] == 4


def walk(item):
    yield item
    for child in item.childItems():
        yield from walk(child)


def test_the_grid_says_what_happened_and_retry_reads_the_folder_again(qapp, tmp_path, monkeypatch):
    from test_qml_shell import _load_main

    monkeypatch.setattr(PhotoModel, "RECHECK_MS", 600000)               # this test presses Retry itself
    engine, window, warnings = _load_main(qapp, tmp_path)
    photos = engine.rootContext().contextProperty("photos")
    window.setWidth(1440)
    window.setHeight(900)
    window.show()
    away = tmp_path / "nas" / "2026 wedding"
    photos.loadFolder(str(away))
    assert wait_for(qapp, lambda: not photos.loading and photos.loadError != "")
    pump(qapp, 0.4)
    panel = window.findChild(QObject, "folderUnavailable")
    assert panel.property("visible")
    assert window.findChild(QObject, "folderUnavailablePath").property("text") == str(away)
    assert window.findChild(QObject, "folderUnavailableReason").property("text") == photos.loadError
    retry, other = window.findChild(QObject, "folderRetry"), window.findChild(QObject, "folderChooseOther")
    assert retry.property("visible") and other.property("visible")

    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, retry.mapToScene(QPointF(retry.width() / 2, retry.height() / 2)).toPoint())
    assert wait_for(qapp, lambda: not photos.loading)
    assert panel.property("visible"), "still away: the screen stays"

    shoot_in(away)
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, retry.mapToScene(QPointF(retry.width() / 2, retry.height() / 2)).toPoint())
    assert wait_for(qapp, lambda: photos.total == 3)
    pump(qapp, 0.2)
    assert not panel.property("visible") and window.findChild(QObject, "photoGrid").property("count") == 3
    assert not [w for w in warnings if "PhotoGrid.qml" in w], warnings
    window.close(); engine.deleteLater(); pump(qapp, 0.2)


def test_the_open_folder_being_deleted_shows_the_screen_and_its_return_is_watched_again(qapp, tmp_path, monkeypatch):
    from image_triage import path_policy
    from image_triage.qml_shell import library_nav as nav_module
    from image_triage.qml_shell.library_nav import LibraryNavModel

    monkeypatch.setattr(path_policy, "is_plain_local", lambda path: True)
    shoot = shoot_in(tmp_path / "shoot")
    photos = PhotoModel(parent=qapp)
    nav = LibraryNavModel(photos, memory=False, persist=False, parent=qapp)
    if nav._watcher is None:
        pytest.skip("no folder watcher on this platform")
    monkeypatch.setattr(nav, "_watching", lambda: True)
    nav.openFolder(str(shoot))
    assert wait_for(qapp, lambda: not photos.loading and photos.total == 3)
    shoot.rename(tmp_path / "moved away")
    nav._watched_change()                                               # what the watcher's timer runs when the folder changes
    assert wait_for(qapp, lambda: not photos.loading and photos.loadError != "")
    (tmp_path / "moved away").rename(shoot)
    assert wait_for(qapp, lambda: photos.total == 3 and photos.loadError == "")
    assert nav._watcher.directories() == [str(shoot)], "a folder that came back is watched again"
    assert nav_module is not None
