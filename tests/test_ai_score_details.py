"""The numbers behind a photo's AI outcome: what the cascade keeps in its saved report, what an older report or the native engine's
document holds, and what the inspector and the cull review show of it. The rankers and the eyes check are fakes (no model, runtime or
graphics card exists here); the native engine's document is a hand-written one in its documented shape."""
from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtCore import QObject

from image_triage.ai_results import find_ai_result_for_record, load_ai_bundle
from image_triage.ai_score_details import NOT_RECORDED, details_cascade, details_for_result, details_native, device_text
from image_triage.cascade.export import CSV_COLUMNS, MEASURES
from image_triage.cascade.task import Scorers
from image_triage.qml_shell.ai_scores import AiScores
from image_triage.qml_shell.cull_real import outcomes_from_bundle, outcomes_from_result
from image_triage.qml_shell.photos import PhotoModel
from test_cascade_run import distinct_survivors, folder, record, roles, run_task  # noqa: F401
from test_qml_shell import _load_main, pump, qapp  # noqa: F401
from test_qml_shell_inspector_data import find, make_folder, photo_rows, until


class FakeRanker:
    """A ranker whose worker says where it runs. Scores are a fixed table, so the saved numbers can be compared exactly."""

    def __init__(self, table: dict[str, float], device: str = "cuda") -> None:
        self.table, self.active_device, self.asked = table, device, []

    def __call__(self, keys):
        self.asked.append([Path(key).name for key in keys])
        return {key: self.table[Path(key).name] for key in keys}


def ranked_run(folder, **scorers):
    quality = FakeRanker({"a1.jpg": 0.41234, "a2.jpg": 0.5, "b1.jpg": 0.3})
    appeal = FakeRanker({"a1.jpg": 0.625, "a2.jpg": 0.875})
    blink = lambda keys: {key: (0.07 if key.endswith("a1.jpg") else 0.0) for key in keys}
    made = {"maniqa": quality, "aesthetic": appeal, "blink": blink, **scorers}
    names = tuple(name for name, key in (("MANIQA", "maniqa"), ("Q-ReAlign", "aesthetic")) if made.get(key) is not None)
    task, events, paths, records = run_task(folder, settings=distinct_survivors(folder), scorer_factory=lambda cancel, progress: Scorers(names=names, **made))
    assert not events["failed"], events["failed"]
    return task, paths, records


def by_label(details):
    return {row["k"]: row for row in details["rows"]}


# -- what the cascade keeps ----------------------------------------------------------------------------------------------------------

def test_the_report_keeps_the_scores_the_rankers_really_returned(folder):
    task, paths, records = ranked_run(folder)
    results, bundle = roles(paths, records)
    summary = json.loads((paths.report_dir / "ranked_export_summary.json").read_text(encoding="utf-8"))
    assert summary["report_version"] == 2 and summary["ranking_device"] == "cuda" and summary["blink_threshold"] == task.settings.blink_threshold
    a2 = dict(results["a2.jpg"].measures)
    assert a2["aesthetic_score"] == 0.875                                  # exactly what the ranker said, not a percentile or a role constant
    assert "maniqa_score" not in a2                                        # two survivors: the quality ranker was never asked about this stack
    assert dict(results["a1.jpg"].measures)["blink_score"] == 0.07
    soft = dict(results["a3_soft.jpg"].measures)
    assert "aesthetic_score" not in soft and "maniqa_score" not in soft    # flagged by the cleanup: no ranker read it, so no score exists
    assert soft["centre_sharpness"] < a2["centre_sharpness"] and 0 <= soft["clipped_fraction"] <= 1
    assert dict(results["w1.jpg"].measures)["clipped_fraction"] == 1.0
    assert results["a2.jpg"].decided_by == "appeal_ranker" and results["a3_soft.jpg"].decided_by == "cleanup"
    assert results["b1.jpg"].decided_by == "unranked"
    assert task.measures[str(folder / "a2.jpg")]["aesthetic_score"] == 0.875


