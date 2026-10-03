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

DRIVE_FIXED = 3


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
