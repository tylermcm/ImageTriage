"""Where a cull review was left is kept per folder and put back when the folder's saved results are shown again, and never onto
another report. The analysis here is the real cleanup cascade over small generated JPEGs (no models, no runtime: none exist here)."""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, Qt
from PySide6.QtGui import QGuiApplication, QWheelEvent

from image_triage import cull_review_store as store
from image_triage.path_keys import path_key
from image_triage.qml_shell.cull_model import CullModel
from image_triage.qml_shell.menu_model import ToastModel
from image_triage.qml_shell.photos import PhotoModel
from test_qml_shell_cull_real import isolated, make_image, wait_for, world  # noqa: F401
from test_qml_shell import _load_main, pump, qapp, rows  # noqa: F401


@pytest.fixture(autouse=True)
def app_data(tmp_path, monkeypatch):
    """The app's data folder for these tests: nothing is written to this machine's own."""
    root = tmp_path / "app-data" / store.DIRECTORY
    monkeypatch.setattr(store, "store_dir", lambda: root)
    return root


STATE = {"filter": "soft", "sort": "reason", "restored": ["/p/b.jpg", "/p/a.jpg"], "sel": "/p/a.jpg", "top": 12,
         "applied": {"act": "reject", "ids": ["/p/c.jpg"], "when": "2026-11-14 10:00"}}


# -- the store ------------------------------------------------------------------------------------------------------------------------

def test_a_review_is_kept_per_folder_under_the_shared_path_key(app_data):
    assert store.save("/shoots/day one", "cascade:abc", STATE)
    kept = json.loads(store.state_path("/shoots/day one").read_text(encoding="utf-8"))
    assert kept["folder"] == path_key("/shoots/day one") and kept["report"] == "cascade:abc" and kept["version"] == 1
    assert store.state_path("/shoots/day one").parent == app_data            # in the app's data, never beside the photos
    assert store.state_path("/shoots/day one/") == store.state_path("/shoots/./day one")      # one key however the path is written
    assert store.load("/shoots/day one", "cascade:abc") == {**STATE, "restored": ["/p/a.jpg", "/p/b.jpg"]}
    assert store.load("/shoots/day two", "cascade:abc") is None               # another folder


def test_a_review_is_never_given_to_another_report(app_data):
    store.save("/shoots/a", "cascade:first", STATE)
    assert store.load("/shoots/a", "cascade:second") is None
    assert store.load("/shoots/a", "") is None and not store.save("/shoots/a", "", STATE)      # no report: nothing kept, nothing given
    assert store.load("/shoots/a", "cascade:first") is not None               # the mismatch did not destroy it
    store.forget("/shoots/a")
    assert store.load("/shoots/a", "cascade:first") is None
    store.forget("/shoots/a")                                               # forgetting twice is not an error


def test_malformed_or_foreign_files_give_nothing(app_data):
    target = store.state_path("/shoots/a")
    target.parent.mkdir(parents=True)
    target.write_text("{not json", encoding="utf-8")
    assert store.load("/shoots/a", "x") is None
    target.write_text(json.dumps({"version": 99, "folder": path_key("/shoots/a"), "report": "x"}), encoding="utf-8")
    assert store.load("/shoots/a", "x") is None                             # a later format is not guessed at
    target.write_text(json.dumps({"version": 1, "folder": path_key("/elsewhere"), "report": "x"}), encoding="utf-8")
    assert store.load("/shoots/a", "x") is None                             # a file for another folder (a hash collision) is not this folder's
    target.write_text(json.dumps({"version": 1, "folder": path_key("/shoots/a"), "report": "x", "filter": 7, "restored": "all", "top": -3,
                                   "applied": {"act": "explode", "ids": [1]}, "sort": "sideways"}), encoding="utf-8")
    assert store.load("/shoots/a", "x") == {"filter": "all", "sort": "stack", "restored": [], "sel": "", "top": 0, "applied": None}
    assert store.is_default({}) and not store.is_default(STATE)


# -- the report's identity ----------------------------------------------------------------------------------------------------------

def test_the_identity_follows_the_reports_content_not_its_dates(qapp, world):
    shoot, photos, toasts, files, cull, tmp = world
    assert store.report_identity(str(shoot)) == ""                           # never analysed: no report
    cull._s.update(rt="missing", eyes=False, rank=False)
    done = []
    cull.runFinished.connect(lambda: done.append(True))
    cull.start()
    assert wait_for(qapp, lambda: done)
    first = store.report_identity(str(shoot))
    assert first.startswith("cascade:") and wait_for(qapp, lambda: cull._review_id == first)
    for path in (shoot / ".image_triage_ai").rglob("*"):
        if path.is_file():
            os.utime(path, (1_700_000_000, 1_700_000_000))                  # copied or synced: the dates change, the report does not
    assert store.report_identity(str(shoot)) == first
    time.sleep(1.1)                                                         # the summary's time is to the second
    done.clear()
    cull.start()                                                            # the same photos analysed again: the same decisions, a new report
    assert wait_for(qapp, lambda: done)
    second = store.report_identity(str(shoot))
    assert second and second != first and wait_for(qapp, lambda: cull._review_id == second)


