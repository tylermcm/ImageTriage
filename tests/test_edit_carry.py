"""A photo's Camera Raw recipe, PhotoCraft project and renders stay with it when the app renames, moves or deletes it."""
from __future__ import annotations

import json

from image_triage.edit_carry import carry_edits, edit_files_for
from image_triage.edit_storage import EDIT_STORAGE_ROOT_NAME


def _edits(folder, photo_name, with_manifest=False):
    root = folder / EDIT_STORAGE_ROOT_NAME
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{photo_name}.cameraraw.json").write_text('{"version": 1}', encoding="utf-8")
    (root / f"{photo_name}.pcraft").write_bytes(b"project")
    (root / f"{photo_name}.photocraft.g7.png").write_bytes(b"render")
    if with_manifest:
        (root / f"{photo_name}.photocraft.renders.json").write_text(
            json.dumps({"version": 1, "outputs": {"png": {"file": f"{photo_name}.photocraft.g7.png"}}}), encoding="utf-8")
    return root


def _names(root):
    return sorted(p.name for p in root.iterdir() if not p.name.startswith(".carry-"))


def test_only_the_photos_own_edit_files_are_found(tmp_path):
    root = _edits(tmp_path, "a.nef")
    (root / "a.nef.notes.txt").write_text("not edit data")
    (root / "a.nefx.cameraraw.json").write_text("{}")                # another photo whose name merely starts the same
    assert [p.name for p in edit_files_for(tmp_path / "a.nef")] == ["a.nef.cameraraw.json", "a.nef.pcraft", "a.nef.photocraft.g7.png"]


