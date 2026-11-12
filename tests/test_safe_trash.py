"""The safe trash keeps a record of where every file came from, and can be listed, restored from and emptied.
Drive classification off Windows comes from the mount table."""
from __future__ import annotations

import json

import pytest

from image_triage import safe_trash
from image_triage.file_ops import FileMove
from image_triage.safe_trash import (
    DRIVE_FIXED,
    DRIVE_REMOTE,
    DRIVE_REMOVABLE,
    MANIFEST_NAME,
    SafeTrash,
    classify_mount,
    mount_for_path,
    parse_mountinfo,
)


@pytest.fixture
def trash(tmp_path, monkeypatch):
    root = tmp_path / "trash"
    monkeypatch.setattr(SafeTrash, "recycle_root_for_folder", lambda self, folder=None: root)
    monkeypatch.setattr(SafeTrash, "is_temporary_storage_folder", lambda self, folder=None: False)
    shoot = tmp_path / "shoot"
    shoot.mkdir()
    root.mkdir()
    return SafeTrash(str(shoot)), shoot, root


def _trashed(trash_obj, shoot, root, name, data=b"x"):
    """Put a file in the trash as a delete does: moved there, and its origin remembered."""
    (root / name).write_bytes(data)
    trash_obj.remember_recycle_origins((FileMove(source_path=str(shoot / name), target_path=str(root / name)),))
    return str(root / name)


def test_origins_are_recorded_for_a_fixed_drive_too(trash):
    trash_obj, shoot, root = trash
    _trashed(trash_obj, shoot, root, "a.jpg")

    assert json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8")) == {str(root / "a.jpg"): str(shoot / "a.jpg")}


def test_list_items_shows_what_is_there_and_where_it_came_from(trash):
    trash_obj, shoot, root = trash
    _trashed(trash_obj, shoot, root, "a.jpg", b"12345")
    (root / "stray.jpg").write_bytes(b"1")                     # put there by an older build that kept no record

    items = {item.trash_path: item for item in trash_obj.list_items()}

    assert set(items) == {str(root / "a.jpg"), str(root / "stray.jpg")}                # the manifest itself is not an item
    assert items[str(root / "a.jpg")].original_path == str(shoot / "a.jpg") and items[str(root / "a.jpg")].size == 5
    assert items[str(root / "stray.jpg")].original_path == ""


def test_restore_puts_files_back_and_forgets_them(trash):
    trash_obj, shoot, root = trash
    a = _trashed(trash_obj, shoot, root, "a.jpg", b"aaa")
    b = _trashed(trash_obj, shoot, root, "b.jpg", b"bbb")

    outcome = trash_obj.restore([a])

    assert outcome.done == (a,) and outcome.left == ()
    assert (shoot / "a.jpg").read_bytes() == b"aaa" and not (root / "a.jpg").exists()
    assert (root / "b.jpg").exists() and trash_obj.load_recycle_manifest() == {b: str(shoot / "b.jpg")}


def test_restore_never_replaces_a_file_and_leaves_what_has_no_origin(trash):
    trash_obj, shoot, root = trash
    a = _trashed(trash_obj, shoot, root, "a.jpg", b"old")
    (shoot / "a.jpg").write_bytes(b"new photo with the same name")
    (root / "stray.jpg").write_bytes(b"1")

    outcome = trash_obj.restore()

    assert outcome.done == ()
    assert dict(outcome.left)[a].startswith("a file named a.jpg")
    assert dict(outcome.left)[str(root / "stray.jpg")] == "where it came from was not recorded"
    assert (shoot / "a.jpg").read_bytes() == b"new photo with the same name" and (root / "a.jpg").read_bytes() == b"old"
    assert trash_obj.load_recycle_manifest() == {a: str(shoot / "a.jpg")}


def test_restore_recreates_a_folder_that_is_gone(trash):
    trash_obj, shoot, root = trash
    (root / "deep.jpg").write_bytes(b"d")
    trash_obj.remember_recycle_origins((FileMove(source_path=str(shoot / "day2" / "deep.jpg"), target_path=str(root / "deep.jpg")),))

    assert trash_obj.restore().left == ()
    assert (shoot / "day2" / "deep.jpg").read_bytes() == b"d"


