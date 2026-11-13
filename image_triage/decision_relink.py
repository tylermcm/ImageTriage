"""Decisions follow a photo that was moved or renamed outside the app (audit D16).

A decision is saved under the photo's path, so renaming a folder in the file manager used to orphan every decision in
it. When a folder is opened and some of its photos have no saved decision, the store's identity index is asked for
saved rows with the same file identity (file name, primary file size, primary modified time). A row is re-keyed to the
photo only when all of this holds:

* **The row is orphaned.** Its own path is provably gone: the volume it was on is answering and the file (or its whole
  folder) is not there. A path on a share that is asleep, an unplugged drive or an unmounted volume is *unavailable*,
  which is not missing, and its row is left exactly as it is.
* **The match is one to one.** Exactly one orphaned row matches the photo and that row matches no other photo. Two
  candidates either way are ambiguous and nothing is moved. Rows whose file name still matches are paired first (a
  moved file, a renamed folder); what is left is then paired on size and time alone, which is how a file renamed in
  place is recognised.

A renamed folder is the same rule applied to every photo in it: the rows relink in one transaction. Nothing is ever
deleted, and each move is written to the store's relink log.

The decisions table stores no content hash, so there is none to compare; name, size and modified time (to the
nanosecond) are the identity.

Everything here does file-system I/O and must run off the UI thread (the shell calls it on the folder-scan thread).
"""
from __future__ import annotations

import os
import posixpath
from dataclasses import dataclass
from pathlib import Path

from . import path_policy
from .models import ImageRecord
from .path_keys import name_key

PRESENT = "present"
MISSING = "missing"
UNAVAILABLE = "unavailable"

_VOLUME_PARENTS = ("/media", "/run/media", "/mnt", "/Volumes", "/net")


@dataclass(slots=True, frozen=True)
class RelinkReport:
    """What relinking did for one folder. ``relinked`` is ``(old path, new path)`` per decision that moved."""

    relinked: tuple[tuple[str, str], ...] = ()
    ambiguous: int = 0                 # photos (or rows) with more than one possible partner: left alone
    unavailable: int = 0               # matching rows whose own path could not be judged: left alone
    collections_changed: int = 0       # collection entries re-keyed for the same moves


class PathProbe:
    """Is a saved path present, provably missing, or on something that is not answering?

    Asks the file system (a dead share can take seconds), so only off the UI thread. A folder is listed once however
    many of its files are asked about, and a volume is asked once.
    """

    def __init__(self) -> None:
        self._folders: dict[str, tuple[str, frozenset[str]]] = {}
        self._volumes: dict[str, bool] = {}
        self._fstab: tuple[str, ...] | None = None

    def state(self, path: str) -> str:
        folder, name = os.path.split(path)
        if not folder or not name:
            return UNAVAILABLE
        known = self._folders.get(folder)
        if known is None:
            known = self._folders[folder] = self._folder_state(folder)
        state, names = known
        if state != PRESENT:
            return state
        return PRESENT if name_key(name) in names else MISSING

    def _folder_state(self, folder: str) -> tuple[str, frozenset[str]]:
        if not self._volume_answers(folder):
            return UNAVAILABLE, frozenset()
        try:
            with os.scandir(folder) as entries:
                return PRESENT, frozenset(name_key(entry.name) for entry in entries)
        except (FileNotFoundError, NotADirectoryError):
            return MISSING, frozenset()          # the volume answered and the folder is not on it
        except OSError:
            return UNAVAILABLE, frozenset()      # a timeout, a stale handle, no permission: not evidence of anything

    def _volume_answers(self, folder: str) -> bool:
        key = path_policy.drive_root(folder) if os.name == "nt" or path_policy.is_unc_path(folder) else self._posix_volume(folder)
        if key is None:
            return False
        answer = self._volumes.get(key)
        if answer is None:
            try:
                answer = os.path.isdir(key)
            except OSError:
                answer = False
            self._volumes[key] = answer
        return answer

    def _posix_volume(self, folder: str) -> str | None:
        """The directory whose presence shows that the volume ``folder`` was on is attached, or ``None`` when it is not.

        An unmounted volume leaves no trace: its paths simply fall through to the parent file system and look deleted.
        So a path where volumes are attached must be on a mount of its own right now, a path under a mount point the
        system knows about (``/etc/fstab``) needs that mount, and an automount point is never taken to be "gone".
        """
        target = posixpath.abspath(folder)
        mount = path_policy.posix_mount(target)
        if mount is not None and mount[1].casefold() == "autofs":
            return None
        point = posixpath.normpath(mount[0]) if mount is not None else "/"
        for parent in _VOLUME_PARENTS:
            if target == parent or target.startswith(parent + "/"):
                if mount is None:
                    # no mount table (macOS): the volume's own directory, /Volumes/<name>, has to be there
                    parts = target[len(parent):].strip("/").split("/")
                    return posixpath.join(parent, parts[0]) if parts[0] else None
                if len(point) <= len(parent):
                    return None                       # under /media, /mnt, ... but on the parent's file system: detached
        for known in self._fstab_points():
            if (target == known or target.startswith(known.rstrip("/") + "/")) and len(point) < len(known):
                return None                           # a configured mount point that is not mounted
        return point

    def _fstab_points(self) -> tuple[str, ...]:
        if self._fstab is None:
            points: list[str] = []
            try:
                for line in Path("/etc/fstab").read_text(encoding="utf-8", errors="replace").splitlines():
                    fields = line.split("#", 1)[0].split()
                    if len(fields) >= 2 and fields[1].startswith("/") and fields[1] != "/":
                        points.append(posixpath.normpath(fields[1].replace("\\040", " ")))
            except OSError:
                pass
            self._fstab = tuple(points)
        return self._fstab


