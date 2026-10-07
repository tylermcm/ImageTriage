"""Characterization of discover_edited_paths (external "edited" variants of a photo) (WI-0.5)."""
from __future__ import annotations

from pathlib import Path

from image_triage.models import ImageRecord
from image_triage.scanner import discover_edited_paths, edit_stem_matches
from tests.harness import make_jpegs


def _record(path: str) -> ImageRecord:
    return ImageRecord(path=path, name=Path(path).name, size=1, modified_ns=1)


def _names(paths) -> list[str]:
    return [Path(p).name for p in paths]


def test_stem_matching_rules() -> None:
    assert edit_stem_matches("img_0001", "img_0001")
    assert edit_stem_matches("img_0001", "img_0001_2")
    assert edit_stem_matches("img_0001", "img_0001-3")
    assert edit_stem_matches("img_0001", "img_0001 4")
    assert not edit_stem_matches("img_0001", "img_0001_final")
    assert not edit_stem_matches("img_0001", "img_00012")
    assert not edit_stem_matches("img_0001", "other")


def test_finds_numbered_siblings_but_not_unrelated_files(tmp_path) -> None:
    (primary,) = make_jpegs(tmp_path, ["IMG_0001.jpg"])
    make_jpegs(tmp_path, ["IMG_0001_2.jpg", "IMG_0001_final.jpg", "IMG_0002.jpg"])

    assert _names(discover_edited_paths(_record(primary))) == ["IMG_0001_2.jpg"]


def test_finds_edits_inside_edit_and_edits_folders(tmp_path) -> None:
    (primary,) = make_jpegs(tmp_path, ["IMG_0001.jpg"])
    make_jpegs(tmp_path / "Edits", ["IMG_0001.jpg"])
    make_jpegs(tmp_path / "edit", ["IMG_0001_2.jpg"])
    make_jpegs(tmp_path / "other", ["IMG_0001_3.jpg"])

    found = discover_edited_paths(_record(primary))

    assert sorted(Path(p).parent.name for p in found) == ["Edits", "edit"]


def test_the_primary_itself_and_its_own_stack_are_excluded(tmp_path) -> None:
    (primary,) = make_jpegs(tmp_path, ["IMG_0001.jpg"])
    (paired,) = make_jpegs(tmp_path, ["IMG_0001_2.jpg"])
    record = ImageRecord(path=primary, name="IMG_0001.jpg", size=1, modified_ns=1, companion_paths=(paired,))

    assert discover_edited_paths(record) == ()


def test_only_editable_image_suffixes_count(tmp_path) -> None:
    (primary,) = make_jpegs(tmp_path, ["IMG_0001.jpg"])
    (tmp_path / "IMG_0001_2.txt").write_text("not an image")
    (tmp_path / "IMG_0001_3.nef").write_bytes(b"raw is not an edit format")

    assert discover_edited_paths(_record(primary)) == ()


def test_an_unreadable_folder_returns_nothing(tmp_path) -> None:
    assert discover_edited_paths(_record(str(tmp_path / "missing" / "IMG_0001.jpg"))) == ()


def test_results_are_ordered_deterministically(tmp_path) -> None:
    (primary,) = make_jpegs(tmp_path, ["IMG_0001.jpg"])
    make_jpegs(tmp_path, ["IMG_0001_2.jpg", "IMG_0001_10.jpg", "IMG_0001_3.jpg"])

    first = _names(discover_edited_paths(_record(primary)))
    second = _names(discover_edited_paths(_record(primary)))

    assert first == second and sorted(first) == sorted(["IMG_0001_2.jpg", "IMG_0001_10.jpg", "IMG_0001_3.jpg"])
