"""Characterization of the batch rename / resize / convert planners and appliers (WI-0.5)."""
from __future__ import annotations

import os
from pathlib import Path

from PIL import Image

from image_triage.batch_rename import BatchRenameRules, RenameCaseMode, build_batch_rename_preview
from image_triage.file_ops import rename_paths
from image_triage.image_convert import (
    ConvertOptions,
    ConvertSourceItem,
    apply_convert_plan,
    build_convert_plan,
)
from image_triage.image_resize import (
    ResizeOptions,
    ResizeSourceItem,
    apply_resize_plan,
    build_resize_plan,
)
from image_triage.models import SortMode
from image_triage.scanner import scan_folder
from image_triage.models import sort_records
from tests.harness import make_jpegs


def _records(folder):
    return sort_records(scan_folder(str(folder)), SortMode.NAME)


def _listing(folder) -> list[str]:
    return sorted(p.name for p in Path(folder).iterdir() if p.is_file())


# ---- rename ---------------------------------------------------------------

def test_rename_preview_is_pure_and_does_not_touch_files(tmp_path) -> None:
    make_jpegs(tmp_path, ["a.jpg", "b.jpg"])
    preview = build_batch_rename_preview(_records(tmp_path), BatchRenameRules(prefix="trip_"))

    assert [item.target_name for item in preview.items] == ["trip_a.jpg", "trip_b.jpg"]
    assert preview.can_apply and preview.renamed_count == 2
    assert _listing(tmp_path) == ["a.jpg", "b.jpg"]


def test_rename_apply_renames_the_files(tmp_path) -> None:
    make_jpegs(tmp_path, ["a.jpg", "b.jpg"])
    preview = build_batch_rename_preview(_records(tmp_path), BatchRenameRules(prefix="trip_"))

    applied = rename_paths(preview.planned_moves)

    assert len(applied) == 2
    assert _listing(tmp_path) == ["trip_a.jpg", "trip_b.jpg"]


def test_rename_sequence_numbers_follow_record_order(tmp_path) -> None:
    make_jpegs(tmp_path, ["a.jpg", "b.jpg", "c.jpg"])
    rules = BatchRenameRules(new_name="shot", sequence_enabled=True, sequence_start=5, sequence_padding=3)
    preview = build_batch_rename_preview(_records(tmp_path), rules)

    assert [item.target_name for item in preview.items] == ["shot_005.jpg", "shot_006.jpg", "shot_007.jpg"]


def test_rename_case_mode_upper(tmp_path) -> None:
    make_jpegs(tmp_path, ["photo.jpg"])
    upper = build_batch_rename_preview(_records(tmp_path), BatchRenameRules(case_mode=RenameCaseMode.UPPER))

    assert upper.items[0].target_name == "PHOTO.jpg"


def test_rename_with_no_rules_still_reports_rename_but_touches_nothing(tmp_path) -> None:
    # Quirk: identical source/target is labelled "Rename" and counted; rename_paths filters it out.
    make_jpegs(tmp_path, ["photo.jpg"])
    preview = build_batch_rename_preview(_records(tmp_path), BatchRenameRules())

    assert preview.items[0].status == "Rename" and preview.items[0].target_name == "photo.jpg"
    assert rename_paths(preview.planned_moves) == ()
    assert _listing(tmp_path) == ["photo.jpg"]


def test_rename_collision_with_an_existing_file_blocks_the_batch(tmp_path) -> None:
    make_jpegs(tmp_path, ["a.jpg", "taken.jpg"])
    records = [r for r in _records(tmp_path) if r.name == "a.jpg"]
    preview = build_batch_rename_preview(records, BatchRenameRules(new_name="taken"))

    assert preview.error_count == 1 and not preview.can_apply
    assert "already exists" in preview.items[0].message


