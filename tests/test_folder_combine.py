"""Combine folders (image_triage/folder_combine.py): a trip's camera folders moved into one, the numbering continued instead of
" (1)" names, each photo's files moved and renamed together."""
from __future__ import annotations

import os
from pathlib import Path

from image_triage import folder_combine as fc


def make(folder: Path, *names: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).write_text(name)
    return folder


def trip(tmp_path):
    root = tmp_path / "Portland 10-26"
    a = make(root / "107NZ7_2", "_DSC1922.NEF", "_DSC1922.JPG", "_DSC1923.NEF", "_DSC1923.NEF.xmp")
    b = make(root / "108NZ7_2", "_DSC0001.NEF", "_DSC0001.JPG", "_DSC0002.NEF")
    c = make(root / "109NZ7_2", "_DSC0001.NEF", "_DSC0009.MOV", "notes.txt")
    return root, a, b, c


def targets(plan):
    return {Path(s.source).parent.name + "/" + Path(s.source).name: Path(s.target).name for s in plan.steps}


def test_into_the_first_folder_the_others_continue_its_numbering(tmp_path):
    root, a, b, c = trip(tmp_path)
    plan = fc.plan_combine([str(a), str(b), str(c)], str(a))
    assert plan.can_apply and plan.folders == [str(b), str(c)]
    assert targets(plan) == {
        "108NZ7_2/_DSC0001.JPG": "_DSC1924.JPG", "108NZ7_2/_DSC0001.NEF": "_DSC1924.NEF",     # the RAW and its JPEG stay a pair
        "108NZ7_2/_DSC0002.NEF": "_DSC1925.NEF",
        "109NZ7_2/_DSC0001.NEF": "_DSC1926.NEF", "109NZ7_2/_DSC0009.MOV": "_DSC1927.MOV",
        "109NZ7_2/notes.txt": "notes.txt",                                                     # no number: its own name, nothing there
    }
    assert plan.renamed == 4 and plan.kept == 1


def test_into_their_parent_the_first_folder_keeps_its_names_and_sidecars_follow(tmp_path):
    root, a, b, c = trip(tmp_path)
    plan = fc.plan_combine([str(a), str(b)], str(root))
    t = targets(plan)
    assert t["107NZ7_2/_DSC1922.NEF"] == "_DSC1922.NEF" and t["107NZ7_2/_DSC1923.NEF.xmp"] == "_DSC1923.NEF.xmp"
    assert t["108NZ7_2/_DSC0001.NEF"] == "_DSC1924.NEF" and t["108NZ7_2/_DSC0002.NEF"] == "_DSC1925.NEF"


def test_without_renumbering_a_taken_name_gets_a_count(tmp_path):
    root, a, b, c = trip(tmp_path)
    plan = fc.plan_combine([str(b), str(c)], str(b), renumber=False)
    assert targets(plan)["109NZ7_2/_DSC0001.NEF"] == "_DSC0001 (1).NEF"


def test_a_wider_number_keeps_its_width_and_a_new_prefix_has_its_own_count(tmp_path):
    root = tmp_path / "t"
    a = make(root / "a", "DSC_9998.NEF", "IMG_0005.CR3")
    b = make(root / "b", "DSC_0001.NEF", "IMG_0001.CR3", "DSC_0002.NEF", "DSC_0003.NEF")
    t = targets(fc.plan_combine([str(a), str(b)], str(a)))
    assert t == {"b/DSC_0001.NEF": "DSC_9999.NEF", "b/DSC_0002.NEF": "DSC_10000.NEF", "b/DSC_0003.NEF": "DSC_10001.NEF",
                 "b/IMG_0001.CR3": "IMG_0006.CR3"}


def test_applying_moves_everything_and_removes_the_emptied_folders(tmp_path):
    root, a, b, c = trip(tmp_path)
    (c / "keep me").mkdir()                                            # a subfolder stays, and so does its folder
    plan = fc.plan_combine([str(a), str(b), str(c)], str(a))
    done, failed = fc.apply_combine(plan)
    assert not failed and len(done) == len(plan.steps)
    assert sorted(p.name for p in a.iterdir()) == sorted(["_DSC1922.NEF", "_DSC1922.JPG", "_DSC1923.NEF", "_DSC1923.NEF.xmp",
                                                         "_DSC1924.NEF", "_DSC1924.JPG", "_DSC1925.NEF", "_DSC1926.NEF", "_DSC1927.MOV", "notes.txt"])
    assert (a / "_DSC1924.JPG").read_text() == "_DSC0001.JPG"           # the same file, renamed
    assert fc.remove_emptied(plan.folders) == [str(b)]
    assert not b.exists() and c.exists() and plan.left == {str(c): ["keep me"]}


def test_nothing_is_written_over_a_file_that_appeared_since_the_plan(tmp_path):
    root, a, b, c = trip(tmp_path)
    plan = fc.plan_combine([str(a), str(b)], str(a))
    (a / "_DSC1924.NEF").write_text("someone else")
    done, failed = fc.apply_combine(plan)
    assert (a / "_DSC1924.NEF").read_text() == "someone else" and any(name == "_DSC0001.NEF" for name, _ in failed)


def test_a_folder_cannot_go_into_itself_or_a_folder_inside_it(tmp_path):
    root, a, b, c = trip(tmp_path)
    assert "inside" in fc.plan_combine([str(root)], str(a)).error
    assert fc.plan_combine([str(a)], str(a)).error