def test_the_details_label_each_value_with_its_scale_and_say_what_was_not_recorded(folder):
    task, paths, records = ranked_run(folder)
    results, bundle = roles(paths, records)
    pick = details_for_result(results["a2.jpg"], bundle.summary)
    rows = by_label(pick)
    assert rows["Appeal (Q-ReAlign)"]["v"] == "0.875" and rows["Appeal (Q-ReAlign)"]["unit"] == "0 to 1" and "higher is better" in rows["Appeal (Q-ReAlign)"]["scale"]
    assert rows["Quality (MANIQA)"]["v"] == NOT_RECORDED and not rows["Quality (MANIQA)"]["recorded"] and "survive" in rows["Quality (MANIQA)"]["scale"]
    assert rows["Eyes closed"]["v"] == "0.00" and "Flagged from" in rows["Eyes closed"]["scale"] and rows["Eyes closed"]["unit"] == "0 open, 1 closed"
    assert rows["Centre sharpness"]["recorded"] and "No fixed scale" in rows["Centre sharpness"]["scale"]
    assert rows["Clipped highlights"]["v"].endswith("%")
    assert pick["stage"] == "Appeal ranker (Q-ReAlign)" and pick["device"] == "Graphics card"
    soft = details_for_result(results["a3_soft.jpg"], bundle.summary)
    assert by_label(soft)["Appeal (Q-ReAlign)"]["v"] == NOT_RECORDED and soft["stage"].startswith("Cleanup pass")


def test_a_run_without_rankers_says_so_instead_of_showing_a_score(folder):
    task, paths, records = ranked_run(folder, maniqa=None, aesthetic=None, blink=None)
    results, bundle = roles(paths, records)
    assert bundle.summary["ranking_device"] == "" and bundle.summary["blink_threshold"] is None
    found = details_for_result(results["a1.jpg"], bundle.summary)
    rows = by_label(found)
    assert rows["Quality (MANIQA)"]["v"] == NOT_RECORDED and "did not run" in rows["Quality (MANIQA)"]["scale"]
    assert rows["Eyes closed"]["v"] == NOT_RECORDED and "did not run for this analysis" in rows["Eyes closed"]["scale"]
    assert rows["Centre sharpness"]["recorded"] and found["device"] == "No ranker ran"


def test_scores_of_a_ranking_that_failed_part_way_are_not_kept(folder):
    class Dies(FakeRanker):
        def __call__(self, keys):
            super().__call__(keys)
            raise type("RankerWorkerError", (RuntimeError,), {})("the worker died")

    quality = FakeRanker({"a1.jpg": 0.4, "a2.jpg": 0.5})
    task, paths, records = ranked_run(folder, maniqa=quality, aesthetic=Dies({"a1.jpg": 0.6, "a2.jpg": 0.8}))
    results, bundle = roles(paths, records)
    assert any("Ranking skipped" in note for note in task.notes) and task.ranked_with == ()
    assert all(not {"maniqa_score", "aesthetic_score"} & set(dict(result.measures)) for result in results.values())
    assert details_for_result(results["a2.jpg"], bundle.summary)["stage"] == "Cleanup passes only (no ranker compared it)"


