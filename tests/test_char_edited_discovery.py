"""Characterization of discover_edited_paths (external "edited" variants of a photo) (WI-0.5)."""
from __future__ import annotations

from pathlib import Path

from image_triage.models import ImageRecord
from image_triage.scanner import discover_edited_paths, edit_stem_matches


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










def test_an_unreadable_folder_returns_nothing(tmp_path) -> None:
    assert discover_edited_paths(_record(str(tmp_path / "missing" / "IMG_0001.jpg"))) == ()