def test_rename_two_items_to_one_name_blocks_the_batch(tmp_path) -> None:
    make_jpegs(tmp_path, ["a.jpg", "b.jpg"])
    preview = build_batch_rename_preview(_records(tmp_path), BatchRenameRules(new_name="same"))

    assert preview.error_count == 2 and not preview.can_apply
    assert _listing(tmp_path) == ["a.jpg", "b.jpg"]


def test_rename_bundle_keeps_raw_and_jpeg_pair_together(tmp_path) -> None:
    make_jpegs(tmp_path, ["img.jpg"])
    (tmp_path / "img.nef").write_bytes(b"raw")
    records = _records(tmp_path)
    preview = build_batch_rename_preview(records, BatchRenameRules(prefix="x_"))
    rename_paths(preview.planned_moves)

    assert _listing(tmp_path) == ["x_img.jpg", "x_img.nef"]


def test_rename_paths_refuses_to_overwrite_an_outside_file(tmp_path) -> None:
    from image_triage.file_ops import FileMove

    make_jpegs(tmp_path, ["a.jpg", "b.jpg"])
    try:
        rename_paths((FileMove(str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")),))
    except FileExistsError:
        pass
    else:
        raise AssertionError("expected FileExistsError")
    assert _listing(tmp_path) == ["a.jpg", "b.jpg"]


# ---- resize ---------------------------------------------------------------

def test_resize_plan_reports_and_apply_writes_a_copy_by_default(tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"], size=(400, 200))
    options = ResizeOptions(preset_key="custom", custom_width=100, custom_height=100)
    plan = build_resize_plan([ResizeSourceItem(src, "a.jpg")], options)

    assert plan.can_apply and plan.executable_count == 1
    assert _listing(tmp_path) == ["a.jpg"], "planning must not write"
    (written,) = apply_resize_plan(plan, options)

    assert written != src and os.path.exists(src)
    with Image.open(written) as out:
        assert max(out.size) <= 100
    with Image.open(src) as original:
        assert original.size == (400, 200)


def test_resize_overwrite_replaces_the_original(tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"], size=(400, 200))
    options = ResizeOptions(preset_key="custom", custom_width=100, custom_height=100, overwrite=True)
    plan = build_resize_plan([ResizeSourceItem(src, "a.jpg")], options)
    (written,) = apply_resize_plan(plan, options)

    assert written == src
    with Image.open(src) as out:
        assert max(out.size) <= 100
    assert _listing(tmp_path) == ["a.jpg"]


def test_resize_shrink_only_never_enlarges(tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"], size=(50, 40))
    options = ResizeOptions(preset_key="custom", custom_width=400, custom_height=400, shrink_only=True)
    plan = build_resize_plan([ResizeSourceItem(src, "a.jpg")], options)
    written = apply_resize_plan(plan, options)

    for path in written:
        with Image.open(path) as out:
            assert out.size == (50, 40)


def test_resize_invalid_bounds_produce_an_error_plan(tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"])
    plan = build_resize_plan([ResizeSourceItem(src, "a.jpg")], ResizeOptions(preset_key="custom", custom_width=0, custom_height=0))

    assert not plan.can_apply and plan.general_error


# ---- convert --------------------------------------------------------------

def test_convert_writes_a_new_format_and_keeps_the_original(tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"])
    options = ConvertOptions(output_suffix=".png")
    plan = build_convert_plan([ConvertSourceItem(src, "a.jpg")], options)

    assert plan.can_apply and _listing(tmp_path) == ["a.jpg"]
    (written,) = apply_convert_plan(plan, options)

    assert written.lower().endswith(".png") and os.path.exists(src)
    with Image.open(written) as out:
        assert out.format == "PNG"


def test_convert_to_the_same_format_without_overwrite_makes_a_distinct_copy(tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"])
    options = ConvertOptions(output_suffix=".jpg")
    plan = build_convert_plan([ConvertSourceItem(src, "a.jpg")], options)
    written = apply_convert_plan(plan, options)

    assert all(path != src for path in written)
    assert os.path.exists(src)
