"""Delete, move and copy photos: the Widgets app's own file operations, with the same trash rules, companions and undo.

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

from ..file_ops import FileMove, copy_paths, move_paths, record_paths, unique_destination
from ..models import SessionAnnotation
from .decision_bridge import session_id

SAFE_TRASH = "Safe Trash (Undoable)"
RECENT_KEY = "folders/recent_destinations"


def _is_recycle_folder(folder: str) -> bool:
    return bool(folder) and any(part.casefold() == "recycle bin" for part in Path(folder).parts)


def rekey_after_move(record, moves, annotation) -> None:
    """A saved decision follows its photo to the new path (the Widgets' `rekey_annotation_after_move`)."""
    if annotation is None or annotation.is_empty:
        return
    from ..decision_store import DecisionStore
    from ..record_ops_controller import RecordOpsController

    DecisionStore().move_annotation(session_id(), record.path, RecordOpsController.record_after_moves(record, tuple(moves)), annotation)


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
        from ..recycle_bin_controller import RecycleBinController

        shim = SimpleNamespace(_folder_session=SimpleNamespace(folder=folder), _is_recycle_folder=lambda f=None: _is_recycle_folder(f or folder), _unique_destination=unique_destination, actions=None)
        return RecycleBinController(shim)

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
        bundle = record_paths(record)
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

    def _safe_trash(self, item: dict, record, bundle: tuple[str, ...], recycle) -> None:
        root = recycle.recycle_root_for_folder()
        moves: list[FileMove] = []
        try:
            root.mkdir(parents=True, exist_ok=True)
            for source in bundle:
                if not os.path.exists(source):
                    continue
                target = unique_destination(str(root), Path(source).name)
                shutil.move(source, target)
                moves.append(FileMove(source_path=source, target_path=target))
        except OSError as exc:
            for done in reversed(moves):                      # put back what was already moved
                try:
                    shutil.move(done.target_path, done.source_path)
                except OSError:
                    pass
            self._toast("error", f"Could not delete {record.name}", str(exc))
            return
        recycle.remember_recycle_origins(tuple(moves))
        annotation = item.get("annotation")
        self._finish_delete(record)
        entry = {}
        entry["id"] = self._photos.push_undo(f"delete of {record.name}", lambda: self._undo_delete(record, tuple(moves), annotation, recycle))
        self._toast("success", f"Deleted {record.name}", "It is in the safe trash.", "Undo", lambda: self._photos.undo_entry(entry["id"]))

    def _undo_delete(self, record, moves: tuple[FileMove, ...], annotation, recycle) -> bool:
        try:
            for move in moves:
                if not os.path.exists(move.target_path):
                    raise OSError(f"{Path(move.target_path).name} is no longer in the safe trash")
                Path(move.source_path).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(move.target_path, move.source_path)
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

    def _transfer(self, row: int, destination: str, *, move: bool) -> None:
        item, record = self._row(row)
        destination = os.path.normpath(destination.strip()) if destination.strip() else ""
        if record is None or not destination:
            return
        folder = self._photos.folder_dir()
        if os.path.normcase(os.path.normpath(folder)) == os.path.normcase(destination):
            self._toast("warn", "That is the folder the photo is in", "Pick another folder.")
            return
        bundle = record_paths(record)
        try:
            moves = move_paths(bundle, destination) if move else copy_paths(bundle, destination)
        except OSError as exc:
            self._toast("error", f"Could not {'move' if move else 'copy'} {record.name}", str(exc))
            return
        self._remember(destination)
        where = _label(destination)
        if not move:
            self._toast("success", f"Copied {record.name}", f"To {where}.")
            return
        annotation = item.get("annotation")
        self._rekey(record, moves, annotation)
        self._photos.remove_paths([record.path])
        entry = {}
        entry["id"] = self._photos.push_undo(f"move of {record.name}", lambda: self._undo_move(record, moves, annotation))
        self._toast("success", f"Moved {record.name}", f"To {where}.", "Undo", lambda: self._photos.undo_entry(entry["id"]))

    def _undo_move(self, record, moves: tuple[FileMove, ...], annotation) -> bool:
        try:
            for move in moves:
                if not os.path.exists(move.target_path):
                    raise OSError(f"{Path(move.target_path).name} is no longer where it was moved to")
                Path(move.source_path).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(move.target_path, move.source_path)
        except OSError as exc:
            self._toast("error", f"Could not move {record.name} back", str(exc))
            return False
        if annotation is not None and not annotation.is_empty:
            moved = next((m.target_path for m in moves if m.source_path == record.path), "")
            if moved:
                from ..record_ops_controller import RecordOpsController

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
