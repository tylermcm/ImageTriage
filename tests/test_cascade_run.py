"""The cascade Analyze run end to end without the UI: generated JPEGs with capture times go in, the ranked-export files come out, and the existing
results loader turns them into buckets. Offscreen, no models (the rankers are fakes)."""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFilter

from image_triage.ai_results import AICullBucket, ai_cull_bucket_for_result, find_ai_result_for_record, load_ai_bundle
from image_triage.ai_workflow import build_ai_workflow_paths
from image_triage.cascade.model import CascadeSettings
from image_triage.cascade.task import CascadeRunSettings, CascadeRunTask, Scorers
from image_triage.models import ImageRecord

BASE_TIME = datetime(2026, 10, 18, 12, 0, 0)


def scene(seed: int, size=(480, 320)) -> Image.Image:
    """A structured picture (shapes with edges) so blur changes sharpness but barely changes the low-frequency hash."""
    rng = random.Random(seed)
    image = Image.new("RGB", size, (rng.randint(60, 140),) * 3)
    draw = ImageDraw.Draw(image)
    for _ in range(40):
        x, y = rng.randint(0, size[0]), rng.randint(0, size[1])
        w, h = rng.randint(20, 120), rng.randint(20, 120)
        draw.rectangle([x, y, x + w, y + h], fill=tuple(rng.randint(30, 220) for _ in range(3)), outline=(0, 0, 0))
    return image


def save(path: Path, image: Image.Image, seconds: float) -> None:
    exif = Image.Exif()
    when = BASE_TIME + timedelta(seconds=seconds)
    exif.get_ifd(0x8769)[0x9003] = when.strftime("%Y:%m:%d %H:%M:%S")
    exif[0x0132] = when.strftime("%Y:%m:%d %H:%M:%S")
    image.save(path, "JPEG", quality=92, exif=exif)


def record(path: Path) -> ImageRecord:
    stat = path.stat()
    return ImageRecord(path=str(path), name=path.name, size=stat.st_size, modified_ns=stat.st_mtime_ns)


@pytest.fixture
def folder(tmp_path):
    """Frames, in capture order: two identical-looking frames then a much blurrier one (one burst), a different scene 30 s later,
    and a pure white frame a minute after that."""
    base = scene(1)
    save(tmp_path / "a1.jpg", base, 0)
    save(tmp_path / "a2.jpg", base.point(lambda v: min(255, v + 1)), 1)
    save(tmp_path / "a3_soft.jpg", base.filter(ImageFilter.GaussianBlur(5)), 2)
    save(tmp_path / "b1.jpg", scene(2), 40)
    save(tmp_path / "w1.jpg", Image.new("RGB", (480, 320), (255, 255, 255)), 120)
    return tmp_path


def run_task(folder: Path, **kwargs):
    paths = build_ai_workflow_paths(folder)
    records = [record(p) for p in sorted(folder.glob("*.jpg"))]
    task = CascadeRunTask(folder=folder, records=records, paths=paths, **kwargs)
    events: dict[str, list[tuple]] = {name: [] for name in ("started", "stage", "progress", "detail", "finished", "failed", "cancelled")}
    for name in events:
        getattr(task.signals, name).connect(lambda *args, _name=name: events[_name].append(args))
    task.setAutoDelete(False)
    task.run()
    return task, events, paths, records


def roles(paths, records):
    bundle = load_ai_bundle(paths.report_dir)
    return {Path(r.path).name: find_ai_result_for_record(bundle, r) for r in records}, bundle


def test_the_run_writes_the_export_files_and_emits_the_same_signals_as_the_old_task(folder):
    task, events, paths, records = run_task(folder)
    assert len(events["started"]) == 1 and len(events["finished"]) == 1 and not events["failed"] and not events["cancelled"]
    folder_text, report_dir, html_path = events["finished"][0]
    assert Path(report_dir) == paths.report_dir and Path(html_path).exists()
    assert [stage[1] for stage in events["stage"]] == [0, 1, 2, 3]
    assert (paths.report_dir / "ranked_clusters_export.csv").exists() and (paths.report_dir / "ranked_export_summary.json").exists()


def test_the_decisions_reach_the_buckets_through_the_existing_loader(folder):
    _, _, paths, records = run_task(folder)
    results, _ = roles(paths, records)
    assert results["a3_soft.jpg"].cascade_role == "rejected" and ai_cull_bucket_for_result(results["a3_soft.jpg"]) is AICullBucket.REJECT
    assert "softer than its siblings" in results["a3_soft.jpg"].cascade_summary
    assert {results["a1.jpg"].cascade_role, results["a2.jpg"].cascade_role} == {"final", "duplicate"}
    kept = results["a1.jpg"] if results["a1.jpg"].cascade_role == "final" else results["a2.jpg"]
    assert ai_cull_bucket_for_result(kept) is AICullBucket.KEEPER
    assert results["b1.jpg"].cascade_role == "final" and results["b1.jpg"].cluster_reason == "A single frame."
    assert results["w1.jpg"].cascade_role == "no_pick" and ai_cull_bucket_for_result(results["w1.jpg"]) is AICullBucket.NEEDS_REVIEW


