"""The eyes-closed signal: the pure decision from blendshape scores, RAW-aware image loading in the worker, and how the run task uses a blink scorer."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from image_triage.ai_results import AICullBucket, ai_cull_bucket_for_result
from image_triage.cascade.blink import BLINK_LEFT, BLINK_RIGHT, BLINK_THRESHOLD, MIN_FACE_AREA, eyes_closed, face_area, frame_blink_score, is_blink
from image_triage.cascade.rankers import SPECS, worker
from image_triage.cascade.task import Scorers
from tests.test_cascade_run import roles, run_task, save, scene


def shapes(left: float, right: float) -> dict[str, float]:
    return {BLINK_LEFT: left, BLINK_RIGHT: right, "mouthSmileLeft": 0.9}


# -- the decision ----------------------------------------------------------------------------------------------------------------------


def test_eyes_closed_is_the_mean_of_both_lids_and_needs_both_to_be_reported():
    assert eyes_closed(shapes(0.8, 0.4)) == pytest.approx(0.6)
    assert eyes_closed({BLINK_LEFT: 0.9}) is None
    assert eyes_closed({}) is None


def test_a_face_counts_by_the_area_its_landmarks_cover():
    assert face_area([(0.4, 0.3), (0.6, 0.3), (0.6, 0.7), (0.4, 0.7)]) == pytest.approx(0.08)
    assert face_area([]) == 0.0
    assert face_area([(0.5, 0.5)]) == 0.0


def test_the_frame_score_is_the_most_closed_prominent_face():
    faces = [(0.20, shapes(0.1, 0.1)), (0.05, shapes(0.9, 0.9)), (0.002, shapes(1.0, 1.0))]
    assert frame_blink_score(faces) == pytest.approx(0.9)  # the tiny background face (0.2% of the frame) is ignored


def test_no_face_or_no_blendshapes_is_not_a_blink():
    assert frame_blink_score([]) == 0.0
    assert frame_blink_score([(0.3, {})]) == 0.0
    assert not is_blink(frame_blink_score([(MIN_FACE_AREA / 2, shapes(1.0, 1.0))]))


def test_the_threshold_is_conservative_and_unreadable_frames_are_never_blinks():
    assert is_blink(BLINK_THRESHOLD) and is_blink(0.95)
    assert not is_blink(0.59) and not is_blink(0.3)  # looking down lowers the lids but scores well below the line
    assert not is_blink(float("-inf"))


def test_the_blink_worker_is_registered_with_the_same_machinery_as_the_rankers():
    assert "blink" in SPECS and "blink" in worker.ENGINES
    assert SPECS["blink"].bundle_key == "faces_blink"


# -- the worker reads RAW frames through their embedded preview ------------------------------------------------------------------------


def test_the_worker_opens_a_raw_through_its_embedded_jpeg_and_bounds_it(tmp_path, monkeypatch):
    from image_triage.raw_embedded_jpeg import EmbeddedJpeg

    jpeg = io.BytesIO()
    Image.new("RGB", (1600, 1200), (200, 30, 30)).save(jpeg, "JPEG")
    raw = tmp_path / "frame.nef"
    raw.write_bytes(b"not a decodable raw")
    monkeypatch.setattr("image_triage.raw_embedded_jpeg.extract_embedded_jpeg", lambda path: EmbeddedJpeg(jpeg.getvalue(), "test", 0, len(jpeg.getvalue()), 1))
    image = worker.load_image(str(raw), 800)
    assert image.size == (800, 600) and image.getpixel((10, 10))[0] > 150


def test_the_worker_applies_the_containers_orientation_to_a_raw_preview(tmp_path, monkeypatch):
    from image_triage.raw_embedded_jpeg import EmbeddedJpeg

    jpeg = io.BytesIO()
    Image.new("RGB", (800, 400), (10, 120, 10)).save(jpeg, "JPEG")
    raw = tmp_path / "portrait.nef"
    raw.write_bytes(b"x")
    monkeypatch.setattr("image_triage.raw_embedded_jpeg.extract_embedded_jpeg", lambda path: EmbeddedJpeg(jpeg.getvalue(), "test", 0, len(jpeg.getvalue()), 6))
    assert worker.load_image(str(raw), 4000).size == (400, 800)  # orientation 6 turns a landscape preview upright


def test_a_raw_without_a_preview_is_an_unreadable_frame_not_a_crash(tmp_path, monkeypatch):
    raw = tmp_path / "empty.nef"
    raw.write_bytes(b"x")
    monkeypatch.setattr("image_triage.raw_embedded_jpeg.extract_embedded_jpeg", lambda path: None)
    with pytest.raises(OSError):
        worker.load_image(str(raw), 800)


def test_plain_images_are_still_opened_directly(tmp_path):
    path = tmp_path / "plain.jpg"
    Image.new("RGB", (640, 480), (1, 2, 3)).save(path)
    assert worker.load_image(str(path), 320).size == (320, 240)


# -- the run task ----------------------------------------------------------------------------------------------------------------------


@pytest.fixture
def folder(tmp_path):
    base = scene(1)
    save(tmp_path / "a1.jpg", base, 0)
    save(tmp_path / "a2.jpg", base.transform(base.size, Image.Transform.AFFINE, (1, 0, 14, 0, 1, 0)), 1)
    save(tmp_path / "b1.jpg", scene(2), 40)
    return tmp_path


def blink_factory(closed: set[str], calls: list | None = None):
    def factory(cancel, progress):
        def blink(keys):
            if calls is not None:
                calls.append(sorted(Path(k).name for k in keys))
            return {k: (0.95 if Path(k).name in closed else 0.02) for k in keys}

        return Scorers(blink=blink)

    return factory


def test_a_frame_the_blink_scorer_calls_closed_is_flagged_with_the_reason(folder):
    task, events, paths, records = run_task(folder, scorer_factory=blink_factory({"a1.jpg"}))
    results, _ = roles(paths, records)
    assert not events["failed"]
    assert results["a1.jpg"].cascade_role == "rejected" and results["a1.jpg"].triggered_tags == "blink"
    assert "eyes closed" in results["a1.jpg"].cascade_summary
    assert ai_cull_bucket_for_result(results["a1.jpg"]) is AICullBucket.REJECT
    assert results["a2.jpg"].cascade_role == "final" and results["b1.jpg"].cascade_role == "final"
    assert '"blink_checked": true' in (paths.report_dir / "ranked_export_summary.json").read_text(encoding="utf-8")


def test_every_readable_frame_is_sent_to_the_blink_scorer_once(folder):
    calls: list = []
    run_task(folder, scorer_factory=blink_factory(set(), calls))
    assert calls == [["a1.jpg", "a2.jpg", "b1.jpg"]]


def test_a_blink_scorer_that_fails_is_skipped_with_a_note_and_the_run_still_finishes(folder):
    class RankerWorkerError(RuntimeError):
        pass

    def factory(cancel, progress):
        def broken(keys):
            raise RankerWorkerError("mediapipe is not installed in the AI runtime")

        return Scorers(blink=broken)

    task, events, paths, records = run_task(folder, scorer_factory=factory)
    assert len(events["finished"]) == 1 and not events["failed"]
    summary = (paths.report_dir / "ranked_export_summary.json").read_text(encoding="utf-8")
    assert "Eyes-closed check skipped: mediapipe is not installed in the AI runtime" in summary and '"blink_checked": false' in summary
    results, _ = roles(paths, records)
    assert all(r.cascade_role != "rejected" for r in results.values())


def test_an_unreadable_frame_scores_minus_infinity_and_is_not_flagged(folder):
    def factory(cancel, progress):
        return Scorers(blink=lambda keys: {k: float("-inf") for k in keys})

    task, events, paths, records = run_task(folder, scorer_factory=factory)
    results, _ = roles(paths, records)
    assert all(r.cascade_role != "rejected" for r in results.values())


# -- the score cache -------------------------------------------------------------------------------------------------------------------


def test_a_second_run_reuses_cached_blink_scores_and_only_new_or_changed_files_are_scored(folder):
    calls: list = []
    run_task(folder, scorer_factory=blink_factory({"a1.jpg"}, calls))
    assert calls == [["a1.jpg", "a2.jpg", "b1.jpg"]]

    calls.clear()
    _, _, paths, records = run_task(folder, scorer_factory=blink_factory({"a1.jpg"}, calls))
    assert calls == []  # nothing changed, so the face model never runs
    results, _ = roles(paths, records)
    assert results["a1.jpg"].cascade_role == "rejected"  # the cached score still flags the frame

    save(folder / "b1.jpg", scene(9), 40)  # a changed file is scored again, the others are not
    calls.clear()
    run_task(folder, scorer_factory=blink_factory(set(), calls))
    assert calls == [["b1.jpg"]]


def test_the_cache_is_dropped_when_the_model_changes_and_ignores_unreadable_frames(tmp_path):
    from image_triage.cascade.blink import BlinkScoreCache

    frame_path = tmp_path / "f.jpg"
    frame_path.write_bytes(b"x")
    cache_file = tmp_path / "cache.json"
    cache = BlinkScoreCache(cache_file, "model-a")
    cache.put(str(frame_path), 0.42)
    cache.put(str(tmp_path / "bad.jpg"), float("-inf"))
    cache.save()
    assert BlinkScoreCache(cache_file, "model-a").get(str(frame_path)) == pytest.approx(0.42)
    assert BlinkScoreCache(cache_file, "model-b").get(str(frame_path)) is None
    assert BlinkScoreCache(cache_file, "model-a").get(str(tmp_path / "bad.jpg")) is None


def test_a_corrupt_cache_file_is_treated_as_empty(tmp_path):
    from image_triage.cascade.blink import BlinkScoreCache

    cache_file = tmp_path / "cache.json"
    cache_file.write_text("{not json", encoding="utf-8")
    frame_path = tmp_path / "f.jpg"
    frame_path.write_bytes(b"x")
    assert BlinkScoreCache(cache_file, "m").get(str(frame_path)) is None
