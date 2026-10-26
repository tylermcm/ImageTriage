"""Ratings and decisions in the QML shell: saved to the Widgets decision store, read back when the folder opens, and undone with Ctrl+Z."""
from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QCoreApplication, QStandardPaths
from PySide6.QtGui import QGuiApplication

from image_triage.models import SessionAnnotation
from image_triage.qml_shell import decision_bridge as bridge_module
from image_triage.qml_shell.decision_bridge import DecisionBridge, annotation_to_view, view_to_annotation
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


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    QStandardPaths.setTestModeEnabled(True)
    monkeypatch.setattr(bridge_module, "session_id", lambda: "qml-test-" + tmp_path.name)
    yield
    QStandardPaths.setTestModeEnabled(False)


@pytest.fixture
def folder(tmp_path):
    shoot = tmp_path / "shoot"
    shoot.mkdir()
    for name in ("a", "b", "c"):
        (shoot / f"{name}.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    return shoot


def open_folder(qapp, folder):
    photos = PhotoModel(parent=qapp)
    toasts = ToastModel(parent=qapp)
    bridge = DecisionBridge(photos, toasts=toasts, parent=qapp)
    photos.loadFolder(str(folder))
    for _ in range(100):
        pump(qapp, 0.05)
        if not photos.loading and photos.total:
            break
    assert photos.total == 3
    return photos, bridge, toasts


def row_of(photos, name):
    return next(i for i in range(photos.shown) if photos.get(i)["name"] == name)


# ---- the mapping -------------------------------------------------------------------------------------------------------------


def test_what_the_shell_shows_for_a_stored_annotation():
    assert annotation_to_view(None) == ("", 0)
    assert annotation_to_view(SessionAnnotation(winner=True, rating=5)) == ("win", 5)
    assert annotation_to_view(SessionAnnotation(reject=True, rating=1)) == ("rej", 1)
    assert annotation_to_view(SessionAnnotation(tags=("keep", "x"), rating=3)) == ("keep", 3)
    assert annotation_to_view(SessionAnnotation(tags=("x",))) == ("", 0)
    assert annotation_to_view(SessionAnnotation(winner=True, reject=True)) == ("rej", 0)      # a reject outranks a winner, as in the Widgets grid


def test_what_is_stored_keeps_what_the_shell_does_not_show():
    previous = SessionAnnotation(photoshop=True, tags=("wedding", "keep"), review_round="round 2", rating=2)
    stored = view_to_annotation("win", 4, previous)
    assert (stored.winner, stored.reject, stored.rating, stored.photoshop, stored.tags, stored.review_round) == (True, False, 4, True, ("wedding",), "round 2")
    stored = view_to_annotation("keep", 4, previous)
    assert stored.tags == ("wedding", "keep") and not stored.winner and not stored.reject
    assert view_to_annotation("", 0, None).is_empty
    assert view_to_annotation("rej", 9, None).rating == 5                                       # stars are 0 to 5


# ---- through the store -------------------------------------------------------------------------------------------------------


def test_a_decision_and_a_rating_come_back_when_the_folder_is_opened_again(qapp, folder):
    photos, bridge, toasts = open_folder(qapp, folder)
    assert all(photos.get(i)["decision"] == "" and photos.get(i)["rating"] == 0 for i in range(photos.shown))      # nothing is invented
    photos.setDecision(row_of(photos, "a.jpg"), "win")
    photos.setRating(row_of(photos, "a.jpg"), 4)
    photos.setDecision(row_of(photos, "b.jpg"), "rej")
    photos.setDecision(row_of(photos, "c.jpg"), "keep")
    bridge.flush()
    pump(qapp, 0.3)
    again, bridge2, _ = open_folder(qapp, folder)
    got = {again.get(i)["name"]: (again.get(i)["decision"], again.get(i)["rating"]) for i in range(again.shown)}
    assert got == {"a.jpg": ("win", 4), "b.jpg": ("rej", 0), "c.jpg": ("keep", 0)}
    assert (again.winCount, again.rejCount, again.keepCount) == (1, 1, 1)
    assert not bridge.failures


def test_it_is_the_widgets_decision_store(qapp, folder):
    from image_triage.decision_store import DecisionStore

    photos, bridge, _ = open_folder(qapp, folder)
    photos.setDecision(row_of(photos, "b.jpg"), "win")
    bridge.flush()
    pump(qapp, 0.3)
    paths = DecisionStore().winner_paths(bridge_module.session_id())
    assert [p.replace("\\", "/").rsplit("/", 1)[-1] for p in paths] == ["b.jpg"]


def test_clearing_a_decision_removes_it_from_the_store(qapp, folder):
    photos, bridge, _ = open_folder(qapp, folder)
    row = row_of(photos, "a.jpg")
    photos.setDecision(row, "win")
    photos.setDecision(row, "win")                                                              # a second press toggles it off
    bridge.flush()
    pump(qapp, 0.3)
    again, _, _ = open_folder(qapp, folder)
    assert again.winCount == 0


def test_a_photo_the_file_changed_under_has_no_saved_decision(qapp, folder):
    photos, bridge, _ = open_folder(qapp, folder)
    photos.setDecision(row_of(photos, "a.jpg"), "win")
    bridge.flush()
    pump(qapp, 0.3)
    (folder / "a.jpg").write_bytes(b"\xff\xd8\xff\xd9" + b"changed")                            # the store matches on size and time, as the Widgets app does
    again, _, _ = open_folder(qapp, folder)
    assert again.winCount == 0


# ---- undo --------------------------------------------------------------------------------------------------------------------


def test_undo_steps_back_through_decisions_and_ratings_and_saves_them(qapp, folder):
    photos, bridge, _ = open_folder(qapp, folder)
    assert photos.canUndo is False and photos.undo() == ""
    row = row_of(photos, "a.jpg")
    photos.setDecision(row, "win")
    photos.setRating(row, 5)
    assert photos.canUndo is True
    assert photos.undo() == "rating of a.jpg" and photos.get(row_of(photos, "a.jpg"))["rating"] == 0
    assert photos.undo() == "decision of a.jpg" and photos.winCount == 0
    assert photos.undo() == "" and photos.canUndo is False
    bridge.flush()
    pump(qapp, 0.3)
    again, _, _ = open_folder(qapp, folder)
    assert again.winCount == 0                                                                   # the store ends where the undo did


def test_a_change_that_changes_nothing_is_not_an_undo_step(qapp, folder):
    photos, bridge, _ = open_folder(qapp, folder)
    photos.setDecision(row_of(photos, "a.jpg"), "")
    assert photos.canUndo is False


def test_undo_is_per_folder(qapp, folder, tmp_path):
    photos, bridge, _ = open_folder(qapp, folder)
    photos.setDecision(0, "win")
    assert photos.canUndo
    other = tmp_path / "other"
    other.mkdir()
    (other / "z.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    photos.loadFolder(str(other))
    for _ in range(60):
        pump(qapp, 0.05)
        if not photos.loading:
            break
    assert photos.canUndo is False


def test_mock_photos_are_never_saved(qapp):
    photos = PhotoModel(parent=qapp)
    saved = []
    photos.annotationChanged.connect(saved.append)
    photos.setRows([{"path": "x.jpg", "name": "x.jpg", "ext": "JPG", "raw": False, "decision": "", "rating": 0, "ai": 50}], "sample")
    photos.setDecision(0, "win")
    assert saved == [] and photos.winCount == 1


def test_a_failed_save_is_reported(qapp, folder, monkeypatch):
    photos, bridge, toasts = open_folder(qapp, folder)
    bridge._failed("D:/x/a.jpg", "disk is full")
    assert toasts.items[-1]["kind"] == "error" and "a.jpg: disk is full" in toasts.items[-1]["body"]