def test_empty_deletes_the_chosen_files_only(trash, tmp_path):
    trash_obj, shoot, root = trash
    a = _trashed(trash_obj, shoot, root, "a.jpg")
    b = _trashed(trash_obj, shoot, root, "b.jpg")
    outside = tmp_path / "keep.jpg"
    outside.write_bytes(b"k")

    outcome = trash_obj.empty([a, str(outside)])

    assert outcome.done == (a,) and outside.exists() and (root / "b.jpg").exists()
    assert trash_obj.load_recycle_manifest() == {b: str(shoot / "b.jpg")}

    assert trash_obj.empty().done == (b,)
    assert trash_obj.list_items() == [] and not (root / MANIFEST_NAME).exists()


def test_an_unreadable_manifest_is_no_manifest(trash):
    trash_obj, _shoot, root = trash
    (root / MANIFEST_NAME).write_text("[1, 2", encoding="utf-8")
    assert trash_obj.load_recycle_manifest() == {}
    (root / MANIFEST_NAME).write_text('["a list"]', encoding="utf-8")
    assert trash_obj.load_recycle_manifest() == {}


# ---- drive classification from the mount table ----------------------------------------------------------------------------------

MOUNTINFO = """\
24 1 8:2 / / rw,relatime shared:1 - ext4 /dev/sda2 rw
91 24 8:17 / /media/user/EOS_DIGITAL rw,nosuid,nodev shared:50 - vfat /dev/sdb1 rw
92 24 0:52 / /mnt/nas rw,relatime shared:51 - cifs //nas/photos rw,vers=3.0
93 24 8:33 / /mnt/My\\040Card rw shared:52 - exfat /dev/sdc1 rw
94 24 0:60 / /home/user/remote rw - fuse.sshfs user@host:/srv rw
"""


def test_mountinfo_is_parsed_and_the_deepest_mount_wins():
    mounts = parse_mountinfo(MOUNTINFO)
    assert ("/mnt/My Card", "exfat", "/dev/sdc1") in mounts
    assert mount_for_path("/media/user/EOS_DIGITAL/DCIM/100CANON", mounts)[0] == "/media/user/EOS_DIGITAL"
    assert mount_for_path("/home/user/Pictures", mounts)[0] == "/"
    assert mount_for_path("/mnt/nasty", mounts)[0] == "/"                       # not inside /mnt/nas


def test_mounts_are_classified_like_windows_drive_types():
    mounts = parse_mountinfo(MOUNTINFO)
    no = lambda source: False
    assert classify_mount(mount_for_path("/home/user/Pictures", mounts), removable=no) == DRIVE_FIXED
    assert classify_mount(mount_for_path("/media/user/EOS_DIGITAL/DCIM", mounts), removable=no) == DRIVE_REMOVABLE
    assert classify_mount(mount_for_path("/mnt/nas/2026", mounts), removable=no) == DRIVE_REMOTE
    assert classify_mount(mount_for_path("/home/user/remote/x", mounts), removable=no) == DRIVE_REMOTE
    assert classify_mount(mount_for_path("/mnt/My Card/DCIM", mounts), removable=no) == DRIVE_FIXED
    assert classify_mount(mount_for_path("/mnt/My Card/DCIM", mounts), removable=lambda source: source == "/dev/sdc1") == DRIVE_REMOVABLE


@pytest.mark.skipif(safe_trash.os.name == "nt", reason="the mount table is the POSIX path")
def test_a_card_gets_its_recycle_folder_beside_the_photos_and_a_share_counts_as_slow(monkeypatch):
    monkeypatch.setattr(safe_trash, "_read_mounts", lambda: parse_mountinfo(MOUNTINFO))
    monkeypatch.setattr(safe_trash, "_block_device_is_removable", lambda source: False)

    card = SafeTrash("/media/user/EOS_DIGITAL/DCIM/100CANON")
    assert card.is_temporary_storage_folder() and card.is_slow_source_folder()
    assert str(card.recycle_root_for_folder()) == "/media/user/EOS_DIGITAL/DCIM/recycle bin"

    share = SafeTrash("/mnt/nas/2026")
    assert not share.is_temporary_storage_folder() and share.is_slow_source_folder()

    local = SafeTrash("/home/user/Pictures")
    assert not local.is_temporary_storage_folder() and not local.is_slow_source_folder()
