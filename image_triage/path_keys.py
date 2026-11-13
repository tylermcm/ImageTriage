"""The one identity key for a path (audit D21).

Two paths name the same file when their keys are equal. Whether case matters is a property of the file system, so
the key folds case only where the file system does:

* **Windows**: case-insensitive. The key is the normalised path, casefolded, which is exactly what every store wrote
  before this module existed, so saved keys keep matching and nothing is migrated.
* **macOS**: case-insensitive. The default APFS and HFS+ volumes are; a volume formatted case-sensitive is rare and is
  deliberately treated like the default (two names differing only by case share a key there, as they always did).
* **Everything else (Linux, BSD)**: case-sensitive. ``IMG_1.jpg`` and ``img_1.JPG`` are two files and get two keys.
  A FAT/exFAT/NTFS volume mounted there is case-insensitive, but the names a directory listing returns are stable, so
  a case-preserving key is still an identity for them; the failure mode of not folding (a lookup by a differently cased
  spelling misses) is harmless, while folding on a case-sensitive volume merges two photos into one.

Separators and ``.``/``..`` segments are normalised with the platform's ``normpath`` (on Windows ``/`` becomes ``\\``;
on POSIX a backslash is an ordinary character of a name and is left alone).

Keys written by earlier builds on a case-sensitive platform were casefolded. ``legacy_path_key`` gives that old key
so a store can still find such a row; ``pick_stored`` applies the rule every store follows: the current key wins, the
old key is used only when it cannot mean another file.

``platform`` exists so the Windows and POSIX behaviour can both be exercised on one machine; application code leaves
it out.
"""
from __future__ import annotations

import ntpath
import os
import posixpath
import sys
from pathlib import Path
from typing import Callable, Iterable, Mapping, TypeVar

T = TypeVar("T")


def _is_windows(platform: str | None) -> bool:
    name = sys.platform if platform is None else platform
    return name.startswith(("win", "cygwin"))


def folds_case(platform: str | None = None) -> bool:
    """Whether names differing only by case are the same file on this platform's usual file systems."""
    name = sys.platform if platform is None else platform
    return _is_windows(name) or name == "darwin"


def fold_text(text: str, *, platform: str | None = None) -> str:
    """Fold an already-normalised path or name the way the platform's file system compares names."""
    return text.casefold() if folds_case(platform) else text


def _normpath(text: str, platform: str | None) -> str:
    if platform is None:
        return os.path.normpath(text)
    return ntpath.normpath(text) if _is_windows(platform) else posixpath.normpath(text)


def path_key(path: str | os.PathLike[str], *, platform: str | None = None) -> str:
    """The identity key of ``path``: pure string work, never the file system. ``""`` for an empty path."""
    text = os.fspath(path) if not isinstance(path, str) else path
    if not text:
        return ""
    return fold_text(_normpath(text, platform), platform=platform)


def name_key(name: str, *, platform: str | None = None) -> str:
    """The identity key of one file name or stem (no directory part)."""
    return fold_text(name, platform=platform)


def resolved_path_key(path: str | os.PathLike[str]) -> str:
    """``path_key`` of the path after resolving symlinks and relative parts through the file system (a stat: keep it off
    hot loops and off the GUI thread for network paths)."""
    raw = str(path).strip()
    if not raw:
        return ""
    candidate = Path(raw).expanduser()
    try:
        candidate = candidate.resolve(strict=False)
    except OSError:
        candidate = candidate.absolute()
    return path_key(str(candidate))


def legacy_path_key(path: str | os.PathLike[str], *, platform: str | None = None) -> str:
    """The key earlier builds wrote on every platform: the normalised path, casefolded."""
    text = os.fspath(path) if not isinstance(path, str) else path
    if not text:
        return ""
    return _normpath(text, platform).casefold()


def same_path(left: str | os.PathLike[str], right: str | os.PathLike[str], *, platform: str | None = None) -> bool:
    return path_key(left, platform=platform) == path_key(right, platform=platform)


def pick_stored(
    path: str | os.PathLike[str],
    stored: Mapping[str, T],
    *,
    stored_path: Callable[[T], str] | None = None,
    live_paths: Iterable[str] = (),
    platform: str | None = None,
) -> tuple[str, T] | None:
    """Find the row saved for ``path`` in ``stored`` (rows by their saved key): ``(saved key, row)`` or ``None``.

    The current key is tried first. A row under the old casefolded key is accepted only when it is unambiguous:

    * when the row remembers the path it was written for (``stored_path``), that path must be this one exactly;
    * otherwise no *other* path among ``live_paths`` (the paths being looked up together, for instance a folder's
      photos) may share the old key, because then the row could belong to either.

    The caller rewrites an accepted old row under the current key on its next save. Nothing is ever dropped here.
    """
    key = path_key(path, platform=platform)
    row = stored.get(key)
    if row is not None:
        return key, row
    old = legacy_path_key(path, platform=platform)
    if old == key:
        return None
    row = stored.get(old)
    if row is None:
        return None
    if stored_path is not None:
        saved = stored_path(row)
        if saved and path_key(saved, platform=platform) != key:
            return None
        if saved:
            return old, row
    for other in live_paths:
        if path_key(other, platform=platform) != key and legacy_path_key(other, platform=platform) == old:
            return None
    return old, row