# -- the Cull window ------------------------------------------------------------------------------------------------------------------

def analysed(qapp, world):
    shoot, photos, toasts, files, cull, tmp = world
    cull._s.update(rt="missing", eyes=False, rank=False)
    done = []
    cull.runFinished.connect(lambda: done.append(True))
    cull.start()
    assert wait_for(qapp, lambda: done) and wait_for(qapp, lambda: cull._review_id != "")
    return shoot, photos, toasts, cull


def reopened(qapp, shoot, toasts):
    photos = PhotoModel(parent=qapp)
    cull = CullModel(photos, memory=False, parent=qapp, toasts=toasts)
    cull._ai = None
    photos.loadFolder(str(shoot))
    assert wait_for(qapp, lambda: cull._s["done"] and cull._real), "the saved report was not found"
    return photos, cull


def kept_on_disk(shoot):
    try:
        return json.loads(store.state_path(str(shoot)).read_text(encoding="utf-8"))
    except OSError:
        return None


def test_the_review_is_put_back_after_the_app_restarts(qapp, world):
    shoot, photos, toasts, cull = analysed(qapp, world)
    soft = str(shoot / "b.jpg")
    assert cull._flags.get(soft) == "soft"
    cull.setFilter("soft")
    cull.setReviewSort("reason")
    cull.pick(soft)
    cull.toggleRestore()                                                    # the person keeps the soft frame after all
    cull.setReviewTop(1)
    assert wait_for(qapp, lambda: (kept_on_disk(shoot) or {}).get("restored") == [soft])
    kept = kept_on_disk(shoot)
    assert kept["filter"] == "soft" and kept["sort"] == "reason" and kept["sel"] == soft and kept["top"] == 1 and kept["applied"] is None
    assert not list(shoot.rglob("*review*.json"))                           # nothing of it is written beside the photos

    other, again = reopened(qapp, shoot, toasts)                            # a new session
    s = again._s
    assert s["filter"] == "soft" and s["sort"] == "reason" and s["restored"] == {soft: True} and s["sel"] == soft
    assert again.view["reviewTop"] == 1 and again._review_id == cull._review_id
    assert soft not in {row["path"] for row, _reason in again._active_flags()}      # the restored frame is not flagged again
    assert again.view["det"]["restoreLabel"] == "Flag again"
    assert not again._review_timer.isActive()                               # putting it back is not a change to write again


def test_an_applied_result_is_remembered_as_applied(qapp, world):
    shoot, photos, toasts, cull = analysed(qapp, world)
    cull.openReview()
    cull.toApply()
    cull.apply()
    assert wait_for(qapp, lambda: cull._s["applied"] is not None and cull._apply_job is None)
    assert wait_for(qapp, lambda: ((kept_on_disk(shoot) or {}).get("applied") or {}).get("act") == "reject")
    kept = kept_on_disk(shoot)
    assert kept["applied"]["ids"] == [str(shoot / "b.jpg")] and kept["applied"]["when"]
    cull.shutdown()

    other, again = reopened(qapp, shoot, toasts)
    applied = again._s["applied"]
    assert applied["act"] == "reject" and applied["ids"] == [str(shoot / "b.jpg")] and applied["kept"]
    assert not again.view["canUndo"]                                        # that session's undo step is gone: none is offered
    assert any(note.startswith("You applied these results on ") and "1 frame marked as Rejects" in note for note in again.view["runNotes"])
    steps = again.view["steps"] if "steps" in again.view else []
    assert not steps or any("applied" in str(step) for step in steps)


def test_a_new_analysis_does_not_inherit_the_old_review(qapp, world):
    shoot, photos, toasts, cull = analysed(qapp, world)
    soft = str(shoot / "b.jpg")
    cull.setFilter("soft")
    cull.pick(soft)
    cull.toggleRestore()
    assert wait_for(qapp, lambda: (kept_on_disk(shoot) or {}).get("restored") == [soft])
    old = cull._review_id
    time.sleep(1.1)
    done = []
    cull.runFinished.connect(lambda: done.append(True))
    cull.start()
    assert cull._s["restored"] == {} and cull._s["filter"] == "all"          # the run starts a fresh review
    assert wait_for(qapp, lambda: done) and wait_for(qapp, lambda: cull._review_id not in ("", old))
    assert cull._flags.get(soft) == "soft" and cull._active_flags()          # flagged again: the earlier restore was of the earlier report
    assert wait_for(qapp, lambda: (kept_on_disk(shoot) or {}).get("report") == cull._review_id)
    assert kept_on_disk(shoot)["restored"] == []
    other, again = reopened(qapp, shoot, toasts)
    assert again._s["restored"] == {} and again._s["filter"] == "all" and again._active_flags()


