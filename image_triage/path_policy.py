"""Which paths may be asked "do you exist?" on the GUI thread (audit P9 / WI-8.5).

A share that is asleep, or a VPN that is off, makes ``os.path.isdir`` block for ~20 s and then answer "no".
Two rules follow, and every GUI-side check should go through this module:

* **Only a plain local fixed drive is checked on the GUI thread.** There an existence check is instant.
  Network shares (UNC paths and mapped drive letters), removable drives, a drive letter that is not present
  right now, and anything unrecognised are *not* touched; opening one is left to the scan worker, which
  reports the failure.
* **An unverifiable path is never judged missing.** Saved lists (favorites, recent folders, Move-To
  destinations) used to drop a path that failed the check and then save the shortened list, so a sleeping
  NAS cost the user those entries for good. A path is only pruned when it is *provably* gone on a drive
  where the check is instant.

Off Windows the drive type cannot be read this way, so every path counts as plain local, which is exactly
how the app behaved before this module existed.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path

from .file_ops import is_unc_path, unc_share_root

DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5


def drive_root(path: str | None) -> str:
    text = str(path or "")
    if is_unc_path(text):
        return unc_share_root(text)
    try:
        return Path(text).anchor
    except (OSError, ValueError):
        return ""


_DRIVE_TYPES: dict[str, int] = {}


def _drive_type(root: str) -> int:
    if not root:
        return 0
    if is_unc_path(root):
        return 4
    key = os.path.normpath(root).casefold()
    cached = _DRIVE_TYPES.get(key)
    if cached is not None:
        return cached
    try:
        drive_type = int(ctypes.windll.kernel32.GetDriveTypeW(root))  # type: ignore[attr-defined]
    except Exception:
        return 0
    # 0 (unknown) and 1 (no such drive right now) are not remembered: the letter may appear later.
    if drive_type >= 2:
        _DRIVE_TYPES[key] = drive_type
    return drive_type


def is_plain_local(path: str | None) -> bool:
    """True when asking the filesystem about ``path`` is instant (a fixed local drive)."""
    if os.name != "nt":
        return True
    return _drive_type(drive_root(path)) == DRIVE_FIXED


def confirmed_missing(path: str | None) -> bool:
    """True only when ``path`` is provably not a folder *and* checking it cannot block."""
    if not path or not is_plain_local(path):
        return False
    return not os.path.isdir(path)


def drive_roots() -> list[tuple[str, int]]:
    """The drives this machine has right now as ``("C:/", drive_type)``, from the drive-letter bitmask and
    ``GetDriveTypeW``: neither touches a drive, so an offline mapped drive costs nothing here. (Asking Windows *about* a
    dead network drive, by existence check, label, size or listing, is what blocks, for ~20 s, and a
    ``QFileSystemModel`` rooted at "all drives" does exactly that on its single worker thread.)

    Off Windows there is one root, ``/``, and it counts as a plain local drive.
    """
    if os.name != "nt":
        return [("/", DRIVE_FIXED)]
    try:
        mask = int(ctypes.windll.kernel32.GetLogicalDrives())  # type: ignore[attr-defined]
    except Exception:
        return []
    roots: list[tuple[str, int]] = []
    for offset in range(26):
        if mask & (1 << offset):
            letter = chr(ord("A") + offset)
            roots.append((f"{letter}:/", _drive_type(f"{letter}:\\")))
    return roots
