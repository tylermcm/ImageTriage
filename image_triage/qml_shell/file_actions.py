"""Delete, move and copy photos: the shared file operations, with the same trash rules, companions and undo.

A photo is a bundle (the file, its RAW or JPEG companion and sidecars: `file_ops.record_paths`); every operation takes the whole bundle. Delete
follows the "Delete behavior" setting: **Safe Trash** moves the bundle into the app's recycle folder (a "recycle bin" folder beside a removable
drive's folder, otherwise the app's `safe-trash`) and can be undone; **System Trash** sends it to the operating system's trash (not undoable here, as
in the Widgets app, and with a question if the system refuses). A file inside a folder called "recycle bin" is deleted for good after a question.
A saved decision follows a moved photo (`DecisionStore.move_annotation`) and is dropped for a deleted one; undoing a delete puts both back.
Every step is on the photo model's undo list (Ctrl+Z, or the toast's button). `--mock` photos have no files: nothing is touched and a toast says so.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QFile, QObject, Signal, Slot

from ..edit_carry import carry_edits, edit_files_for
from ..file_ops import FileMove, bundle_destinations, copy_paths, move_paths, record_paths
from ..models import SessionAnnotation
from .decision_bridge import session_id

SAFE_TRASH = "Safe Trash (Undoable)"
RECENT_KEY = "folders/recent_destinations"


def delete_bundle(record) -> tuple[str, ...]:
    """Everything that goes when a photo is deleted: its files, its sidecars and its edit data (which would otherwise be left for a later file of the same name to inherit)."""
    paths = list(record_paths(record))
    seen = {os.path.normcase(os.path.normpath(p)) for p in paths}
    for image in record.stack_paths:
        for item in edit_files_for(image):
            key = os.path.normcase(os.path.normpath(str(item)))
            if key not in seen:
                seen.add(key)
                paths.append(str(item))
    return tuple(paths)


def _carry(moves, toast, *, back: bool = False) -> None:
    """Take the edit data along with photos that were just moved (or moved back)."""
    pairs = [(m.target_path, m.source_path) if back else (m.source_path, m.target_path) for m in moves]
    left = carry_edits(pairs)
    if left:
        toast("warn", "Some edits did not move with the photo", ", ".join(left[:3]) + (f" and {len(left) - 3} more" if len(left) > 3 else ""))


def _is_recycle_folder(folder: str) -> bool:
    return bool(folder) and any(part.casefold() == "recycle bin" for part in Path(folder).parts)


def rekey_after_move(record, moves, annotation) -> None:
    """A saved decision follows its photo to the new path (the Widgets' `rekey_annotation_after_move`)."""
    if annotation is None or annotation.is_empty:
        return
    from ..decision_store import DecisionStore
    from ..file_ops import record_after_moves

    DecisionStore().move_annotation(session_id(), record.path, record_after_moves(record, tuple(moves)), annotation)


def _label(path: str) -> str:
    parts = [p for p in str(path).replace("\\", "/").split("/") if p]
    return " / ".join(parts[-2:]) if parts else str(path)


class FileActions(QObject):
    confirmRequested = Signal(str, str, str, str)       # id, title, body, the confirm button's text
    recentChanged = Signal()

    def __init__(self, photos, toasts, memory: bool = False, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._photos = photos
        self._toasts = toasts
        self._memory = memory
        self._pending: dict[str, object] = {}           # confirmation id -> what to do when it is answered yes
        self._next = 1
        self.asker = None                                # fn(title, body, confirm) -> bool: asks at once instead of through the window (the full-screen viewer sets it)
        self._recent: list[str] = ["D:/Delivery/Album", "D:/Georgia trip/Picks"] if memory else []

    # ---------------- helpers ----------------
    def _settings(self):
        from ..app_identity import user_settings

        return user_settings()

    def _delete_mode(self) -> str:
        if self._memory:
            return SAFE_TRASH
        return str(self._settings().value("workflow/delete_mode", SAFE_TRASH, str) or SAFE_TRASH)

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

    def _rekey(self, record, moves: tuple[FileMove, ...], annotation: SessionAnnotation | None) -> None:
        rekey_after_move(record, moves, annotation)

    # ---------------- recent destinations ----------------
    def recent(self) -> list[str]:
        if self._memory:
            return list(self._recent)
        raw = self._settings().value(RECENT_KEY, [], list)
        if isinstance(raw, str):
            raw = [raw]
        here = os.path.normcase(os.path.normpath(self._photos.folder_dir())) if self._photos.folder_dir() else ""
        return [p for p in raw or [] if isinstance(p, str) and p and os.path.isdir(p) and os.path.normcase(os.path.normpath(p)) != here][:6]

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
        folder = self._photos.folder_dir()
        bundle = delete_bundle(record)
        name = record.name
        if _is_recycle_folder(folder):
            self._ask(f"Delete {name} permanently?", "It is in a recycle bin, so this cannot be undone.", "Delete", lambda: self._delete_for_good(record, bundle))
            return
        recycle = self._recycle(folder)
        if self._delete_mode() == SAFE_TRASH or recycle.is_temporary_storage_folder():
            self._safe_trash(item, record, bundle, recycle)
            return
        moved_all = True
        for path in bundle:
            if os.path.exists(path):
                handle = QFile(path)
                moved_all = (handle.moveToTrash() if hasattr(handle, "moveToTrash") else False) and moved_all
        if not moved_all:
            self._ask(f"Delete {name} permanently?", "It could not be moved to the trash.", "Delete", lambda: self._delete_for_good(record, bundle))
            return
        self._finish_delete(record)
        self._toast("success", f"Moved {name} to the trash", "Use the system trash to bring it back.")

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
        records = [item["record"] for _row, item in picked]
        count = len(records)
        folder = self._photos.folder_dir()
        if _is_recycle_folder(folder):
            self._ask(f"Delete {count} photos permanently?", "They are in a recycle bin, so this cannot be undone.", "Delete", lambda: self._delete_many_for_good(records))
            return
        if self._delete_mode() == SAFE_TRASH or self._recycle(folder).is_temporary_storage_folder():
            self.trashPaths([record.path for record in records], "")
            return
        trashed, failed = [], []
        for record in records:
            ok = True
            for path in delete_bundle(record):
                if os.path.exists(path):
                    handle = QFile(path)
                    ok = (handle.moveToTrash() if hasattr(handle, "moveToTrash") else False) and ok
            (trashed if ok else failed).append(record)
        self._finish_delete_many(trashed)
        if trashed:
            self._toast("success", f"Moved {len(trashed)} photo{'s' if len(trashed) != 1 else ''} to the trash", "Use the system trash to bring them back.")
        if failed:
            self._toast("error", f"{len(failed)} photo{'s' if len(failed) != 1 else ''} could not be moved to the trash", ", ".join(r.name for r in failed[:3]) + (" and more" if len(failed) > 3 else ""))

    def _delete_many_for_good(self, records: list) -> None:
        gone, failed = [], ""
        for record in records:
            try:
                for path in delete_bundle(record):
                    if os.path.exists(path):
                        os.remove(path)
            except OSError as exc:
                failed = f"{record.name}: {exc}"
                break
            gone.append(record)
        self._finish_delete_many(gone)
        if failed:
            self._toast("error", f"Stopped after deleting {len(gone)} of {len(records)}", failed)
        elif gone:
            self._toast("success", f"Deleted {len(gone)} photos for good")

    def _finish_delete_many(self, records: list) -> None:
        if not records:
            return
        if not self._memory:
            try:
                store = self._store()
                for record in records:
                    store.delete_annotation(session_id(), record.path)
            except Exception:
                pass
        self._photos.remove_paths([record.path for record in records])

    def _safe_trash(self, item: dict, record, bundle: tuple[str, ...], recycle) -> None:
        root = recycle.recycle_root_for_folder()
        moves: list[FileMove] = []
        try:
            root.mkdir(parents=True, exist_ok=True)
            _move_bundle(bundle, str(root), moves)
        except OSError as exc:
            left = _put_back(moves)
            self._toast("error", f"Could not delete {record.name}", _with_leftovers(str(exc), left, str(root)))
            return
        recycle.remember_recycle_origins(tuple(moves))
        annotation = item.get("annotation")
        self._finish_delete(record)
        entry = {}
        entry["id"] = self._photos.push_undo(f"delete of {record.name}", lambda: self._undo_delete(record, tuple(moves), annotation, recycle))
        self._toast("success", f"Deleted {record.name}", "It is in the safe trash.", "Undo", lambda: self._photos.undo_entry(entry["id"]))

    @Slot("QVariantList", str, result=int)
    def trashPaths(self, paths, why: str = "") -> int:
        """Move these photos (with their companions) to the safe trash as one step: one toast, one Undo. Returns the undo step's id (0 when nothing moved)."""
        wanted = {os.path.normcase(os.path.normpath(str(p))) for p in paths}
        rows = [r for r in self._photos.all_rows() if os.path.normcase(os.path.normpath(r["path"])) in wanted and r.get("record") is not None]
        if not rows:
            return 0
        recycle = self._recycle(self._photos.folder_dir())
        root = recycle.recycle_root_for_folder()
        moved: list[tuple] = []                                   # (record, moves, annotation)
        moves: list[FileMove] = []                                # the photo being moved, kept apart so a failure half way through it is put back too
        try:
            root.mkdir(parents=True, exist_ok=True)
            for row in rows:
                moves = []
                _move_bundle(delete_bundle(row["record"]), str(root), moves)
                moved.append((row["record"], tuple(moves), row.get("annotation")))
                moves = []
        except OSError as exc:
            left = _put_back([*(m for _record, done, _a in moved for m in done), *moves])
            self._toast("error", "Could not move them to the recycle bin", _with_leftovers(str(exc), left, str(root)))
            return 0
        recycle.remember_recycle_origins(tuple(m for _r, moves, _a in moved for m in moves))
        for record, _moves, _a in moved:
            if not self._memory:
                try:
                    self._store().delete_annotation(session_id(), record.path)
                except Exception:
                    pass
        self._photos.remove_paths([record.path for record, _m, _a in moved])
        entry = {}
        entry["id"] = self._photos.push_undo(f"move of {len(moved)} photos to the recycle bin", lambda: self._undo_trash(moved, recycle))
        self._toast("success", f"Moved {len(moved)} photo{'s' if len(moved) != 1 else ''} to the recycle bin", "Undo brings them back.", "Undo", lambda: self._photos.undo_entry(entry["id"]))
        return entry["id"]

    def _undo_trash(self, moved: list, recycle) -> bool:
        try:
            for _record, moves, _a in moved:
                _restore(moves)
        except OSError as exc:
            self._toast("error", "Could not bring them back", str(exc))
            return False
        recycle.forget_recycle_origins(tuple(m.target_path for _r, moves, _a in moved for m in moves))
        for record, _moves, annotation in moved:
            if annotation is not None and not annotation.is_empty:
                self._store().save_annotation(session_id(), record, annotation)
        self._photos.reload()
        self._toast("success", f"Brought back {len(moved)} photo{'s' if len(moved) != 1 else ''}")
        return True

    def _undo_delete(self, record, moves: tuple[FileMove, ...], annotation, recycle) -> bool:
        try:
            _restore(moves, gone="is no longer in the safe trash")
        except OSError as exc:
            self._toast("error", f"Could not bring back {record.name}", str(exc))
            return False
        recycle.forget_recycle_origins(tuple(m.target_path for m in moves))
        if annotation is not None and not annotation.is_empty:
            self._store().save_annotation(session_id(), record, annotation)
        self._photos.reload()
        self._toast("success", f"Brought back {record.name}")
        return True

    def _delete_for_good(self, record, bundle: tuple[str, ...]) -> None:
        try:
            for path in bundle:
                if os.path.exists(path):
                    os.remove(path)
        except OSError as exc:
            self._toast("error", f"Could not delete {record.name}", str(exc))
            return
        self._finish_delete(record)
        self._toast("success", f"Deleted {record.name} for good")

    def _finish_delete(self, record) -> None:
        if not self._memory:
            try:
                self._store().delete_annotation(session_id(), record.path)
            except Exception:
                pass
        self._photos.remove_paths([record.path])

    # ---------------- move and copy ----------------
    @Slot(int, str)
    def moveRow(self, row: int, destination: str) -> None:
        self._transfer(row, destination, move=True)

    @Slot(int, str)
    def copyRow(self, row: int, destination: str) -> None:
        self._transfer(row, destination, move=False)

    @Slot("QVariantList", str)
    def moveRows(self, rows, destination: str) -> None:
        self._transfer_many([int(r) for r in rows], destination, move=True)

    @Slot("QVariantList", str)
    def copyRows(self, rows, destination: str) -> None:
        self._transfer_many([int(r) for r in rows], destination, move=False)

    def _transfer(self, row: int, destination: str, *, move: bool) -> None:
        self._transfer_many([row], destination, move=move)

    def _transfer_many(self, rows: list[int], destination: str, *, move: bool) -> None:
        """Move or copy every photo the action applies to, as one step with one Undo. Stops at the first failure and says how far it got."""
        destination = os.path.normpath(destination.strip()) if destination.strip() else ""
        rows = list(dict.fromkeys(rows))
        if len(rows) == 1:
            item, record = self._row(rows[0])
            picked = [(item, record)] if record is not None else []
        else:
            picked = [(item, item["record"]) for item in (self._photos.row_at(r) for r in rows) if item is not None and item.get("record") is not None]
        if not picked or not destination:
            return
        folder = self._photos.folder_dir()
        many = len(picked) > 1
        if os.path.normcase(os.path.normpath(folder)) == os.path.normcase(destination):
            self._toast("warn", "That is the folder the photos are in" if many else "That is the folder the photo is in", "Pick another folder.")
            return
        verb = "move" if move else "copy"
        done: list[tuple] = []                                     # (record, moves, annotation)
        failure = ""
        for item, record in picked:
            try:
                bundle = record_paths(record)
                moves = move_paths(bundle, destination) if move else copy_paths(bundle, destination)
            except OSError as exc:
                failure = f"{record.name}: {exc}" if many else str(exc)
                break
            done.append((record, moves, item.get("annotation")))
        if failure:
            title = f"Stopped after {'moving' if move else 'copying'} {len(done)} of {len(picked)}" if many else f"Could not {verb} {picked[0][1].name}"
            self._toast("error", title, failure)
        if not done:
            return
        self._remember(destination)
        where = _label(destination)
        what = f"{len(done)} photos" if len(done) != 1 else done[0][0].name
        if not move:
            if not failure or many:
                self._toast("success", f"Copied {what}", f"To {where}.")
            return
        for record, moves, annotation in done:
            self._rekey(record, moves, annotation)
            _carry(moves, self._toast)
        self._photos.remove_paths([record.path for record, _m, _a in done])
        entry = {}
        entry["id"] = self._photos.push_undo(f"move of {what}", lambda: self._undo_moves(done))
        self._toast("success", f"Moved {what}", f"To {where}.", "Undo", lambda: self._photos.undo_entry(entry["id"]))

    def _undo_moves(self, done: list) -> bool:
        if len(done) == 1:
            return self._undo_move(*done[0])
        try:
            for _record, moves, _annotation in done:
                _restore(moves, gone="is no longer where it was moved to")
        except OSError as exc:
            self._toast("error", "Could not move them back", str(exc))
            return False
        for _record, moves, _annotation in done:
            _carry(moves, self._toast, back=True)
        for record, moves, annotation in done:
            if annotation is not None and not annotation.is_empty:
                moved = next((m.target_path for m in moves if m.source_path == record.path), "")
                if moved:
                    self._store().move_annotation(session_id(), moved, record, annotation)
        self._photos.reload()
        self._toast("success", f"Moved {len(done)} photos back")
        return True

    def _undo_move(self, record, moves: tuple[FileMove, ...], annotation) -> bool:
        try:
            _restore(moves, gone="is no longer where it was moved to")
        except OSError as exc:
            self._toast("error", f"Could not move {record.name} back", str(exc))
            return False
        _carry(moves, self._toast, back=True)
        if annotation is not None and not annotation.is_empty:
            moved = next((m.target_path for m in moves if m.source_path == record.path), "")
            if moved:
                from ..file_ops import record_after_moves

                self._store().move_annotation(session_id(), moved, record, annotation)
        self._photos.reload()
        self._toast("success", f"Moved {record.name} back")
        return True

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


def _restore(moves, gone: str = "is no longer there") -> None:
    """Take files back to their original places. Never replaces a file that is there now, and can be run again after a partial failure."""
    for move in moves:
        if not os.path.exists(move.target_path):
            if os.path.exists(move.source_path):
                continue                                  # already back from an earlier attempt
            raise OSError(f"{Path(move.target_path).name} {gone}")
        if os.path.exists(move.source_path):
            raise OSError(f"A file named {Path(move.source_path).name} is already in the original folder")
        Path(move.source_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.move(move.target_path, move.source_path)
