"""POSIX drive classification and file-name rules (audit D32).

The mount table is a fixture, so nothing here needs a real card, share or FAT volume. The Windows name rules are
exercised through ``check_name(..., rules=RULES_WINDOWS)``; nothing in this file runs on Windows.
"""
from __future__ import annotations

import os

import pytest

from image_triage import file_ops, path_policy, safe_trash
from image_triage.path_policy import (
    DRIVE_FIXED,
    DRIVE_REMOTE,
    DRIVE_REMOVABLE,
    DRIVE_UNKNOWN,
    RULES_PORTABLE,
    RULES_POSIX,
    RULES_WINDOWS,
    check_name,
)

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the mount table is the POSIX path")

MOUNTINFO = """\
22 1 8:1 / / rw,relatime shared:1 - ext4 /dev/sda1 rw
30 22 0:25 / /home/sam/nas rw,relatime shared:2 - nfs4 nas.example:/photos rw
31 22 8:17 / /media/sam/CARD rw,nosuid,nodev shared:3 - vfat /dev/sdb1 rw
32 22 8:33 / /media/sam/EXT rw,nosuid,nodev shared:4 - ext4 /dev/sdc1 rw
33 22 8:49 / /mnt/win rw,relatime shared:5 - fuseblk /dev/sdd1 rw
34 22 0:40 / /mnt/big\\040card rw,relatime shared:6 - exfat /dev/sde1 rw
35 22 0:41 / /net rw,relatime shared:7 - autofs /etc/auto.net rw
36 22 0:42 / /home/sam/share rw,relatime shared:8 - cifs //server/share rw
"""


@pytest.fixture
def mounts(monkeypatch):
    monkeypatch.setattr(path_policy, "_read_mounts", lambda: path_policy.parse_mountinfo(MOUNTINFO))
    monkeypatch.setattr(path_policy, "_block_device_is_removable", lambda source: False)
    path_policy.forget_mounts()
    yield
    path_policy.forget_mounts()


def test_paths_are_classified_by_the_mount_they_are_on(mounts):
    assert path_policy.drive_type("/home/sam/Pictures/2026") == DRIVE_FIXED
    assert path_policy.drive_type("/home/sam/nas/2026/shoot") == DRIVE_REMOTE
    assert path_policy.drive_type("/home/sam/share") == DRIVE_REMOTE
    assert path_policy.drive_type("/net/server/photos") == DRIVE_REMOTE          # an automount point: touching it mounts it
    assert path_policy.drive_type("/media/sam/CARD/DCIM/100CANON") == DRIVE_REMOVABLE
    assert path_policy.drive_type("/mnt/big card/DCIM") == DRIVE_FIXED            # not removable by name or by device
    assert path_policy.drive_type("") == DRIVE_UNKNOWN
    assert path_policy.drive_root("/home/sam/nas/2026/shoot") == "/home/sam/nas"
    assert path_policy.drive_root("/media/sam/CARD/DCIM") == "/media/sam/CARD"
    assert path_policy.drive_root("/home/sam/Pictures") == "/"
    assert path_policy.filesystem_type("/mnt/big card/DCIM") == "exfat"


def test_only_a_fixed_local_path_is_asked_about_on_the_gui_thread(mounts, monkeypatch, tmp_path):
    asked: list[str] = []
    real_isdir = os.path.isdir

    def isdir(path):
        asked.append(str(path))
        return real_isdir(path)

    monkeypatch.setattr(path_policy.os.path, "isdir", isdir)
    for unverifiable in ("/home/sam/nas/2026", "/media/sam/CARD/DCIM", "/net/server/photos", "/home/sam/share/x"):
        assert not path_policy.is_plain_local(unverifiable)
        assert path_policy.confirmed_missing(unverifiable) is False          # unavailable is not missing
    assert asked == []                                                       # and none of them was touched
    assert path_policy.is_plain_local(str(tmp_path))
    assert path_policy.confirmed_missing(str(tmp_path / "gone")) is True
    assert path_policy.confirmed_missing(str(tmp_path)) is False
    assert asked == [str(tmp_path / "gone"), str(tmp_path)]


def test_a_removable_disk_is_recognised_by_its_device(monkeypatch):
    monkeypatch.setattr(path_policy, "_read_mounts", lambda: path_policy.parse_mountinfo(MOUNTINFO))
    monkeypatch.setattr(path_policy, "_block_device_is_removable", lambda source: source == "/dev/sde1")
    path_policy.forget_mounts()
    try:
        assert path_policy.is_removable("/mnt/big card/DCIM")
        assert not path_policy.is_removable("/home/sam/Pictures")
    finally:
        path_policy.forget_mounts()


def test_without_a_mount_table_only_volume_locations_are_left_unchecked(monkeypatch):
    monkeypatch.setattr(path_policy, "_read_mounts", lambda: [])               # macOS: there is no /proc
    path_policy.forget_mounts()
    try:
        assert path_policy.is_plain_local("/Users/sam/Pictures")
        assert not path_policy.is_plain_local("/Volumes/NAS/photos")
        assert path_policy.confirmed_missing("/Volumes/NAS/photos") is False
        assert path_policy.drive_root("/Users/sam/Pictures") == "/"
    finally:
        path_policy.forget_mounts()


def test_the_mount_table_is_read_once_for_a_burst_of_questions(monkeypatch):
    reads = []
    monkeypatch.setattr(path_policy, "_read_mounts", lambda: reads.append(1) or path_policy.parse_mountinfo(MOUNTINFO))
    monkeypatch.setattr(path_policy, "_block_device_is_removable", lambda source: False)
    path_policy.forget_mounts()
    try:
        for index in range(200):
            path_policy.is_plain_local(f"/home/sam/nas/{index}")
        assert len(reads) == 1
    finally:
        path_policy.forget_mounts()


