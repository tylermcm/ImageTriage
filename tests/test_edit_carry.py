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