def plan_relinks(
    records: list[ImageRecord],
    rows: list[tuple[str, int, int]],
    state_of,
) -> tuple[list[tuple[str, ImageRecord]], int, int]:
    """Pair photos that have no saved row with orphaned rows of the same identity.

    ``rows`` are ``(saved path, modified_ns, file_size)``; ``state_of(path)`` answers ``PRESENT``, ``MISSING`` or
    ``UNAVAILABLE``. Returns ``(pairs as (old path, record), ambiguous, unavailable)``. Only a one-to-one match is paired.
    """
    live = {record.path for record in records}
    by_identity: dict[tuple[int, int], list[str]] = {}
    for path, modified_ns, file_size in rows:
        if path not in live:
            by_identity.setdefault((int(modified_ns), int(file_size)), []).append(path)
    if not by_identity:
        return [], 0, 0

    # Every row with a matching size and time is a candidate; only the provably orphaned ones stay.
    orphaned: dict[tuple[int, int], list[str]] = {}
    unavailable = 0
    for identity, paths in by_identity.items():
        for old_path in paths:
            found = state_of(old_path)
            if found == MISSING:
                orphaned.setdefault(identity, []).append(old_path)
            elif found == UNAVAILABLE:
                unavailable += 1

    def candidates(record: ImageRecord, same_name: bool, used: set[str]) -> list[str]:
        name = name_key(os.path.basename(record.path))
        found: list[str] = []
        for identity in {(int(record.identity_modified_ns), int(record.identity_size)), (int(record.modified_ns), int(record.size))}:
            for old_path in orphaned.get(identity, ()):
                if old_path not in used and old_path not in found and (not same_name or name_key(os.path.basename(old_path)) == name):
                    found.append(old_path)
        return found

    pairs: list[tuple[str, ImageRecord]] = []
    used: set[str] = set()
    settled: set[str] = set()
    unsettled: set[str] = set()
    # First the photos whose file kept its name (a moved file, a renamed folder), then, among what is left, the files that
    # were renamed: there the size and the time are all there is, so one candidate each way is required all the more.
    for same_name in (True, False):
        partners: dict[str, list[str]] = {}
        claimed: dict[str, list[str]] = {}
        for record in records:
            if record.path in settled:
                continue
            for old_path in candidates(record, same_name, used):
                partners.setdefault(record.path, []).append(old_path)
                claimed.setdefault(old_path, []).append(record.path)
        by_path = {record.path: record for record in records}
        for new_path, old_paths in partners.items():
            if len(old_paths) == 1 and len(claimed[old_paths[0]]) == 1:
                pairs.append((old_paths[0], by_path[new_path]))
                used.add(old_paths[0])
                settled.add(new_path)
                unsettled.discard(new_path)
            else:
                unsettled.add(new_path)
    return pairs, len(unsettled), unavailable


def relink_folder(store, session_id: str, records: list[ImageRecord], *, probe: PathProbe | None = None, library=None) -> RelinkReport:
    """Re-key the decisions (and collection entries) of photos in ``records`` that were moved or renamed outside the app.

    ``store`` is a ``DecisionStore``; ``library`` a ``LibraryStore`` or ``None`` to leave collections alone. Blocks on the
    file system: call it off the UI thread.
    """
    records = [record for record in records if record.path and not record.is_folder]
    if not records:
        return RelinkReport()
    probe = probe or PathProbe()
    lacking, rows = store.relink_candidates(session_id, records)
    pairs, ambiguous, unavailable = plan_relinks(lacking, rows, probe.state) if rows else ([], 0, 0)
    relinked = store.apply_relinks(session_id, pairs, reason="moved outside the app") if pairs else []
    changed = 0
    if library is not None:
        moves = list(relinked)
        try:
            member_rows = library.collection_item_identities()
        except Exception:
            member_rows = []
        if member_rows:
            taken = {old for old, _new in moves}
            member_pairs, member_ambiguous, member_unavailable = plan_relinks(records, member_rows, probe.state)
            moves += [(old, record.path) for old, record in member_pairs if old not in taken]
            ambiguous += member_ambiguous
            unavailable += member_unavailable
        if moves:
            changed = int(library.rename_item_paths(moves) or 0)
    return RelinkReport(relinked=tuple(relinked), ambiguous=ambiguous, unavailable=unavailable, collections_changed=changed)
