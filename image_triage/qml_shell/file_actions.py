"""Delete, move and copy photos: the shared file operations, with the same trash rules, companions and undo.

A photo is a bundle (the file, its RAW or JPEG companion and sidecars: `file_ops.record_paths`); every operation takes the whole bundle. Delete
follows the "Delete behavior" setting: **Safe Trash** moves the bundle into the app's recycle folder (a "recycle bin" folder beside a removable
drive's folder, otherwise the app's `safe-trash`) and can be undone; **System Trash** sends it to the operating system's trash (not undoable here, as
in the Widgets app, and with a question if the system refuses). A file inside a folder called "recycle bin" is deleted for good after a question.
A saved decision follows a moved photo (`DecisionStore.move_annotation`) and is dropped for a deleted one; undoing a delete puts both back.
Every step is on the photo model's undo list (Ctrl+Z, or the toast's button). `--mock` photos have no files: nothing is touched and a toast says so.

The files are never touched on the UI thread. A slot here picks the photos, hands the file work to `file_jobs.FileJobs` (one operation at a time,
with progress and Cancel between photos) and returns; the grid, the saved decisions, the collections and the undo step change when the worker
reports back, with exactly the photos that went. A photo that fails is left whole where it was and named at the end; the others still go.
Undo and `trashPaths` have to answer at once, so they wait for the worker with the window kept alive (`FileJobs.wait`). `waitForIdle` does the
same for anything else that needs the result.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from PySide6.QtCore import Property, QFile, QObject, Signal, Slot

from ..edit_carry import carry_edits, copy_edits, deletable_edit_paths
from ..edit_storage import EDIT_STORAGE_ROOT_NAME
from .. import safe_trash_store
from ..file_ops import FileMove, bundle_destinations, copy_paths, move_paths, record_after_moves, record_paths
from .decision_bridge import session_id
from .file_jobs import FileJobs

_restore = safe_trash_store.restore_moves          # one photo's files back to where they were, all or none, never replacing
LIMIT_KEY = "workflow/safe_trash_limit_gb"         # 0: no limit
COLLISION_KEY = "workflow/name_collision"           # file_ops.COLLISION_MODES
TRASH_ROOTS_KEY = "workflow/safe_trash_roots"       # the recycle folders the app has made on drives

SAFE_TRASH = "Safe Trash (Undoable)"
RECENT_KEY = "folders/recent_destinations"


def delete_bundle(record) -> tuple[str, ...]:
    """Everything that goes when a photo is deleted: its files, its sidecars and its edit data (which would otherwise be left for a later file of
    the same name to inherit). A built-in editor session goes too, unless another photo with the same stem stays in the folder."""
    paths = list(record_paths(record))
    seen = {os.path.normcase(os.path.normpath(p)) for p in paths}
    for item in deletable_edit_paths(record.stack_paths):
        key = os.path.normcase(os.path.normpath(str(item)))
        if key not in seen:
            seen.add(key)
            paths.append(str(item))
    return tuple(paths)


def _remove(path: str) -> None:
    """Delete a file, or a folder with what is in it (a session's assets)."""
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        os.remove(path)


def _unchanged(path: str, stamp) -> bool:
    """Is the file at ``path`` still the one that was made there (``stamp`` is its size and time then; None for a folder)?"""
    try:
        if stamp is None:
            return os.path.isdir(path)
        status = os.stat(path)
        return (status.st_size, status.st_mtime_ns) == stamp
    except OSError:
        return False


def _stamp(path: str):
    try:
        if os.path.isdir(path):
            return None
        status = os.stat(path)
        return (status.st_size, status.st_mtime_ns)
    except OSError:
        return (-1, -1)


def _is_recycle_folder(folder: str) -> bool:
    return bool(folder) and any(part.casefold() == "recycle bin" for part in Path(folder).parts)


def rekey_after_move(record, moves, annotation) -> None:
    """A saved decision follows its photo to the new path (the Widgets' `rekey_annotation_after_move`)."""
    if annotation is None or annotation.is_empty:
        return
    from ..decision_store import DecisionStore
    from ..file_ops import record_after_moves

    DecisionStore().move_annotation(session_id(), record.path, record_after_moves(record, tuple(moves)), annotation)


def rekey_collections(pairs) -> bool:
    """Collections hold photos by path, so they follow a photo the app moved or renamed. ``pairs`` is ``(old path, new path)``.
    False when the library could not be updated (the photos themselves have already moved)."""
    pairs = [(old, new) for old, new in pairs if old and new and old != new]
    if not pairs:
        return True
    try:
        from ..library_store import LibraryStore

        LibraryStore().rename_item_paths(pairs)
    except Exception:
        return False
    return True


def _trash_bundle(bundle) -> tuple[bool, list[str]]:
    """Send one photo's files to the system trash, all or none.

    Stops at the first file the system refuses and brings back the ones already sent, so a RAW is not left behind while its
    JPEG and sidecar are in the trash. Returns whether everything went, and the names that are in the trash and could not be
    brought back (the system does not always say where it put a file).
    """
    trashed: list[tuple[str, str]] = []                  # (where it was, where the system put it)
    for path in bundle:
        if not os.path.exists(path):
            continue
        handle = QFile(path)
        if hasattr(handle, "moveToTrash") and handle.moveToTrash():
            trashed.append((path, str(handle.fileName() or "") if hasattr(handle, "fileName") else ""))
            continue
        stranded: list[str] = []
        for original, in_trash in reversed(trashed):
            try:
                if not in_trash or not os.path.exists(in_trash) or os.path.exists(original):
                    raise OSError("cannot be brought back")
                shutil.move(in_trash, original)
                _forget_trash_info(in_trash)
            except OSError:
                stranded.append(Path(original).name)
        return False, stranded
    return True, []


def _forget_trash_info(in_trash: str) -> None:
    """A freedesktop trash keeps a note beside each file (``info/<name>.trashinfo``); a file taken back out leaves it orphaned."""
    files_dir = Path(in_trash).parent
    if files_dir.name != "files":
        return
    try:
        (files_dir.parent / "info" / (Path(in_trash).name + ".trashinfo")).unlink()
    except OSError:
        pass


def _label(path: str) -> str:
    parts = [p for p in str(path).replace("\\", "/").split("/") if p]
    return " / ".join(parts[-2:]) if parts else str(path)


def _plural(count: int, one: str = "photo") -> str:
    return f"{count} {one}{'s' if count != 1 else ''}"


def _named(failed, single: bool) -> str:
    """The first failure, and how many more there were: ``failed`` is ``[(photo name, why, ...)]``."""
    if not failed:
        return ""
    name, why = failed[0][0], failed[0][1]
    text = why if single else f"{name}: {why}"
    return text + (f" (and {len(failed) - 1} more)" if len(failed) > 1 else "")


class FileActions(QObject):
    confirmRequested = Signal(str, str, str, str)       # id, title, body, the confirm button's text
    recentChanged = Signal()
    busyChanged = Signal()

    def __init__(self, photos, toasts, memory: bool = False, parent: QObject | None = None, jobs: FileJobs | None = None) -> None:
        super().__init__(parent)
        self._photos = photos
        self._toasts = toasts
        self._memory = memory
        self._jobs = jobs if jobs is not None else FileJobs(parent=self)       # one file operation at a time, shared with the Safe Trash window
        self._jobs.busyChanged.connect(self.busyChanged)
        self._last_trash_id = 0                          # the undo step of the last move to the safe trash (0: nothing moved)
        self._pending: dict[str, object] = {}           # confirmation id -> what to do when it is answered yes
        self._next = 1
        self.asker = None                                # fn(title, body, confirm) -> bool: asks at once instead of through the window (the full-screen viewer sets it)
        self._recent: list[str] = ["D:/Delivery/Album", "D:/Georgia trip/Picks"] if memory else []

    # ---------------- the operation in hand ----------------
    busy = Property(bool, lambda self: self._jobs.busy, notify=busyChanged)

    @property
    def jobs(self) -> FileJobs:
        return self._jobs

    @Slot()
    def cancel(self) -> None:
        """Stop the running operation after the photo it is on."""
        self._jobs.cancel()

    @Slot(result=bool)
    @Slot(int, result=bool)
    def waitForIdle(self, timeout_ms: int = 60000) -> bool:
        """Keep the window alive until the running operation has ended and its result has been applied. True when nothing runs."""
        return self._jobs.wait(timeout_ms)

    def shutdown(self) -> None:
        """Quitting: stop after the photo in hand and apply what was done (the decisions and collections of the photos that moved)."""
        self._jobs.shutdown()

    def _start(self, title: str, work, done, *, route: str = "", total: int = 0) -> bool:
        if self._jobs.start(title, work, done, route=route, total=total, failed=lambda message: self._toast("error", "The file operation stopped", message)):
            return True
        self._toast("warn", "Another file operation is still running", "Wait for it to finish, or cancel it, then try again.")
        return False

    def _run_and_wait(self, title: str, work, *, route: str = "", total: int = 0):
        """Run an operation and keep the window alive until it has ended; its result, or None when it could not be started.
        For Undo, which has to say whether it worked."""
        box: list = []
        if not self._start(title, work, box.append, route=route, total=total):
            return None
        self._jobs.wait()
        return box[0] if box else None

    # ---------------- helpers ----------------
    def _settings(self):
        from ..app_identity import user_settings

        return user_settings()

    def _delete_mode(self) -> str:
        if self._memory:
            return SAFE_TRASH
        return str(self._settings().value("workflow/delete_mode", SAFE_TRASH, str) or SAFE_TRASH)

    def _collision_mode(self) -> str:
        """What a move or copy does with a photo whose name is taken in the destination (Settings, General)."""
        from ..file_ops import COLLISION_MODES, COLLISION_RENAME

        if self._memory:
            return COLLISION_RENAME
        mode = str(self._settings().value(COLLISION_KEY, COLLISION_RENAME, str) or COLLISION_RENAME)
        return mode if mode in COLLISION_MODES else COLLISION_RENAME

    def _toast(self, kind: str, title: str, body: str = "", action: str = "", callback=None) -> None:
        self._toasts.push(kind, title, body, action, callback)

    def _row(self, row: int):
        item = self._photos.row_at(row)
        if item is None:
            return None, None
        record = item.get("record")
        if record is None:
            self._toast("info", "These are sample photos", "There is no file to change.")
            return item, None
        return item, record

    def _recycle(self, folder: str):
        """The Widgets' recycle-bin rules for `folder` (where the safe trash is, whether it is a removable drive, the restore manifest)."""
        from ..safe_trash import SafeTrash

        return SafeTrash(folder)

    def _store(self):
        from ..decision_store import DecisionStore

        return DecisionStore()

    def _rekey_decisions(self, entries) -> None:
        """Saved decisions follow their photos: ``entries`` is ``(old path, the record at its new path, annotation)``. One
        transaction for all of them, so a failure leaves every decision where it was rather than some here and some there."""
        entries = [entry for entry in entries if entry[0] and entry[2] is not None and not entry[2].is_empty]
        if not entries or self._memory:
            return
        try:
            self._store().move_annotations(session_id(), entries)
        except Exception as exc:
            self._toast("warn", "Decisions were not carried over", f"The photos moved, but their saved decisions did not follow: {exc}")

    def _rekey_collections(self, pairs) -> None:
        if not self._memory and not rekey_collections(pairs):
            self._toast("warn", "Collections were not updated", "The photos moved, but the collections they are in still point at the old place.")

    # ---------------- recent destinations ----------------
    def recent(self) -> list[str]:
        if self._memory:
            return list(self._recent)
        raw = self._settings().value(RECENT_KEY, [], list)
        if isinstance(raw, str):
            raw = [raw]
        from .. import path_policy

        here = os.path.normcase(os.path.normpath(self._photos.folder_dir())) if self._photos.folder_dir() else ""
        # This runs on the UI thread every time a menu is built. Only a folder on a plain local drive is asked whether it is
        # still there; a share or a removable drive is listed unasked (a sleeping one would hold the window for seconds).
        return [p for p in raw or [] if isinstance(p, str) and p and not path_policy.confirmed_missing(p) and os.path.normcase(os.path.normpath(p)) != here][:6]

    def _remember(self, destination: str) -> None:
        if self._memory:
            self._recent = [destination, *[d for d in self._recent if d != destination]][:10]
        else:
            current = self._settings().value(RECENT_KEY, [], list)
            current = [current] if isinstance(current, str) else list(current or [])
            key = os.path.normcase(os.path.normpath(destination))
            self._settings().setValue(RECENT_KEY, [destination, *[d for d in current if os.path.normcase(os.path.normpath(d)) != key]][:10])
        self.recentChanged.emit()

    # ---------------- delete ----------------
    @Slot(int)
    def deleteRow(self, row: int) -> None:
        item, record = self._row(row)
        if record is None:
            return
        self._delete([(record, item.get("annotation"))], single=True)

    @Slot("QVariantList")
    def deleteRows(self, rows) -> None:
        """Delete every photo the action applies to (the selection, else the row the grid is on) as one step."""
        picked = []
        for row in dict.fromkeys(int(r) for r in rows):
            item = self._photos.row_at(row)
            if item is not None and item.get("record") is not None:
                picked.append((row, item))
        if len(picked) <= 1:
            for row in ([picked[0][0]] if picked else [int(r) for r in list(rows)[:1]]):
                self.deleteRow(row)
            return
        self._delete([(item["record"], item.get("annotation")) for _row, item in picked], single=False)

    def _delete(self, items: list, *, single: bool) -> None:
        """``items`` is ``[(record, annotation)]``. Which kind of delete it is depends on the folder and the setting; the files
        themselves are handled on the worker, and the grid and the saved decisions change when it reports back."""
        folder = self._photos.folder_dir()
        name = items[0][0].name
        if _is_recycle_folder(folder):
            if single:
                self._ask(f"Delete {name} permanently?", "It is in a recycle bin, so this cannot be undone.", "Delete", lambda: self._delete_for_good(items, True))
            else:
                self._ask(f"Delete {len(items)} photos permanently?", "They are in a recycle bin, so this cannot be undone.", "Delete", lambda: self._delete_for_good(items, False))
            return
        recycle = self._recycle(folder)
        if self._delete_mode() == SAFE_TRASH or recycle.is_temporary_storage_folder():
            self._safe_trash(items, recycle, single)
            return
        count = len(items)
        self._start(f"Moving {name if single else _plural(count)} to the trash", lambda ctx: _work_system_trash(ctx, items),
                    lambda result: self._system_trashed(result, items, single), route=f"{folder} \u2192 System trash", total=count)

    def _system_trashed(self, result: dict, items: list, single: bool) -> None:
        trashed, failed, stranded = result["done"], result["failed"], result["stranded"]
        self._finish_delete([record for record, _annotation in trashed])
        if single:
            name = items[0][0].name
            if failed:
                why = "It could not be moved to the trash."
                if stranded:
                    why += f" Already in the trash: {', '.join(stranded[:3])}{' and more' if len(stranded) > 3 else ''}."
                self._ask(f"Delete {name} permanently?", why, "Delete", lambda: self._delete_for_good(items, True))
            elif trashed:
                self._toast("success", f"Moved {name} to the trash", "Use the system trash to bring it back.")
            return
        if trashed:
            self._toast("success", f"Moved {_plural(len(trashed))} to the trash", "Use the system trash to bring them back.")
        if failed:
            body = ", ".join(record.name for record, _annotation in failed[:3]) + (" and more" if len(failed) > 3 else "")
            if stranded:
                body += f". Already in the trash: {', '.join(stranded[:3])}{' and more' if len(stranded) > 3 else ''}"
            self._toast("error", f"{_plural(len(failed))} could not be moved to the trash", body)
        if result["cancelled"]:
            self._toast("info", f"Stopped after {len(trashed)} of {len(items)}", "The ones already moved are in the system trash.")

    def _delete_for_good(self, items: list, single: bool) -> None:
        count = len(items)
        self._start(f"Deleting {items[0][0].name if single else _plural(count)}", lambda ctx: _work_remove(ctx, items),
                    lambda result: self._deleted_for_good(result, items, single), total=count)

    def _deleted_for_good(self, result: dict, items: list, single: bool) -> None:
        gone, failed = result["done"], result["failed"]
        self._finish_delete([record for record, _annotation in gone])
        if single:
            name = items[0][0].name
            if failed:
                self._toast("error", f"Could not delete {name}", failed[0][1])
            elif gone:
                self._toast("success", f"Deleted {name} for good")
            return
        if failed:
            self._toast("error", f"{len(failed)} of {len(items)} photos could not be deleted", _named(failed, False))
        if result["cancelled"]:
            self._toast("info", f"Stopped after deleting {len(gone)} of {len(items)}", "The rest were not touched.")
        elif gone and not failed:
            self._toast("success", f"Deleted {len(gone)} photos for good")

    def _finish_delete(self, records: list) -> None:
        if not records:
            return
        if not self._memory:
            try:                                          # one store and one commit, however many photos
                self._store().delete_annotations(session_id(), [record.path for record in records])
            except Exception:
                pass
        self._photos.remove_paths([record.path for record in records])

    def trash_limit_bytes(self) -> int:
        """The size the safe trash on this computer is kept under (0: no limit)."""
        if self._memory:
            return 0
        try:
            return max(0, int(self._settings().value(LIMIT_KEY, 0, int) or 0)) * 1024 ** 3
        except (TypeError, ValueError):
            return 0

    def _remember_trash_root(self, root: str) -> None:
        """A recycle folder on a drive: remembered so the Safe Trash window lists it whenever that drive is connected."""
        known = self._settings().value(TRASH_ROOTS_KEY, [], list) or []
        known = [known] if isinstance(known, str) else [str(item) for item in known]
        key = os.path.normcase(os.path.normpath(root))
        if all(os.path.normcase(os.path.normpath(item)) != key for item in known):
            self._settings().setValue(TRASH_ROOTS_KEY, [root, *known][:40])

    def _safe_trash(self, items: list, recycle, single: bool) -> bool:
        root = recycle.recycle_root_for_folder()
        count = len(items)
        self._last_trash_id = 0
        session = session_id()
        # the limit is for the trash in the app's own data, not for a recycle folder on a card
        limit = 0 if recycle.is_temporary_storage_folder() or _is_recycle_folder(str(root)) else self.trash_limit_bytes()
        return self._start(f"Deleting {items[0][0].name}" if single else f"Moving {_plural(count)} to the recycle bin",
                           lambda ctx: _work_safe_trash(ctx, items, recycle, root, session, limit),
                           lambda result: self._safe_trashed(result, recycle, root, single, count),
                           route=f"{self._photos.folder_dir()} \u2192 Safe trash", total=count)

    def _safe_trashed(self, result: dict, recycle, root, single: bool, count: int) -> None:
        moved, failed = result["done"], result["failed"]         # moved: [(record, moves, annotation)], the photos that are in the trash now
        if moved and _is_recycle_folder(str(root)) and not self._memory:
            self._remember_trash_root(str(root))
        if moved:
            self._finish_delete([record for record, _moves, _annotation in moved])
            entry = {}
            if single:
                name = moved[0][0].name
                entry["id"] = self._photos.push_undo(f"delete of {name}", lambda: self._undo_trash(moved, recycle, True))
                self._toast("success", f"Deleted {name}", "It is in the safe trash (File menu, Safe Trash).", "Undo", lambda: self._photos.undo_entry(entry["id"]))
            else:
                entry["id"] = self._photos.push_undo(f"move of {len(moved)} photos to the recycle bin", lambda: self._undo_trash(moved, recycle, False))
                self._toast("success", f"Moved {_plural(len(moved))} to the recycle bin", "Undo brings them back; later, use Safe Trash in the File menu.", "Undo", lambda: self._photos.undo_entry(entry["id"]))
            self._last_trash_id = entry["id"]
        if failed:
            left = [name for _name, _why, names in failed for name in names]
            title = f"Could not delete {failed[0][0]}" if single else ("Could not move them to the recycle bin" if not moved else f"{_plural(len(failed))} could not be moved to the recycle bin")
            self._toast("error", title, _with_leftovers(_named(failed, single), left, str(root)))
        if result["cancelled"]:
            self._toast("info", f"Stopped after {len(moved)} of {count}", "The ones already moved are in the recycle bin; Undo brings them back.")
        if result.get("evicted"):
            self._toast("info", f"{_plural(result['evicted'], 'older photo')} left the safe trash for good", "It was over its size limit (Safe Trash, in the File menu).")

    @Slot("QVariantList", str, result=int)
    def trashPaths(self, paths, why: str = "") -> int:
        """Move these photos (with their companions) to the safe trash as one step: one toast, one Undo. Returns the undo step's id
        (0 when nothing moved). The files move on the worker; this waits for it, with the window alive, because the caller needs the id."""
        wanted = {os.path.normcase(os.path.normpath(str(p))) for p in paths}
        rows = [r for r in self._photos.all_rows() if os.path.normcase(os.path.normpath(r["path"])) in wanted and r.get("record") is not None]
        if not rows:
            return 0
        if not self._safe_trash([(row["record"], row.get("annotation")) for row in rows], self._recycle(self._photos.folder_dir()), False):
            return 0
        self._jobs.wait()
        return self._last_trash_id

    def _undo_trash(self, moved: list, recycle, single: bool) -> bool:
        """Bring deleted photos back from the safe trash. ``moved`` shrinks to what is still there, so a step that was
        cancelled or only partly worked stays on the history and can be run again for the rest."""
        count = len(moved)
        name = moved[0][0].name if moved else ""
        result = self._run_and_wait(f"Bringing back {name if count == 1 else _plural(count)}",
                                    lambda ctx: _work_restore(ctx, list(moved), "is no longer in the safe trash", recycle=recycle), total=count)
        if result is None:
            return False
        back, failed = result["done"], result["failed"]
        if back:
            saved = [(record, annotation) for record, _moves, annotation in back if annotation is not None and not annotation.is_empty]
            if saved and not self._memory:
                self._store().save_annotations(session_id(), saved)
            self._photos.reload()
        returned = {id(entry) for entry in back}
        moved[:] = [entry for entry in moved if id(entry) not in returned]
        if failed:
            self._toast("error", f"Could not bring back {failed[0][0]}" if single or count == 1 else "Could not bring them back",
                        _named(failed, single or count == 1) + (f" {_plural(len(back))} came back." if back else ""))
        elif moved:
            self._toast("info", f"Stopped after bringing back {len(back)} of {count}", "Undo again brings back the rest.")
        else:
            self._toast("success", f"Brought back {name}" if single else f"Brought back {_plural(count)}")
        return not moved

    # ---------------- move and copy ----------------
    @Slot(int, str)
    def moveRow(self, row: int, destination: str) -> None:
        self._transfer_many([row], destination, move=True)

    @Slot(int, str)
    def copyRow(self, row: int, destination: str) -> None:
        self._transfer_many([row], destination, move=False)

    @Slot("QVariantList", str)
    def moveRows(self, rows, destination: str) -> None:
        self._transfer_many([int(r) for r in rows], destination, move=True)

    @Slot("QVariantList", str)
    def copyRows(self, rows, destination: str) -> None:
        self._transfer_many([int(r) for r in rows], destination, move=False)

    def _transfer_many(self, rows: list[int], destination: str, *, move: bool) -> None:
        """Move or copy every photo the action applies to, as one step with one Undo. A photo that fails is left where it was
        (its whole bundle) and named at the end; the others still go."""
        destination = os.path.normpath(destination.strip()) if destination.strip() else ""
        rows = list(dict.fromkeys(rows))
        if len(rows) == 1:
            item, record = self._row(rows[0])
            picked = [(record, item.get("annotation"))] if record is not None else []
        else:
            picked = [(item["record"], item.get("annotation")) for item in (self._photos.row_at(r) for r in rows) if item is not None and item.get("record") is not None]
        if not picked or not destination:
            return
        folder = self._photos.folder_dir()
        many = len(picked) > 1
        if os.path.normcase(os.path.normpath(folder)) == os.path.normcase(destination):
            self._toast("warn", "That is the folder the photos are in" if many else "That is the folder the photo is in", "Pick another folder.")
            return
        what = _plural(len(picked)) if many else picked[0][0].name
        policy = self._collision_mode()
        self._start(f"{'Moving' if move else 'Copying'} {what}", lambda ctx: _work_transfer(ctx, picked, destination, move, policy),
                    lambda result: self._transferred(result, picked, destination, move), route=f"{folder} \u2192 {destination}", total=len(picked))

    def _transferred(self, result: dict, picked: list, destination: str, move: bool) -> None:
        done, failed = result["done"], result["failed"]            # done: [(record, moves, annotation)], the photos that went
        many = len(picked) > 1
        verb = "move" if move else "copy"
        skipped, renamed = result.get("skipped", []), result.get("renamed", [])
        if skipped:
            title = (f"{len(skipped)} of {len(picked)} photos were already there and were left where they are" if many
                     else f"{skipped[0]} is already in that folder")
            self._toast("warn", title, ("" if not many else ", ".join(skipped[:3]) + (f" and {len(skipped) - 3} more" if len(skipped) > 3 else "") + ". ")
                        + "Settings, General chooses whether a photo with a taken name is skipped or kept beside the other.")
        if renamed:
            self._toast("info", f"{len(renamed)} photo{'s were' if len(renamed) != 1 else ' was'} given a new name",
                        "The name was taken in that folder: " + ", ".join(renamed[:3]) + (f" and {len(renamed) - 3} more" if len(renamed) > 3 else "") + ".")
        if result.get("name_warnings"):
            self._toast("warn", "Some names may not work on Windows", "\n".join(result["name_warnings"][:3]))
        if result["edits_left"]:
            left = result["edits_left"]
            self._toast("warn", "Some edits did not move with the photo" if move else "Some edits were not copied with the photo",
                        ", ".join(left[:3]) + (f" and {len(left) - 3} more" if len(left) > 3 else ""))
        if failed:
            title = f"{len(failed)} of {len(picked)} photos could not be {'moved' if move else 'copied'}" if many else f"Could not {verb} {picked[0][0].name}"
            self._toast("error", title, _named(failed, not many))
        if result["cancelled"]:
            self._toast("info", f"Stopped after {'moving' if move else 'copying'} {len(done)} of {len(picked)}", "The ones already done stay where they are.")
        if not done:
            return
        self._remember(destination)
        where = _label(destination)
        what = f"{len(done)} photos" if len(done) != 1 else done[0][0].name
        if not move:
            entry = {}
            entry["id"] = self._photos.push_undo(f"copy of {what}", lambda: self._undo_copies(done))
            self._toast("success", f"Copied {what}", f"To {where}.", "Undo", lambda: self._photos.undo_entry(entry["id"]))
            return
        self._rekey_decisions([(record.path, record_after_moves(record, tuple(moves)), annotation) for record, moves, annotation in done])
        self._rekey_collections([(m.source_path, m.target_path) for _record, moves, _a in done for m in moves])
        self._photos.remove_paths([record.path for record, _m, _a in done])
        entry = {}
        entry["id"] = self._photos.push_undo(f"move of {what}", lambda: self._undo_moves(done))
        self._toast("success", f"Moved {what}", f"To {where}.", "Undo", lambda: self._photos.undo_entry(entry["id"]))

    def _undo_copies(self, done: list) -> bool:
        """Remove what a copy made: the copied files and the edit data copied with them, nothing that was there before and
        nothing changed since. ``done`` shrinks to what is still to be removed, so the step can be run again."""
        count = len(done)
        name = done[0][0].name if done else ""
        result = self._run_and_wait(f"Removing the copy of {name}" if count == 1 else f"Removing {count} copies",
                                    lambda ctx: _work_uncopy(ctx, list(done)), total=count)
        if result is None:
            return False
        removed, failed, kept = result["done"], result["failed"], result["kept"]
        gone = {id(entry) for entry in removed}
        done[:] = [entry for entry in done if id(entry) not in gone]
        if kept:
            self._toast("warn", "Some copies were kept", "Changed since they were copied: " + ", ".join(kept[:3]) + (f" and {len(kept) - 3} more" if len(kept) > 3 else ""))
        if failed:
            self._toast("error", f"Could not remove the copy of {failed[0][0]}" if count == 1 else "Could not remove the copies", _named(failed, count == 1))
        elif done:
            self._toast("info", f"Stopped after removing {len(removed)} of {count}", "Undo again removes the rest.")
        else:
            self._toast("success", f"Removed the copy of {name}" if count == 1 else f"Removed {count} copies")
        return not done

    def _undo_moves(self, done: list) -> bool:
        """Move photos back where they were. ``done`` shrinks to what is still away, so the step can be run again for the rest."""
        count = len(done)
        name = done[0][0].name if done else ""
        result = self._run_and_wait(f"Moving {name if count == 1 else _plural(count)} back",
                                    lambda ctx: _work_restore(ctx, list(done), "is no longer where it was moved to", carry_back=True), total=count)
        if result is None:
            return False
        back, failed = result["done"], result["failed"]
        if result["edits_left"]:
            left = result["edits_left"]
            self._toast("warn", "Some edits did not move with the photo", ", ".join(left[:3]) + (f" and {len(left) - 3} more" if len(left) > 3 else ""))
        if back:
            self._rekey_collections([(m.target_path, m.source_path) for _record, moves, _a in back for m in moves])
            self._rekey_decisions([(next((m.target_path for m in moves if m.source_path == record.path), ""), record, annotation) for record, moves, annotation in back])
            self._photos.reload()
        returned = {id(entry) for entry in back}
        done[:] = [entry for entry in done if id(entry) not in returned]
        if failed:
            self._toast("error", f"Could not move {failed[0][0]} back" if count == 1 else "Could not move them back",
                        _named(failed, count == 1) + (f" {_plural(len(back))} went back." if back else ""))
        elif done:
            self._toast("info", f"Stopped after moving back {len(back)} of {count}", "Undo again moves back the rest.")
        else:
            self._toast("success", f"Moved {name} back" if count == 1 else f"Moved {count} photos back")
        return not done

    # ---------------- questions ----------------

    def _ask(self, title: str, body: str, confirm: str, then) -> None:
        if self.asker is not None:
            if self.asker(title, body, confirm):
                then()
            return
        ident = f"q{self._next}"
        self._next += 1
        self._pending[ident] = then
        self.confirmRequested.emit(ident, title, body, confirm)

    @Slot(str, bool)
    def answer(self, ident: str, yes: bool) -> None:
        then = self._pending.pop(ident, None)
        if yes and then is not None:
            then()


def _move_bundle(bundle, root: str, moves: list) -> None:
    """Move one photo's files into ``root`` under names that keep them paired; ``moves`` fills as each file lands, so the caller can put them back."""
    present = tuple(path for path in bundle if os.path.exists(path))
    for source, target in zip(present, bundle_destinations(present, root)):
        shutil.move(source, target)
        moves.append(FileMove(source_path=source, target_path=target))


def _put_back(moves) -> list[str]:
    """Return moved files to where they came from after a failure; gives the names that could not be returned."""
    left: list[str] = []
    for done in reversed(list(moves)):
        try:
            if os.path.exists(done.source_path):
                raise OSError("a file with that name is there now")
            shutil.move(done.target_path, done.source_path)
        except OSError:
            left.append(Path(done.target_path).name)
    return left


def _with_leftovers(message: str, left: list[str], where: str) -> str:
    if not left:
        return message
    shown = ", ".join(left[:3]) + (f" and {len(left) - 3} more" if len(left) > 3 else "")
    return f"{message} These could not be put back and are in {where}: {shown}."


# ---------------- the file work itself: these run on the worker thread and touch nothing but files ----------------
# Each takes the job's context (`ctx.cancelled`, `ctx.step`) and returns plain data. Cancel is looked at between photos only, so a
# photo's bundle is either wholly handled or wholly where it was.

def _outcome(ctx, total: int, done: list, failed: list, **more) -> dict:
    ctx.step(total, "", total)
    return dict(done=done, failed=failed, cancelled=bool(ctx.cancelled and len(done) + len(failed) < total), **more)


def _work_safe_trash(ctx, items: list, recycle, root, session: str = "", limit: int = 0) -> dict:
    """Move each photo's bundle into the safe trash. A photo that fails is put back whole and named; the rest still go."""
    done: list[tuple] = []                                 # (record, moves, annotation)
    failed: list[tuple] = []                               # (name, why, the names that could not be put back)
    unrecorded: list[FileMove] = []
    photos: list[tuple] = []                               # what the Safe Trash window needs to show each photo and give it back whole

    def record_origins() -> None:
        if unrecorded:
            recycle.remember_recycle_origins(tuple(unrecorded))
            unrecorded.clear()
        if photos:
            safe_trash_store.remember_photos(root, photos)
            photos.clear()

    try:
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return _outcome(ctx, len(items), done, [(record.name, str(exc), []) for record, _annotation in items])
        for index, (record, annotation) in enumerate(items):
            if ctx.cancelled:
                break
            ctx.step(index, record.name)
            moves: list[FileMove] = []
            try:
                _move_bundle(delete_bundle(record), str(root), moves)
            except Exception as exc:                        # not only OSError: a bad name raises ValueError, and the rest still go
                failed.append((record.name, str(exc) or type(exc).__name__, _put_back(moves)))
                continue
            done.append((record, tuple(moves), annotation))
            unrecorded.extend(moves)
            photos.append((record.name, [m.target_path for m in moves], annotation, session))
            if len(unrecorded) >= 200:                      # where the files came from is written as it goes: a crash must not leave a trash nobody can restore from
                record_origins()
    finally:
        record_origins()
    evicted = 0
    if limit > 0 and done:
        try:                                                # the photos just deleted are never the ones the limit removes
            evicted = len(safe_trash_store.enforce_limit(root, limit, [m.target_path for _record, moves, _a in done for m in moves])["done"])
        except OSError:
            pass
    return _outcome(ctx, len(items), done, failed, evicted=evicted)


def _work_system_trash(ctx, items: list) -> dict:
    done, failed, stranded = [], [], []
    for index, (record, annotation) in enumerate(items):
        if ctx.cancelled:
            break
        ctx.step(index, record.name)
        ok, left = _trash_bundle(delete_bundle(record))
        (done if ok else failed).append((record, annotation))
        stranded.extend(left)
    return _outcome(ctx, len(items), done, failed, stranded=stranded)


def _work_remove(ctx, items: list) -> dict:
    """Delete for good. There is no putting back, so a photo that fails part way is named and the rest still go."""
    done, failed = [], []
    for index, (record, annotation) in enumerate(items):
        if ctx.cancelled:
            break
        ctx.step(index, record.name)
        try:
            for path in delete_bundle(record):
                if os.path.lexists(path):
                    _remove(path)
        except OSError as exc:
            failed.append((record.name, str(exc)))
            continue
        done.append((record, annotation))
    return _outcome(ctx, len(items), done, failed)


def _work_transfer(ctx, items: list, destination: str, move: bool, on_collision: str = "rename") -> dict:
    """Move or copy each photo's bundle, then its edit data. `move_paths` and `copy_paths` undo their own half-done bundle."""
    from ..file_ops import NameTaken

    done, failed, edits_left, name_warnings, skipped, renamed = [], [], [], [], [], []
    for index, (record, annotation) in enumerate(items):
        if ctx.cancelled:
            break
        ctx.step(index, record.name)
        try:
            bundle = record_paths(record)
            policy = {} if on_collision == "rename" else {"on_collision": on_collision}      # keeping both is what the calls do unasked
            moves = move_paths(bundle, destination, **policy) if move else copy_paths(bundle, destination, **policy)
        except NameTaken:
            skipped.append(record.name)                 # not a failure: the policy is to leave it where it is
            continue
        except Exception as exc:
            failed.append((record.name, str(exc) or type(exc).__name__))
            continue
        pairs = [(m.source_path, m.target_path) for m in moves]
        arrived = next((Path(target).name for source, target in pairs if source == record.path), "")
        if arrived and arrived != record.name:
            renamed.append(f"{record.name} \u2192 {arrived}")
        from ..file_ops import name_warning
        for _source, target in pairs:
            warning = name_warning(Path(target).name, destination)
            if warning:
                name_warnings.append(f"{Path(target).name}: {warning}")
        if move:
            edits_left.extend(carry_edits(pairs))
            done.append((record, moves, annotation))
            continue
        made: list[str] = []
        edits_left.extend(copy_edits(pairs, made))
        # what this copy made, each with its size and time now: undoing the copy removes these, and only while they are still as made
        created = [(path, _stamp(path)) for path in (*(m.target_path for m in moves), *made)]
        done.append((record, moves, annotation, created))
    return _outcome(ctx, len(items), done, failed, edits_left=edits_left, name_warnings=name_warnings, skipped=skipped, renamed=renamed)


def _work_uncopy(ctx, entries: list) -> dict:
    """Undo a copy: remove what the copy made and nothing else. A copied photo that has been changed since is kept, with the
    edit data copied for it, and named."""
    done, failed, kept = [], [], []
    roots: list[str] = []                                    # edit folders the copy made: removed at the end, when nothing else is in them
    for index, entry in enumerate(entries):
        if ctx.cancelled:
            break
        record, moves, created = entry[0], entry[1], entry[3]
        ctx.step(index, record.name)
        roots.extend(path for path, stamp in created if stamp is None and Path(path).name == EDIT_STORAGE_ROOT_NAME)
        stamps = dict(created)
        changed = [Path(m.target_path).name for m in moves if os.path.lexists(m.target_path) and not _unchanged(m.target_path, stamps.get(m.target_path))]
        if changed:
            kept.extend(changed)
            done.append(entry)
            continue
        try:
            for path, stamp in created:
                if not os.path.lexists(path) or Path(path).name == EDIT_STORAGE_ROOT_NAME:
                    continue
                if stamp is None:
                    shutil.rmtree(path)                      # a session's assets folder, copied whole
                elif _unchanged(path, stamp):
                    os.remove(path)
                else:
                    kept.append(Path(path).name)
        except OSError as exc:
            failed.append((record.name, str(exc)))
            continue
        done.append(entry)
    for path in dict.fromkeys(roots):
        try:
            os.rmdir(path)
        except OSError:
            pass
    return _outcome(ctx, len(entries), done, failed, kept=kept)


def _work_restore(ctx, entries: list, gone: str, *, recycle=None, carry_back: bool = False) -> dict:
    """Take photos back to where they were (out of the safe trash, or back from a move). ``entries`` is ``[(record, moves,
    annotation)]``; the ones that went back are returned as the same objects."""
    done, failed, edits_left = [], [], []
    try:
        for index, entry in enumerate(entries):
            if ctx.cancelled:
                break
            record, moves = entry[0], entry[1]
            ctx.step(index, record.name)
            try:
                _restore(moves, gone=gone)
            except OSError as exc:
                failed.append((record.name, str(exc)))
                continue
            if carry_back:
                edits_left.extend(carry_edits([(m.target_path, m.source_path) for m in moves]))
            done.append(entry)
    finally:
        if recycle is not None and done:
            safe_trash_store.forget_files(recycle.recycle_root_for_folder(), [m.target_path for _record, moves, *_rest in done for m in moves])
    return _outcome(ctx, len(entries), done, failed, edits_left=edits_left)