def test_an_older_report_still_loads_and_claims_nothing(folder):
    task, paths, records = ranked_run(folder)
    report = paths.report_dir / "ranked_clusters_export.csv"
    with report.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    old_columns = [column for column in CSV_COLUMNS if column not in MEASURES and column != "decided_by"]
    with report.open("w", encoding="utf-8", newline="") as handle:        # the file as it was written before version 2
        writer = csv.DictWriter(handle, fieldnames=old_columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    summary_path = paths.report_dir / "ranked_export_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    for key in ("report_version", "ranking_device", "blink_threshold"):
        summary.pop(key)
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    results, bundle = roles(paths, records)
    assert results["a2.jpg"].cascade_role == "pick" and results["a2.jpg"].measures == () and results["a2.jpg"].decided_by == ""
    found = details_for_result(results["a2.jpg"], bundle.summary)
    assert all(row["v"] == NOT_RECORDED and not row["recorded"] and "before scores were kept" in row["scale"] for row in found["rows"])
    assert found["stage"] == NOT_RECORDED and found["device"] == NOT_RECORDED


def test_values_that_are_not_numbers_are_never_shown():
    found = details_cascade({}, "", {"report_version": 2, "ranked_with": ["MANIQA"], "blink_checked": True})
    assert [row["v"] for row in found["rows"]] == [NOT_RECORDED] * 4 and found["device"] == NOT_RECORDED
    assert device_text("cuda:0", "RTX 4070") == "Graphics card (RTX 4070)" and device_text("cpu") == "CPU" and device_text("") == ""


# -- the native engine's document ----------------------------------------------------------------------------------------------------

NATIVE_ITEM = {"decision": "reject", "capture": "c1", "comparison_set": "burst-3", "rank_in_set": 2, "set_size": 3, "score": 0.412,
               "scores": {"maniqa": 0.38, "maniqa_rel": 0.1, "appeal": None, "sharpness": 812.4, "qra_iqa": 0.55, "novel_model": 12.5, "label": "text"},
               "reasons": ["Rejected: a better frame of this burst was kept"]}


def test_native_scores_are_shown_under_the_engines_names_and_scale():
    found = details_native(NATIVE_ITEM, {"device": "cuda", "gpu": "RTX 4080 SUPER"})
    rows = by_label(found)
    assert rows["Quality (MANIQA)"]["v"] == "0.380" and rows["Quality (MANIQA)"]["unit"] == "0 to 1"
    assert rows["Appeal (Q-ReAlign)"]["v"] == NOT_RECORDED and rows["Eyes closed"]["v"] == NOT_RECORDED
    assert rows["Sharpness"]["v"] == "812" and rows["Sharpness"]["unit"] == "no fixed scale" and rows["Image quality (Q-ReAlign)"]["v"] == "0.550"
    assert rows["novel_model"]["v"] == "12.500" and rows["novel_model"]["unit"] == "engine scale" and "label" not in rows      # an unknown score is shown as given; text is not a score
    assert "maniqa_rel" not in rows                                              # the engine's working value within the set is not listed as a score
    assert rows["Engine score"]["v"] == "0.412" and "engine's own scale" in rows["Engine score"]["scale"]
    assert found["stage"] == "Rejected: a better frame of this burst was kept" and found["device"] == "Graphics card (RTX 4080 SUPER)"
    bare = details_native({"decision": "keep", "score": None})
    assert all(row["v"] == NOT_RECORDED for row in bare["rows"]) and bare["stage"] == NOT_RECORDED and bare["device"] == NOT_RECORDED


def test_the_device_the_native_worker_reported_is_kept_with_the_generation(tmp_path):
    photo = str(tmp_path / "a.jpg")
    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps({"schema": "aiculler-decisions/1", "files": {photo: NATIVE_ITEM}}), encoding="utf-8")
    digest = hashlib.sha256(decisions.read_bytes()).hexdigest()
    active = tmp_path / "active.json"
    descriptor = {"schema": "image-triage-culler-run/1", "outputs": {"decisions": {"path": str(decisions), "sha256": digest}, "index": None}}
    active.write_text(json.dumps(descriptor), encoding="utf-8")
    bundle = load_ai_bundle(active)                                        # a generation activated before the device was kept
    assert "host_runtime" not in bundle.summary
    assert outcomes_from_bundle(bundle, [])[photo].details["device"] == NOT_RECORDED
    active.write_text(json.dumps({**descriptor, "runtime": {"device": "cpu", "torch_version": "2.9.0+cpu"}}), encoding="utf-8")
    bundle = load_ai_bundle(active)
    outcome = outcomes_from_bundle(bundle, [])[photo]
    assert outcome.details["device"] == "CPU" and by_label(outcome.details)["Quality (MANIQA)"]["v"] == "0.380"


# -- what the inspector and the cull review read ---------------------------------------------------------------------------------------

def test_the_inspector_gets_the_recorded_values_for_the_selected_photo(qapp, folder):
    task, paths, records = ranked_run(folder)
    photos = PhotoModel(parent=qapp)
    scores = AiScores(photos, parent=qapp, loader=lambda _folder: load_ai_bundle(paths.report_dir))
    photos.setRows([{"path": r.path, "name": Path(r.path).stem, "ext": "JPG", "raw": False, "decision": "", "rating": 0, "ai": -1} for r in records], str(folder))
    until(qapp, lambda: scores.get(str(folder / "a2.jpg")).get("has"))
    info = scores.get(str(folder / "a2.jpg"))
    rows = {row["k"]: row for row in info["details"]}
    assert rows["Appeal (Q-ReAlign)"]["v"] == "0.875" and info["stage"] == "Appeal ranker (Q-ReAlign)" and info["device"] == "Graphics card"


