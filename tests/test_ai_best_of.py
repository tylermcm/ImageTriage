"""Choosing a folder's best photos from its saved AI report, without analysing it again. The report is written by a cascade run whose
rankers are fakes (no model, runtime or graphics card exists here); the native document is hand-written in its documented shape."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QObject

from image_triage import ai_best_of
from image_triage.ai_best_of import TOP_OVERALL, TOP_PER_PERSON, TOP_PER_STACK, Candidate, candidates_from_bundle, coverage, default_measure, plan
from image_triage.ai_results import load_ai_bundle
from image_triage.cli_culler_results import native_ai_bundle
from image_triage.qml_shell.bestof_model import BestOfModel
from image_triage.qml_shell.collections_model import CollectionsModel
from image_triage.qml_shell.menu_model import ToastModel
from image_triage.qml_shell.photos import PhotoModel
from test_ai_score_details import FakeRanker, ranked_run
from test_cascade_run import folder, save, scene  # noqa: F401
from test_qml_shell import _load_main, pump, qapp  # noqa: F401
from test_qml_shell_inspector_data import find, make_folder, photo_rows, until


def C(name, stack, **values):
    return Candidate(name, stack, values)


POOL = [C("a1", "s1", appeal=.9, quality=.5, sharpness=300), C("a2", "s1", appeal=.7, quality=.6, sharpness=900), C("a3", "s1", sharpness=100),
        C("b1", "s2", appeal=.8, sharpness=50), C("b2", "s2", appeal=.2, sharpness=60), C("c1", "alone:c1", sharpness=10)]


# -- the choice, from recorded scores only --------------------------------------------------------------------------------------------

def test_top_overall_uses_the_recorded_score_and_never_picks_an_unscored_photo():
    found = plan(POOL, TOP_OVERALL, "appeal", 3)
    assert found.paths == ("a1", "b1", "a2") and found.problem == ""
    assert found.note == "2 of 6 photos have no Appeal (Q-ReAlign) value and cannot be chosen."
    assert plan(POOL, TOP_OVERALL, "appeal", 50).paths == ("a1", "b1", "a2", "b2")          # asking for more than exist gives what exists
    assert plan(POOL, TOP_OVERALL, "quality", 1).paths == ("a2",)


def test_top_per_stack_takes_each_stacks_best_and_says_which_stacks_gave_none():
    found = plan(POOL, TOP_PER_STACK, "appeal", 1)
    assert found.paths == ("a1", "b1") and "1 of 3 stacks have no photo with that value" in found.note
    assert plan(POOL, TOP_PER_STACK, "sharpness", 2).paths == ("a2", "a1", "b2", "b1", "c1")    # sharpness orders inside a stack


def test_a_score_that_only_compares_inside_a_stack_cannot_rank_the_folder():
    found = plan(POOL, TOP_OVERALL, "sharpness", 3)
    assert found.paths == () and "only compares the photos of one stack" in found.problem
    assert "only compares" in plan(POOL, TOP_PER_PERSON, "sharpness", 1, people={"Anna": ["a1"]}).problem


def test_top_per_person_needs_people_and_picks_for_each_of_them():
    assert "No people have been found" in plan(POOL, TOP_PER_PERSON, "appeal", 1, people=None).problem
    assert "No people have been found" in plan(POOL, TOP_PER_PERSON, "appeal", 1, people={}).problem
    people = {"Anna": ["a1", "a2", "b2"], "Mark": ["a1", "b1", "gone"], "Unnamed person 7": ["c1", "a3"]}
    found = plan(POOL, TOP_PER_PERSON, "appeal", 1, people=people)
    assert found.paths == ("a1",) and "1 of 3 people have no photo with that value" in found.note      # both share their best photo
    assert plan(POOL, TOP_PER_PERSON, "appeal", 2, people=people).paths == ("a1", "a2", "b1")
    assert "None of the photos with people" in plan(POOL, TOP_PER_PERSON, "appeal", 1, people={"Unnamed person 7": ["c1"]}).problem


def test_what_is_missing_is_said_plainly():
    assert "no saved AI results" in plan([], TOP_OVERALL, "appeal", 3, has_report=False).problem
    assert "do not cover any of the photos shown" in plan([], TOP_OVERALL, "appeal", 3).problem
    bare = [C("x", "s"), C("y", "s")]
    assert "hold no score this can rank by" in plan(bare, TOP_OVERALL, "", 3).problem
    no_quality = plan([C("x", "s", appeal=.4)], TOP_OVERALL, "quality", 3)
    assert no_quality.paths == () and "hold no Quality (MANIQA) values" in no_quality.problem and "quality ranker" in no_quality.problem
    assert coverage(POOL) == {"appeal": 4, "quality": 2, "sharpness": 6, "rank": 0}
    assert default_measure(POOL, TOP_OVERALL) == "appeal" and default_measure([C("x", "s", sharpness=3)], TOP_OVERALL) == ""
    assert default_measure([C("x", "s", sharpness=3)], TOP_PER_STACK) == "sharpness"


# -- from real saved reports ----------------------------------------------------------------------------------------------------------

def test_candidates_come_from_the_saved_cascade_report(folder):
    task, paths, records = ranked_run(folder)
    bundle = load_ai_bundle(paths.report_dir)
    rows = [{"path": r.path, "record": r, "decision": ""} for r in records]
    found = {Path(c.path).name: c for c in candidates_from_bundle(bundle, rows)}
    assert found["a2.jpg"].values["appeal"] == 0.875 and found["a1.jpg"].values["appeal"] == 0.625      # the numbers the ranker returned
    assert "appeal" not in found["a3_soft.jpg"].values and "sharpness" in found["a3_soft.jpg"].values
    assert found["a1.jpg"].stack == found["a2.jpg"].stack != found["b1.jpg"].stack
    assert [Path(p).name for p in plan(list(found.values()), TOP_OVERALL, "appeal", 1).paths] == ["a2.jpg"]
    rows[1]["decision"] = "rej"                                             # a photo the person rejected is not offered
    assert "a2.jpg" not in {Path(c.path).name for c in candidates_from_bundle(bundle, rows)}
    assert candidates_from_bundle(None, rows) == []


def test_a_report_saved_before_scores_were_kept_says_it_has_none(tmp_path):
    shoot = make_folder(tmp_path)                                           # the older ranked report: one blended score per photo
    bundle = load_ai_bundle(shoot / ".image_triage_ai" / "ranker_report")
    found = candidates_from_bundle(bundle, photo_rows(shoot))
    assert found and all(c.values == {} for c in found)
    assert "hold no score this can rank by" in plan(found, TOP_OVERALL, default_measure(found, TOP_OVERALL), 3).problem


def test_the_native_engines_document_gives_its_scores_and_its_own_ranking(tmp_path):
    files = {str(tmp_path / f"{name}.jpg"): item for name, item in {
        "a": {"decision": "keep", "comparison_set": "burst-1", "rank_in_set": 1, "set_size": 2, "scores": {"appeal": .8, "maniqa": .4, "sharpness": 500.0}, "people": [1]},
        "b": {"decision": "reject", "comparison_set": "burst-1", "rank_in_set": 2, "set_size": 2, "scores": {"appeal": .3, "maniqa": .5}, "people": [1, 2]},
        "c": {"decision": "keep", "comparison_set": None, "score": None, "scores": {}}}.items()}
    bundle = native_ai_bundle({"schema": "aiculler-decisions/1", "files": files}, tmp_path / "decisions.json")
    rows = [{"path": path, "record": None, "decision": ""} for path in files]
    found = {Path(c.path).stem: c for c in candidates_from_bundle(bundle, rows)}
    assert found["a"].values == {"appeal": .8, "quality": .4, "sharpness": 500.0, "rank": 1.0} and found["c"].values == {}
    assert found["a"].stack == found["b"].stack == "burst-1" and found["c"].stack.startswith("alone:")
    assert [Path(p).stem for p in plan(list(found.values()), TOP_PER_STACK, "rank", 1).paths] == ["a"]       # rank 1 is the best
    assert "only compares the photos of one stack" in plan(list(found.values()), TOP_OVERALL, "rank", 1).problem


# -- the model ------------------------------------------------------------------------------------------------------------------------

def opened(qapp, folder, records, people=None, collections=False):
    photos = PhotoModel(parent=qapp)
    photos.setRows([{"path": r.path, "name": Path(r.path).stem, "ext": "JPG", "raw": False, "decision": "", "rating": 0, "ai": -1, "record": r} for r in records], str(folder))
    coll = CollectionsModel(photos, memory=True, parent=qapp) if collections else None

    def loader(folder_text, rows):
        from image_triage.qml_shell.bestof_model import load_report

        has, candidates, found_people = load_report(folder_text, rows)
        return has, candidates, people if people is not None else found_people

    toasts = ToastModel(qapp)
    best = BestOfModel(photos, parent=qapp, collections=coll, toasts=toasts, loader=loader)
    best.prepare()
    assert best.aiInfo["state"] == "loading"
    until(qapp, lambda: best.aiInfo["state"] != "loading")
    return photos, best, coll, toasts


def test_the_dialog_is_told_what_the_saved_report_offers(qapp, folder):
    task, paths, records = ranked_run(folder)
    photos, best, coll, toasts = opened(qapp, folder, records)
    info = best.aiInfo
    assert info["ready"] and info["total"] == 5 and info["people"] == 0 and info["why"] == ""
    assert [m["value"] for m in info["measures"]] == ["appeal", "sharpness"]              # MANIQA was never asked: it is not offered
    assert info["measures"][0]["label"] == "Appeal (Q-ReAlign) · 2 of 5 photos"
    assert [a["value"] for a in info["actions"]] == ["show", "win"]                        # no collections here: not offered
    assert best.defaultMeasure(TOP_OVERALL) == "appeal"
    assert best.preview(TOP_OVERALL, "appeal", 1) == {"count": 1, "problem": "", "note": "3 of 5 photos have no Appeal (Q-ReAlign) value and cannot be chosen."}
    assert "No people have been found" in best.preview(TOP_PER_PERSON, "appeal", 1)["problem"]
    assert photos.scopeKind == "" and not photos.canUndo                    # looking changed nothing


def test_marking_the_picks_as_winners_is_one_undo_step(qapp, folder):
    task, paths, records = ranked_run(folder)
    photos, best, coll, toasts = opened(qapp, folder, records)
    assert best.buildFromReport(TOP_OVERALL, "appeal", 2, "win", "")
    decided = {Path(r["path"]).name: r["decision"] for r in photos.all_rows()}
    assert decided == {"a1.jpg": "win", "a2.jpg": "win", "a3_soft.jpg": "", "b1.jpg": "", "w1.jpg": ""}
    assert (photos.scopeKind, photos.shown) == ("best", 2) and "2 marked as Winners" in best.summary and "saved AI results" in best.summary
    assert toasts.items[-1]["title"] == "Best photos chosen"
    photos.set_scope()
    photos.undo()                                                           # one Ctrl+Z takes both back
    assert all(r["decision"] == "" for r in photos.all_rows()) and not photos.canUndo
    best.prepare()
    until(qapp, lambda: best.aiInfo["state"] == "ready")
    photos.setDecision(photos.row_for_path(str(folder / "a2.jpg")), "win")
    assert best.buildFromReport(TOP_OVERALL, "appeal", 1, "win", "")        # already a Winner: it is not toggled off
    assert photos.all_rows()[1]["decision"] == "win" and "already Winners" in best.summary


def test_adding_the_picks_to_a_collection_is_one_undo_step(qapp, folder):
    task, paths, records = ranked_run(folder)
    photos, best, coll, toasts = opened(qapp, folder, records, collections=True)
    cid = coll.pickerEntries()[0]["id"]
    before = coll.get(cid)["count"]
    assert [a["value"] for a in best.aiInfo["actions"]] == ["show", "win", "collection"]
    assert best.buildFromReport(TOP_PER_STACK, "sharpness", 1, "collection", cid)
    assert coll.get(cid)["count"] == before + 3 and "3 added to" in best.summary       # one per stack: the burst, the other scene, the white frame
    photos.set_scope()
    photos.undo()
    assert coll.get(cid)["count"] == before
    assert all(r["decision"] == "" for r in photos.all_rows())               # adding to a collection decided nothing


def test_per_person_uses_the_folders_people(qapp, folder):
    task, paths, records = ranked_run(folder)
    people = {"Anna": [str(folder / "a1.jpg"), str(folder / "a2.jpg")], "Unnamed person 3": [str(folder / "b1.jpg")]}
    photos, best, coll, toasts = opened(qapp, folder, records, people=people)
    assert best.aiInfo["people"] == 2
    found = best.preview(TOP_PER_PERSON, "appeal", 1)
    assert found["count"] == 1 and "1 of 2 people have no photo with that value" in found["note"]
    assert best.buildFromReport(TOP_PER_PERSON, "appeal", 1, "show", "")
    assert photos.shown == 1 and Path(photos.get(0)["path"]).name == "a2.jpg" and not photos.canUndo


def test_a_folder_without_a_saved_report_says_so_and_changes_nothing(qapp, tmp_path):
    shoot = make_folder(tmp_path, report=False)
    photos = PhotoModel(parent=qapp)
    photos.setRows(photo_rows(shoot), str(shoot))
    best = BestOfModel(photos, parent=qapp)
    best.prepare()
    until(qapp, lambda: best.aiInfo["state"] != "loading")
    info = best.aiInfo
    assert info["state"] == "none" and not info["ready"] and "no saved AI results" in info["why"] and info["measures"] == []
    assert "no saved AI results" in best.preview(TOP_OVERALL, "appeal", 3)["problem"]
    assert not best.buildFromReport(TOP_OVERALL, "appeal", 3, "win", "")
    assert photos.scopeKind == "" and not photos.canUndo and all(r["decision"] == "" for r in photos.all_rows())
    best.build("top_n", 3)                                                   # choosing from your own winners still works
    assert (photos.scopeKind, photos.shown) == ("best", 3)


def test_another_folder_forgets_the_last_folders_report(qapp, folder, tmp_path_factory):
    task, paths, records = ranked_run(folder)
    photos, best, coll, toasts = opened(qapp, folder, records)
    other = make_folder(tmp_path_factory.mktemp("other"), report=False)
    photos.setRows(photo_rows(other), str(other))
    photos.folderChanged.emit()
    assert best.aiInfo["state"] == "none" and best.preview(TOP_OVERALL, "appeal", 1)["count"] == 0


# -- the dialog -----------------------------------------------------------------------------------------------------------------------

def test_the_dialog_offers_the_saved_report_and_says_when_there_is_none(qapp, tmp_path, folder):
    task, paths, records = ranked_run(folder)
    engine, window, warnings = _load_main(qapp, tmp_path / "ui")
    window.setWidth(1440)
    window.setHeight(1000)
    context = engine.rootContext()
    photos = context.contextProperty("photos")
    photos.setRows([{"path": r.path, "name": Path(r.path).stem, "ext": "JPG", "raw": False, "decision": "", "rating": 0, "ai": -1, "record": r} for r in records], str(folder))
    best = BestOfModel(photos, parent=qapp, collections=context.contextProperty("collections"), toasts=context.contextProperty("toasts"))
    context.setContextProperty("bestOf", best)
    pump(qapp, 0.3)
    state = window.findChild(QObject, "bestOfDialog")
    ui = engine.singletonInstance("", "UiState") if hasattr(engine, "singletonInstance") else None
    window.setProperty("startupBestOf", True)
    from PySide6.QtCore import Q_ARG, QMetaObject
    QMetaObject.invokeMethod(window, "runCommand", Q_ARG("QVariant", "workflow.best_of"))
    until(qapp, lambda: state.property("shown") and best.aiInfo["state"] == "ready")
    pump(qapp, 0.6)
    assert state.property("source") == "ai" and state.property("measure") == "appeal" and state.property("canBuild")
    assert "would be chosen from the saved results" in find(window, "bestOfPreview").property("text")
    shot = os.environ.get("IT_SHOT_DIR")
    if shot:
        window.grabWindow().save(os.path.join(shot, "best_of_ai.png"))
    state.setProperty("aiStrat", "ai_person")
    pump(qapp, 0.3)
    assert "No people have been found" in find(window, "bestOfPreview").property("text") and not state.property("canBuild")
    if shot:
        window.grabWindow().save(os.path.join(shot, "best_of_no_people.png"))
    state.setProperty("aiStrat", "ai_overall")
    state.setProperty("action", "win")
    state.setProperty("n", 2)
    pump(qapp, 0.2)
    assert find(window, "bestOfBuild").property("text") == "Mark as Winners"
    QMetaObject.invokeMethod(state, "build")
    pump(qapp, 0.4)
    assert not state.property("shown") and sum(1 for r in photos.all_rows() if r["decision"] == "win") == 2 and photos.scopeKind == "best"
    assert not [w for w in warnings if "ReferenceError" in w or "TypeError" in w or "Required property" in w or "Unable to assign" in w], warnings
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)


def test_the_dialog_without_a_report_falls_back_to_your_winners(qapp, tmp_path):
    from PySide6.QtCore import Q_ARG, QMetaObject

    engine, window, warnings = _load_main(qapp, tmp_path)
    window.setWidth(1440)
    window.setHeight(1000)
    best = engine.rootContext().contextProperty("bestOf")
    QMetaObject.invokeMethod(window, "runCommand", Q_ARG("QVariant", "workflow.best_of"))
    state = window.findChild(QObject, "bestOfDialog")
    until(qapp, lambda: state.property("shown") and best.aiInfo["state"] != "loading")
    pump(qapp, 0.5)
    assert state.property("source") == "mine" and find(window, "bestOfNoReportHint").property("visible")
    state.setProperty("source", "ai")
    pump(qapp, 0.3)
    assert find(window, "bestOfNoReport").property("visible") and "no saved AI results" in state.property("ai")["why"] and not state.property("canBuild")
    shot = os.environ.get("IT_SHOT_DIR")
    if shot:
        window.grabWindow().save(os.path.join(shot, "best_of_no_report.png"))
    assert not [w for w in warnings if "ReferenceError" in w or "TypeError" in w or "Required property" in w or "Unable to assign" in w], warnings
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)