def test_a_review_kept_for_another_report_is_not_put_back(qapp, world):
    shoot, photos, toasts, cull = analysed(qapp, world)
    soft = str(shoot / "b.jpg")
    store.save(str(shoot), "cascade:" + "0" * 64, {"filter": "soft", "restored": [soft], "sel": soft, "top": 3,
                                                 "applied": {"act": "reject", "ids": [soft], "when": "2026-01-01 09:00"}})
    other, again = reopened(qapp, shoot, toasts)
    s = again._s
    assert s["filter"] == "all" and s["restored"] == {} and s["applied"] is None and again.view["reviewTop"] == 0
    assert again._review_id == cull._review_id and again._active_flags()


def test_what_no_longer_fits_the_folder_is_dropped_not_guessed(qapp, world):
    shoot, photos, toasts, cull = analysed(qapp, world)
    gone = str(shoot / "deleted.jpg")
    fine = str(shoot / "a.jpg")                                             # not flagged: "restoring" it means nothing
    store.save(str(shoot), cull._review_id, {"filter": "blink", "restored": [gone, fine], "sel": gone, "top": 2,
                                             "applied": {"act": "reject", "ids": [gone, str(shoot / "b.jpg")], "when": "2026-11-13 08:00"}})
    other, again = reopened(qapp, shoot, toasts)
    s = again._s
    assert s["filter"] == "all"                                             # no frame has that flag in this report
    assert s["restored"] == {} and s["sel"] != gone and s["applied"]["ids"] == [str(shoot / "b.jpg")]


def test_clearing_the_results_forgets_the_review(qapp, world):
    shoot, photos, toasts, cull = analysed(qapp, world)
    cull._ai = object()                                                     # "Clear all results" is the real AI Center's button
    cull.setFilter("soft")
    assert wait_for(qapp, lambda: kept_on_disk(shoot) is not None)
    cull.clearRuns()
    assert wait_for(qapp, lambda: kept_on_disk(shoot) is None) and cull._review_id == ""


def test_the_sample_page_keeps_nothing(qapp, app_data):
    photos = PhotoModel(parent=qapp)
    photos.setRows([dict(row, decision="") for row in rows(12)], "/p")
    model = CullModel(photos, memory=True, parent=qapp)
    model.scene("cu-review")
    model.setFilter("soft")
    model.setReviewTop(4)
    pump(qapp, 0.7)
    assert not app_data.exists() and not model._review_timer.isActive()


def test_leaving_a_folder_writes_its_last_change_under_its_own_name(qapp, world, tmp_path):
    shoot, photos, toasts, cull = analysed(qapp, world)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    make_image(elsewhere / "x.jpg", 5)
    cull.setFilter("soft")                                                  # changed a moment before the other folder is opened
    assert cull._review_timer.isActive()
    photos.loadFolder(str(elsewhere))
    assert wait_for(qapp, lambda: not photos.loading)
    assert kept_on_disk(shoot)["filter"] == "soft" and kept_on_disk(elsewhere) is None


# -- the list's scroll position ---------------------------------------------------------------------------------------------------------

def test_the_review_list_goes_to_the_kept_position_and_reports_scrolling(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path)
    window.setWidth(1440)
    window.setHeight(1000)
    context = engine.rootContext()
    photos = context.contextProperty("photos")
    photos.setRows([dict(row, decision="") for row in rows(400)], "/p")
    model = context.contextProperty("cull")
    model.scene("cu-review")
    model._flags = {row["path"]: ("soft", "clipped", "blink")[i % 3] for i, row in enumerate(photos.collect())}
    model.close()
    model._review_top, model._review_top_rev = 37, model._review_top_rev + 1      # as a kept review would set it
    model._rebuild()
    pump(qapp, 0.3)
    model.openReview()
    pump(qapp, 1.0)
    listing = window.findChild(QObject, "cullReviewList")
    assert listing.property("count") > 100
    from PySide6.QtCore import Q_ARG, Q_RETURN_ARG, QMetaObject

    def top_index():
        return QMetaObject.invokeMethod(listing, "indexAt", Q_RETURN_ARG(int), Q_ARG(float, 10.0), Q_ARG(float, float(listing.property("contentY")) + 4.0))

    assert listing.property("contentY") > 0 and top_index() == 37
    shot = os.environ.get("IT_SHOT_DIR")
    if shot:
        window.grabWindow().save(os.path.join(shot, "review_kept_position.png"))
    center = listing.mapToScene(QPointF(100, 150))
    wheel = QWheelEvent(center, QPointF(window.mapToGlobal(center.toPoint())), QPoint(), QPoint(0, -480), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QGuiApplication.sendEvent(window, wheel)
    pump(qapp, 0.9)
    assert model._review_top > 37 and model._review_top == top_index()      # scrolling is told to the model, a moment after it stops
    assert not [w for w in warnings if "ReferenceError" in w or "TypeError" in w or "Required property" in w or "Unable to assign" in w], warnings
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)