def test_a_move_takes_the_edits_to_the_new_folders_edit_root(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir(); dst.mkdir()
    old_root = _edits(src, "a.nef")
    assert carry_edits([(src / "a.nef", dst / "a.nef")]) == []
    assert _names(old_root) == []
    assert _names(dst / EDIT_STORAGE_ROOT_NAME) == ["a.nef.cameraraw.json", "a.nef.pcraft", "a.nef.photocraft.g7.png"]


def test_a_rename_renames_the_edits_and_the_pictures_the_manifest_names(tmp_path):
    root = _edits(tmp_path, "a.nef", with_manifest=True)
    assert carry_edits([(tmp_path / "a.nef", tmp_path / "wedding-001.nef")]) == []
    assert _names(root) == ["wedding-001.nef.cameraraw.json", "wedding-001.nef.pcraft", "wedding-001.nef.photocraft.g7.png", "wedding-001.nef.photocraft.renders.json"]
    manifest = json.loads((root / "wedding-001.nef.photocraft.renders.json").read_text(encoding="utf-8"))
    assert manifest["outputs"]["png"]["file"] == "wedding-001.nef.photocraft.g7.png"


def test_two_photos_that_swap_names_swap_their_edits(tmp_path):
    root = tmp_path / EDIT_STORAGE_ROOT_NAME
    root.mkdir()
    (root / "a.jpg.cameraraw.json").write_text("edits of a")
    (root / "b.jpg.cameraraw.json").write_text("edits of b")
    assert carry_edits([(tmp_path / "a.jpg", tmp_path / "b.jpg"), (tmp_path / "b.jpg", tmp_path / "a.jpg")]) == []
    assert (root / "b.jpg.cameraraw.json").read_text() == "edits of a" and (root / "a.jpg.cameraraw.json").read_text() == "edits of b"


def test_edits_already_at_the_destination_are_never_replaced(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir(); dst.mkdir()
    (src / EDIT_STORAGE_ROOT_NAME).mkdir(); (dst / EDIT_STORAGE_ROOT_NAME).mkdir()
    (src / EDIT_STORAGE_ROOT_NAME / "a.jpg.cameraraw.json").write_text("mine")
    (dst / EDIT_STORAGE_ROOT_NAME / "a.jpg.cameraraw.json").write_text("someone else's")
    assert carry_edits([(src / "a.jpg", dst / "a.jpg")]) == ["a.jpg.cameraraw.json"]
    assert (dst / EDIT_STORAGE_ROOT_NAME / "a.jpg.cameraraw.json").read_text() == "someone else's"
    assert (src / EDIT_STORAGE_ROOT_NAME / "a.jpg.cameraraw.json").read_text() == "mine"          # left where it was, not lost


# ---- the built-in editor's sessions: <stem>.edit.json and <stem>.edit-assets, in the hidden root or beside the photo ----
from image_triage.edit_carry import copy_edits, deletable_edit_paths, session_files_for


def _session(folder, stem, hidden=True):
    root = folder / EDIT_STORAGE_ROOT_NAME if hidden else folder
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{stem}.edit.json").write_text(json.dumps({"version": 1, "operations": [{"op": "crop"}], "assets": {"dir": f"{stem}.edit-assets", "bitmapMasks": []}}), encoding="utf-8")
    (root / f"{stem}.edit-assets").mkdir()
    (root / f"{stem}.edit-assets" / "mask.png").write_bytes(b"mask")
    return root


def test_a_session_moves_with_its_photo_in_both_layouts(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir(); dst.mkdir()
    _session(src, "a")
    _session(src, "b", hidden=False)
    assert carry_edits([(src / "a.nef", dst / "a.nef"), (src / "a.jpg", dst / "a.jpg"), (src / "b.jpg", dst / "b.jpg")]) == []
    assert not session_files_for(src / "a.nef") and not session_files_for(src / "b.jpg")
    assert (dst / EDIT_STORAGE_ROOT_NAME / "a.edit.json").exists() and (dst / EDIT_STORAGE_ROOT_NAME / "a.edit-assets" / "mask.png").read_bytes() == b"mask"
    assert (dst / "b.edit.json").exists() and (dst / "b.edit-assets" / "mask.png").exists()       # the older layout stays the older layout


def test_a_renamed_photos_session_is_renamed_and_still_finds_its_assets(tmp_path):
    root = _session(tmp_path, "a")
    assert carry_edits([(tmp_path / "a.jpg", tmp_path / "wedding-001.jpg")]) == []
    assert _names(root) == ["wedding-001.edit-assets", "wedding-001.edit.json"]
    assert json.loads((root / "wedding-001.edit.json").read_text(encoding="utf-8"))["assets"]["dir"] == "wedding-001.edit-assets"


def test_a_session_is_copied_not_taken_when_another_photo_with_the_stem_stays(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir(); dst.mkdir()
    root = _session(src, "shot")
    (src / "shot.png").write_bytes(b"stays")                       # shot.jpg has moved; shot.png shares the session and is still here
    assert carry_edits([(src / "shot.jpg", dst / "shot.jpg")]) == []
    assert (root / "shot.edit.json").exists() and (dst / EDIT_STORAGE_ROOT_NAME / "shot.edit.json").exists()
    # moving it back finds the same session already there: nothing is replaced, nothing is reported, no copy is left behind
    assert carry_edits([(dst / "shot.jpg", src / "shot.jpg")]) == []
    assert (root / "shot.edit-assets" / "mask.png").exists() and _names(dst / EDIT_STORAGE_ROOT_NAME) == []


def test_a_session_already_at_the_destination_is_never_replaced(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir(); dst.mkdir()
    _session(src, "a")
    (dst / EDIT_STORAGE_ROOT_NAME).mkdir()
    (dst / EDIT_STORAGE_ROOT_NAME / "a.edit.json").write_text("theirs")
    assert carry_edits([(src / "a.jpg", dst / "a.jpg")]) == ["a.edit.json"]
    assert (dst / EDIT_STORAGE_ROOT_NAME / "a.edit.json").read_text() == "theirs" and (src / EDIT_STORAGE_ROOT_NAME / "a.edit.json").exists()


def test_copying_copies_the_session_and_says_what_it_made(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir(); dst.mkdir()
    root = _session(src, "a")
    (root / "a.jpg.cameraraw.json").write_text("{}")
    made = []
    assert copy_edits([(src / "a.jpg", dst / "a_1.jpg")], made) == []
    there = dst / EDIT_STORAGE_ROOT_NAME
    assert _names(root) == ["a.edit-assets", "a.edit.json", "a.jpg.cameraraw.json"]                 # the original keeps everything
    assert _names(there) == ["a_1.edit-assets", "a_1.edit.json", "a_1.jpg.cameraraw.json"]
    assert json.loads((there / "a_1.edit.json").read_text(encoding="utf-8"))["assets"]["dir"] == "a_1.edit-assets"
    assert sorted(made) == sorted(str(p) for p in (there, there / "a_1.edit-assets", there / "a_1.edit.json", there / "a_1.jpg.cameraraw.json"))


def test_a_deleted_photo_takes_its_session_unless_the_stem_is_shared(tmp_path):
    root = _session(tmp_path, "a")
    for name in ("a.nef", "a.jpg"):
        (tmp_path / name).write_bytes(b"x")
    assert sorted(p.name for p in deletable_edit_paths([tmp_path / "a.nef", tmp_path / "a.jpg"])) == ["a.edit-assets", "a.edit.json"]
    (tmp_path / "a.png").write_bytes(b"another photo with the stem")
    assert deletable_edit_paths([tmp_path / "a.nef", tmp_path / "a.jpg"]) == []
    assert (root / "a.edit.json").exists()