def test_a_report_of_the_older_kind_shows_not_recorded_in_the_inspector(qapp, tmp_path):
    shoot = make_folder(tmp_path)                                          # the pre-cascade ranked report: one blended score, nothing per model
    photos = PhotoModel(parent=qapp)
    scores = AiScores(photos, parent=qapp)
    photos.setRows(photo_rows(shoot), str(shoot))
    until(qapp, lambda: scores.get(str(shoot / "A.jpg")).get("has"))
    info = scores.get(str(shoot / "A.jpg"))
    assert info["details"] and all(row["v"] == NOT_RECORDED for row in info["details"]) and info["device"] == NOT_RECORDED


def test_the_review_detail_has_the_same_values_live_and_from_the_saved_report(folder):
    task, paths, records = ranked_run(folder)
    summary = {"report_version": 2, "ranked_with": list(task.ranked_with), "ranking_device": task.ranking_device, "blink_checked": True,
               "blink_threshold": task.settings.blink_threshold}
    live = outcomes_from_result(task.result, task.measures, summary)
    bundle = load_ai_bundle(paths.report_dir)
    saved = outcomes_from_bundle(bundle, [{"path": r.path, "record": r} for r in records])
    for path in live:
        assert live[path].details == saved[path].details, path
    assert outcomes_from_result(task.result)[str(folder / "a2.jpg")].details is None      # nothing measured was handed in: nothing is made up


# -- the windows ---------------------------------------------------------------------------------------------------------------------

def test_the_inspector_and_the_review_card_show_the_rows(qapp, tmp_path, folder):
    from image_triage.qml_shell.cull_model import CullModel
    from image_triage.qml_shell.menu_model import ToastModel

    task, paths, records = ranked_run(folder)
    engine, window, warnings = _load_main(qapp, tmp_path / "ui")
    window.setWidth(1440)
    window.setHeight(1000)
    context = engine.rootContext()
    photos = context.contextProperty("photos")
    scores = context.contextProperty("aiScores")
    rows = [{"path": r.path, "name": Path(r.path).stem, "ext": "JPG", "raw": False, "decision": "", "rating": 0, "ai": -1, "record": r} for r in records]
    photos.setRows(rows, str(folder))
    bundle = load_ai_bundle(paths.report_dir)
    scores._arrived(scores._token, bundle, {})
    inspector = find(window, "inspector")
    inspector.setProperty("tab", 2)
    window.findChild(QObject, "photoGrid").setProperty("currentIndex", 1)                    # a2, the pick
    pump(qapp, 0.8)
    section = find(window, "aiScoreDetails")
    assert section is not None and section.property("visible") and len(section.property("rows")) == 6
    assert find(window, "aiScoreStage").property("text") == "Appeal ranker (Q-ReAlign)" and find(window, "aiScoreDevice").property("text") == "Graphics card"
    shot = os.environ.get("IT_SHOT_DIR")
    if shot:
        window.grabWindow().save(os.path.join(shot, "inspector_scores.png"))

    cull = CullModel(photos, memory=False, parent=qapp, toasts=ToastModel(qapp))
    cull._ai = None
    cull._saved_arrived(cull._token, outcomes_from_bundle(bundle, rows))
    context.setContextProperty("cull", cull)
    cull.openReview()
    cull.pick(str(folder / "a2.jpg"))
    pump(qapp, 0.9)
    det = cull.view["det"]
    assert {row["k"]: row["v"] for row in det["scores"]}["Appeal (Q-ReAlign)"] == "0.875" and det["device"] == "Graphics card"
    card = find(window, "cullScoreDetails")
    assert card is not None and card.property("visible")
    if shot:
        window.grabWindow().save(os.path.join(shot, "review_scores.png"))
    assert not [w for w in warnings if "ReferenceError" in w or "TypeError" in w or "Required property" in w or "Unable to assign" in w], warnings
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)


def test_the_scripted_sample_shows_no_scores(qapp):
    from image_triage.qml_shell.cull_model import CullModel

    model = CullModel(PhotoModel(parent=qapp), memory=True, parent=qapp)
    assert model.view["det"]["scores"] == [] and model._score_view("anything") == {"scores": [], "stage": "", "device": ""}