def test_frames_in_a_burst_share_a_stack_and_far_apart_frames_do_not(folder):
    _, _, paths, records = run_task(folder)
    results, _ = roles(paths, records)
    assert results["a1.jpg"].group_id == results["a2.jpg"].group_id == results["a3_soft.jpg"].group_id
    assert results["b1.jpg"].group_id not in {results["a1.jpg"].group_id, results["w1.jpg"].group_id}
    assert results["a1.jpg"].group_size == 3


def distinct_survivors(folder: Path) -> CascadeRunSettings:
    """Make a2 a shifted copy of a1 and stop the duplicate collapse from merging them, so the stack has two distinct survivors for the rankers."""
    base = scene(1)
    save(folder / "a2.jpg", base.transform(base.size, Image.Transform.AFFINE, (1, 0, 14, 0, 1, 0)), 1)
    return CascadeRunSettings(cascade=CascadeSettings(duplicate_distance=0))


def test_rankers_only_see_survivors_and_order_the_stack(folder):
    seen: list[list[str]] = []

    def factory(cancel, progress):
        def aesthetic(keys):
            seen.append([Path(k).name for k in keys])
            return {k: (1.0 if k.endswith("a2.jpg") else 0.0) for k in keys}

        return Scorers(aesthetic=aesthetic, names=("aesthetic",))

    settings = distinct_survivors(folder)
    task, events, paths, records = run_task(folder, settings=settings, scorer_factory=factory)
    results, _ = roles(paths, records)
    assert not events["failed"]
    assert seen == [["a1.jpg", "a2.jpg"]]  # the soft, flagged frame never reaches a ranker
    assert task.result.scored_by_aesthetic == 2
    assert results["a2.jpg"].cascade_role == "pick" and ai_cull_bucket_for_result(results["a2.jpg"]) is AICullBucket.AI_PICK
    assert results["a1.jpg"].cascade_role == "final" and ai_cull_bucket_for_result(results["a1.jpg"]) is AICullBucket.KEEPER
    assert results["a3_soft.jpg"].cascade_role == "rejected"
    assert "aesthetic" in (paths.report_dir / "ranked_export_summary.json").read_text(encoding="utf-8")


def test_a_ranker_that_cannot_start_is_skipped_with_a_note_instead_of_failing_the_run(folder):
    def factory(cancel, progress):
        raise RuntimeError("no AI runtime installed")

    task, events, paths, records = run_task(folder, scorer_factory=factory)
    assert len(events["finished"]) == 1 and not events["failed"]
    summary = (paths.report_dir / "ranked_export_summary.json").read_text(encoding="utf-8")
    assert "Ranking skipped: no AI runtime installed" in summary
    assert any("Ranking skipped" in args[1] for args in events["detail"])


def test_a_ranker_error_mid_run_falls_back_to_the_cheap_passes(folder):
    class RankerUnavailable(RuntimeError):
        pass

    settings = distinct_survivors(folder)

    def factory(cancel, progress):
        def broken(keys):
            raise RankerUnavailable("weights missing")

        return Scorers(maniqa=broken, aesthetic=broken, names=("maniqa", "aesthetic"))

    task, events, paths, records = run_task(folder, settings=settings, scorer_factory=factory)
    results, _ = roles(paths, records)
    assert len(events["finished"]) == 1 and not events["failed"]
    summary = (paths.report_dir / "ranked_export_summary.json").read_text(encoding="utf-8")
    assert "Ranking skipped: weights missing" in summary and '"ranked_with": []' in summary
    assert results["a1.jpg"].cascade_role == "final" and results["a2.jpg"].cascade_role == "final"  # unranked: both kept, no pick offered
    assert ai_cull_bucket_for_result(results["a1.jpg"]) is AICullBucket.KEEPER


def test_a_winner_the_person_already_marked_is_never_flagged(folder):
    task, events, paths, records = run_task(folder, protected_paths=[str(folder / "a3_soft.jpg")])
    results, _ = roles(paths, records)
    assert results["a3_soft.jpg"].cascade_role == "final" and "already marked" in results["a3_soft.jpg"].cascade_summary


def test_cancelling_before_the_run_emits_cancelled_and_writes_nothing(folder):
    paths = build_ai_workflow_paths(folder)
    task = CascadeRunTask(folder=folder, records=[record(p) for p in sorted(folder.glob("*.jpg"))], paths=paths)
    events = []
    task.signals.cancelled.connect(lambda *args: events.append(args))
    task.signals.finished.connect(lambda *args: events.append(("finished",)))
    task.setAutoDelete(False)
    task.cancel()
    task.run()
    assert events and events[0][0] == str(folder) and not (paths.report_dir / "ranked_clusters_export.csv").exists()


def test_a_second_run_reuses_the_cached_features(folder, monkeypatch):
    run_task(folder)
    import image_triage.cascade.task as task_module

    calls = {"computed": None}
    real = task_module.extract_features

    def spy(paths, **kwargs):
        result = real(paths, **kwargs)
        calls["computed"] = result.stats.computed
        return result

    monkeypatch.setattr(task_module, "extract_features", spy)
    run_task(folder)
    assert calls["computed"] == 0