def test_safe_trash_still_offers_the_mount_reader_under_its_old_names():
    assert safe_trash.parse_mountinfo is path_policy.parse_mountinfo
    assert safe_trash.classify_mount is path_policy.classify_mount
    assert (safe_trash.DRIVE_REMOVABLE, safe_trash.DRIVE_FIXED, safe_trash.DRIVE_REMOTE) == (DRIVE_REMOVABLE, DRIVE_FIXED, DRIVE_REMOTE)


# ------------------------------------------------------------------------------------------------------------ names

def test_which_rules_a_destination_has(mounts):
    assert path_policy.name_rules("/home/sam/Pictures") == RULES_POSIX
    assert path_policy.name_rules("/home/sam/nas/2026") == RULES_POSIX            # an NFS export takes any POSIX name
    assert path_policy.name_rules("/media/sam/CARD/DCIM") == RULES_WINDOWS         # FAT enforces them itself
    assert path_policy.name_rules("/mnt/big card") == RULES_WINDOWS                # exFAT too
    assert path_policy.name_rules("/media/sam/EXT/x") == RULES_PORTABLE            # a removable ext4 disk
    assert path_policy.name_rules("/mnt/win/Photos") == RULES_PORTABLE             # NTFS through ntfs-3g
    assert path_policy.name_rules("/home/sam/share/x") == RULES_PORTABLE           # an SMB share
    assert path_policy.name_rules("\\\\nas\\photos\\x") == RULES_WINDOWS


@pytest.mark.parametrize("name", ['a:b?.jpg', 'what*"<>|.jpg', "CON.jpg", "aux", "trailing dot.", "back\\slash.jpg", "tab\tname.jpg"])
def test_a_posix_file_system_accepts_what_only_windows_forbids(name):
    assert check_name(name, rules=RULES_POSIX) == path_policy.NameVerdict()
    assert check_name(name, rules=RULES_WINDOWS).error
    verdict = check_name(name, rules=RULES_PORTABLE)
    assert verdict.error == "" and verdict.warning.startswith("Windows cannot read this name.")


@pytest.mark.parametrize("rules", [RULES_POSIX, RULES_PORTABLE, RULES_WINDOWS])
def test_what_every_file_system_refuses(rules):
    assert check_name("a/b.jpg", rules=rules).error
    assert check_name("nul\x00.jpg", rules=rules).error
    assert check_name(".", rules=rules).error and check_name("..", rules=rules).error
    assert check_name("", rules=rules).error == "Enter a name."
    assert check_name("IMG_0001.CR3", rules=rules) == path_policy.NameVerdict()


def test_the_posix_length_limit_counts_encoded_bytes():
    fits = "é" * 127 + ".j"            # 256 bytes would be one too many: 127 * 2 + 2 = 256
    assert "255 bytes" in check_name(fits, rules=RULES_POSIX).error
    assert check_name("é" * 126 + ".jp", rules=RULES_POSIX).error == ""         # 255 bytes, 129 characters
    assert check_name("a" * 255, rules=RULES_POSIX).error == ""
    assert check_name("a" * 256, rules=RULES_POSIX).error
    # the same 129-character name is fine for Windows, which counts UTF-16 units; 256 units are not
    assert check_name("é" * 127 + ".j", rules=RULES_WINDOWS).error == ""
    assert "255 characters" in check_name("é" * 256, rules=RULES_WINDOWS).error


def test_the_windows_messages_are_the_ones_the_app_always_gave():
    assert check_name("a:b", rules=RULES_WINDOWS).error == 'Names cannot contain < > : " / \\ | ? *'
    assert check_name("name ", rules=RULES_WINDOWS).error == "Names cannot end with a space or period on Windows."
    assert check_name("com1.txt", rules=RULES_WINDOWS).error == "'com1' is reserved on Windows."


def test_folders_and_renames_follow_the_destination(mounts, tmp_path, monkeypatch):
    # tmp_path is on the fixture's ext4 root: a name only Windows forbids is created ...
    made = file_ops.create_folder(str(tmp_path), "2026: Tbilisi?")
    assert os.path.isdir(made) and os.path.basename(made) == "2026: Tbilisi?"
    renamed = file_ops.rename_folder(made, "CON")
    assert os.path.basename(renamed) == "CON" and os.path.isdir(renamed)
    photo = tmp_path / "IMG_1.jpg"
    photo.write_bytes(b"x")
    assert file_ops.resolve_primary_rename_name(str(photo), "who? me") == "who? me.jpg"
    assert file_ops.name_warning("who? me.jpg", str(tmp_path)) == ""
    # ... a path is still not a name, and what POSIX forbids is refused
    with pytest.raises(ValueError, match="not a full path"):
        file_ops.create_folder(str(tmp_path), "a/b")
    with pytest.raises(ValueError, match="255 bytes"):
        file_ops.create_folder(str(tmp_path), "é" * 200)
    # on a FAT card the same name is refused before anything is written, and on a removable ext4 disk it is warned about
    with pytest.raises(ValueError, match="cannot contain"):
        file_ops.create_folder("/media/sam/CARD/DCIM", "2026: Tbilisi?")
    with pytest.raises(ValueError, match="reserved on Windows"):
        file_ops.resolve_primary_rename_name("/media/sam/CARD/DCIM/IMG_1.jpg", "con")
    assert file_ops.name_warning("2026: Tbilisi?", "/media/sam/EXT/x").startswith("Windows cannot read this name.")
    assert file_ops.name_warning("Tbilisi", "/media/sam/EXT/x") == ""
