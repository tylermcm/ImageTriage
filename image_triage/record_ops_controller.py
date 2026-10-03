from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QDir, QFile
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from .annotation_queue import WinnerSyncRequest
from .file_ops import FileMove, copy_paths, move_paths, rename_bundle_paths
from .models import DeleteMode, ImageRecord, SessionAnnotation, WinnerMode
from .scanner import normalize_filesystem_path, normalized_path_key
from .transfer_progress import TransferItem, run_file_transfer
from .ui import confirm_transfer, show_transfer_complete

if TYPE_CHECKING:
    from .window import MainWindow


@dataclass(slots=True)
class UndoAction:
    """Captures the minimum state needed to reverse one destructive user action."""
    kind: str
    primary_path: str
    file_moves: tuple[FileMove, ...] = ()
    original_winner: bool = False
    original_reject: bool = False
    original_photoshop: bool = False
    rating: int = 0
    tags: tuple[str, ...] = ()
    original_review_round: str = ""
    folder: str = ""
    source_paths: tuple[str, ...] = ()
    session_id: str = ""
    winner_mode: str = ""
    # Actions pushed from the same batch operation (e.g. one multi-file move)
    # share a non-empty batch_id, so a single Undo reverses all of them and
    # does one view refresh, not one per file.
    batch_id: str = ""


class RecordOpsController:
    """Move/copy/delete/rename orchestration for image records, and the
    undo stack that reverses it. Holds no state of its own - every
    attribute it reads or writes (`_undo_stack`, `_annotations`,
    `_records_repo`, `_decision_store`, `_current_folder`, ...) is shared
    with the rest of MainWindow, so this is a pure orchestration layer over
    the `window` back-reference, the same pattern used by the file-ops
    controllers before it. The one piece of real state ownership this WI
    introduced - the record list itself - lives in `RecordsRepository`
    (see records_repository.py); this controller calls into it rather than
    touching `_all_records`/`_all_records_by_path` directly."""

    def __init__(self, window: "MainWindow") -> None:
        self._window = window

    # -- Single-record move/copy/delete/restore -------------------------

    def remove_record(self, index: int) -> None:
        window = self._window
        if not 0 <= index < len(window._records):
            return
        record = window._records[index]
        window._records_repo.remove_paths((record.path,))
        next_path = window._next_visible_path(index)
        if next_path == record.path:
            next_path = None
        if window._current_folder:
            window._persist_folder_record_cache(window._current_folder, window._all_records, source="window-remove")
        window._apply_records_view(current_path=next_path)

    def remove_records_by_paths(self, paths: list[str]) -> int:
        """Remove several records with a single view refresh (WI-4.1b),
        instead of calling `remove_record` once per path and rebuilding the
        view each time. Focus lands on the next surviving record after the
        last one removed, falling back to the nearest surviving record
        before the first one removed, matching `remove_record`'s
        next-then-previous neighbour preference."""
        window = self._window
        indices = sorted({window._record_index_for_path(path) for path in paths} - {None})
        if not indices:
            return 0
        next_path = self.next_visible_path_after_batch_removal(indices)
        removed_paths = {window._records[index].path for index in indices}
        window._records_repo.remove_paths(removed_paths)
        if window._current_folder:
            window._persist_folder_record_cache(window._current_folder, window._all_records, source="window-remove-batch")
        window._apply_records_view(current_path=next_path)
        return len(indices)

    def next_visible_path_after_batch_removal(self, sorted_indices: list[int]) -> str | None:
        window = self._window
        if not window._records:
            return None
        for index in range(sorted_indices[-1] + 1, len(window._records)):
            return window._records[index].path
        for index in range(sorted_indices[0] - 1, -1, -1):
            return window._records[index].path
        return None

    def delete_record(self, index: int) -> None:
        window = self._window
        record = window._record_at(index)
        if record is None:
            return
        if not window._current_folder:
            window.statusBar().showMessage("Open a real folder to delete files. Virtual scopes are non-destructive views.")
            return

        bundle_paths = window._record_paths(record)
        annotation = window._annotations.get(record.path, SessionAnnotation())
        if window._is_recycle_folder():
            confirmation = QMessageBox.question(
                window,
                "Delete Permanently?",
                f"Permanently delete {record.name} from the recycle bin?\n\nThis cannot be undone.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirmation != QMessageBox.StandardButton.Yes:
                return
            try:
                self.delete_paths_permanently(bundle_paths)
            except OSError as exc:
                QMessageBox.warning(window, "Delete Failed", f"Could not permanently delete {record.name}.\n\n{exc}")
                return
            window._forget_recycle_origins(bundle_paths)
            window._decision_store.delete_annotation(window._session_id, record.path)
            window._annotations.pop(record.path, None)
            self.remove_record(index)
            window._refresh_recycle_button()
            window.statusBar().showMessage(f"Permanently deleted {record.name}")
            return

        try:
            trash_moves: tuple[FileMove, ...] = ()
            use_safe_trash = window._delete_mode == DeleteMode.SAFE_TRASH or window._is_temporary_storage_folder()
            if use_safe_trash:
                trash_moves = self.move_bundle_to_recycle(bundle_paths)
                window._remember_recycle_origins(trash_moves)
            else:
                moved_all = self.trash_or_delete_paths(bundle_paths)
                if not moved_all:
                    confirmation = QMessageBox.question(
                        window,
                        "Delete Permanently?",
                        f"Could not move this file set to the trash.\n\nDelete permanently?\n\n{record.name}",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    if confirmation != QMessageBox.StandardButton.Yes:
                        return
                    self.delete_paths_permanently(bundle_paths)
        except OSError as exc:
            QMessageBox.warning(window, "Delete Failed", f"Could not delete {record.name}.\n\n{exc}")
            return

        if use_safe_trash:
            self.push_undo(
                UndoAction(
                    kind="delete",
                    primary_path=record.path,
                    file_moves=trash_moves,
                    original_winner=annotation.winner,
                    original_reject=annotation.reject,
                    original_photoshop=annotation.photoshop,
                    rating=annotation.rating,
                    tags=annotation.tags,
                    original_review_round=annotation.review_round,
                    folder=window._current_folder,
                    source_paths=bundle_paths,
                    session_id=window._session_id,
                    winner_mode=window._winner_mode.value,
                )
            )

        window._decision_store.delete_annotation(window._session_id, record.path)
        window._annotations.pop(record.path, None)
        self.remove_record(index)
        window._refresh_recycle_button()
        if use_safe_trash:
            if window._is_temporary_storage_folder():
                window.statusBar().showMessage(f"Moved {record.name} to this drive's recycle bin")
            else:
                window.statusBar().showMessage(f"Safely removed {record.name}")
        else:
            window.statusBar().showMessage(f"Removed {record.name}")

    def keep_record(self, index: int) -> None:
        window = self._window
        record = window._record_at(index)
        if record is None:
            return
        if not window._current_folder:
            window.statusBar().showMessage("Open a real folder to move files. Collections and catalog views do not move originals.")
            return

        keep_dir = os.path.join(window._current_folder, "_keep")
        os.makedirs(keep_dir, exist_ok=True)
        try:
            moves = self.move_bundle(window._record_paths(record), keep_dir)
        except OSError as exc:
            QMessageBox.warning(window, "Move Failed", f"Could not move {record.name}.\n\n{exc}")
            return
        self.rekey_annotation_after_move(record, moves)
        self.push_undo(
            UndoAction(
                kind="move",
                primary_path=record.path,
                file_moves=moves,
                folder=window._current_folder,
                session_id=window._session_id,
            )
        )
        self.remove_record(index)
        window.statusBar().showMessage(f"Moved {record.name} to _keep")

    def move_record_prompt(self, index: int) -> None:
        window = self._window
        record = window._record_at(index)
        if record is None:
            return
        if not window._current_folder:
            window.statusBar().showMessage("Open a real folder to move files. Virtual scopes are browse-only for file moves.")
            return

        destination_dir = QFileDialog.getExistingDirectory(window, "Move Selected Image", window._current_folder or QDir.homePath())
        if not destination_dir:
            return

        try:
            moves = self.move_bundle(window._record_paths(record), destination_dir)
        except OSError as exc:
            QMessageBox.warning(window, "Move Failed", f"Could not move {record.name}.\n\n{exc}")
            return
        self.rekey_annotation_after_move(record, moves)
        self.push_undo(
            UndoAction(
                kind="move",
                primary_path=record.path,
                file_moves=moves,
                folder=window._current_folder,
                session_id=window._session_id,
            )
        )
        window._remember_recent_destination(destination_dir)
        self.remove_record(index)
        window.statusBar().showMessage(f"Moved {record.name} to {destination_dir}")

    # -- Path-indirection adapters ---------------------------------------

    def delete_record_by_path(self, path: str) -> bool:
        window = self._window
        index = window._record_index_for_path(path)
        if index is None:
            return False
        self.delete_record(index)
        return window._record_index_for_path(path) is None

    def copy_record_to_path(self, path: str, destination_dir: str) -> bool:
        window = self._window
        index = window._record_index_for_path(path)
        if index is None:
            return False
        return self.copy_record_to(index, destination_dir)

    def keep_record_by_path(self, path: str) -> bool:
        window = self._window
        index = window._record_index_for_path(path)
        if index is None:
            return False
        self.keep_record(index)
        return window._record_index_for_path(path) is None

    def restore_record_by_path(self, path: str) -> bool:
        window = self._window
        index = window._record_index_for_path(path)
        if index is None:
            return False
        self.restore_record(index)
        return window._record_index_for_path(path) is None

    def copy_record_to(self, index: int, destination_dir: str) -> bool:
        window = self._window
        record = window._record_at(index)
        if record is None:
            return False
        try:
            self.copy_bundle(window._record_paths(record), destination_dir)
        except OSError as exc:
            QMessageBox.warning(window, "Copy Failed", f"Could not copy {record.name}.\n\n{exc}")
            return False
        window._remember_recent_destination(destination_dir)
        window.statusBar().showMessage(f"Copied {record.name} to {destination_dir}")
        return True

    def move_record_to(
        self, index: int, destination_dir: str, *, defer_removal: bool = False, batch_id: str = ""
    ) -> bool:
        window = self._window
        record = window._record_at(index)
        if record is None:
            return False

        try:
            moves = self.move_bundle(window._record_paths(record), destination_dir)
        except OSError as exc:
            QMessageBox.warning(window, "Move Failed", f"Could not move {record.name}.\n\n{exc}")
            return False
        self.rekey_annotation_after_move(record, moves)
        self.push_undo(
            UndoAction(
                kind="move",
                primary_path=record.path,
                file_moves=moves,
                folder=window._current_folder,
                session_id=window._session_id,
                batch_id=batch_id,
            )
        )
        window._remember_recent_destination(destination_dir)
        if not defer_removal:
            self.remove_record(index)
        return True

    def restore_record(self, index: int) -> None:
        window = self._window
        record = window._record_at(index)
        if record is None:
            return
        if not window._is_recycle_folder():
            return

        try:
            restores = window._restore_bundle(window._record_paths(record))
        except OSError as exc:
            QMessageBox.warning(window, "Restore Failed", f"Could not restore {record.name}.\n\n{exc}")
            return
        if not restores:
            QMessageBox.warning(window, "Restore Failed", f"Could not restore {record.name}.")
            return
        self.remove_record(index)
        window._refresh_recycle_button()
        window.statusBar().showMessage(f"Restored {record.name}")

    def move_record_to_path(
        self, path: str, destination_dir: str, *, defer_removal: bool = False, batch_id: str = ""
    ) -> bool:
        window = self._window
        index = window._record_index_for_path(path)
        if index is None:
            return False
        return self.move_record_to(index, destination_dir, defer_removal=defer_removal, batch_id=batch_id)

    def move_record_to_ai_recycle(self, index: int, *, defer_removal: bool = False, batch_id: str = "") -> bool:
        window = self._window
        record = window._record_at(index)
        if record is None:
            return False
        if not window._current_folder:
            window.statusBar().showMessage("Open a real folder to move files into the program recycle bin.")
            return False

        bundle_paths = window._record_paths(record)
        annotation = window._annotations.get(record.path, SessionAnnotation())
        try:
            trash_moves = self.move_bundle_to_recycle(bundle_paths)
            window._remember_recycle_origins(trash_moves)
        except OSError as exc:
            QMessageBox.warning(window, "Recycle Failed", f"Could not move {record.name} into the program recycle bin.\n\n{exc}")
            return False

        self.push_undo(
            UndoAction(
                kind="delete",
                primary_path=record.path,
                file_moves=trash_moves,
                original_winner=annotation.winner,
                original_reject=annotation.reject,
                original_photoshop=annotation.photoshop,
                rating=annotation.rating,
                tags=annotation.tags,
                original_review_round=annotation.review_round,
                folder=window._current_folder,
                source_paths=bundle_paths,
                session_id=window._session_id,
                winner_mode=window._winner_mode.value,
                batch_id=batch_id,
            )
        )
        window._decision_store.delete_annotation(window._session_id, record.path)
        window._annotations.pop(record.path, None)
        if not defer_removal:
            self.remove_record(index)
        window._refresh_recycle_button()
        return True

    def move_record_to_ai_recycle_by_path(
        self, path: str, *, defer_removal: bool = False, batch_id: str = ""
    ) -> bool:
        window = self._window
        index = window._record_index_for_path(path)
        if index is None:
            return False
        return self.move_record_to_ai_recycle(index, defer_removal=defer_removal, batch_id=batch_id)

    # -- Batch move/copy/delete -------------------------------------------

    def copy_records_by_paths(
        self, primary_paths: list[str], destination_dir: str, *, include_companions: bool = True
    ) -> int:
        window = self._window
        items: list[TransferItem] = []
        records: dict[int, ImageRecord] = {}
        for position, path in enumerate(primary_paths):
            index = window._record_index_for_path(path)
            record = window._record_at(index) if index is not None else None
            if record is None:
                continue
            bundle = window._record_paths(record) if include_companions else (record.path,)
            items.append(TransferItem(position, record.name, bundle))
            records[position] = record
        if not items:
            return 0

        result = run_file_transfer(
            window, items, destination_dir, source_label=window._current_folder or "", verb="Copying", keep_source=True
        )
        copied = sum(1 for item in items if item.key in result.moved)
        if copied:
            window._remember_recent_destination(destination_dir)
        if result.failed:
            first_key = next(iter(result.failed))
            QMessageBox.warning(
                window,
                "Copy Failed",
                f"Could not copy {len(result.failed)} item(s)." + chr(10) + chr(10)
                + f"{records[first_key].name}: {result.failed[first_key]}",
            )
        return copied

    def move_records_by_paths(
        self,
        primary_paths: list[str],
        destination_dir: str,
        *,
        batch_id: str = "",
        include_companions: bool = True,
    ) -> int:
        window = self._window
        items: list[TransferItem] = []
        records: dict[int, ImageRecord] = {}
        for position, path in enumerate(primary_paths):
            index = window._record_index_for_path(path)
            record = window._record_at(index) if index is not None else None
            if record is None:
                continue
            bundle = window._record_paths(record) if include_companions else (record.path,)
            items.append(TransferItem(position, record.name, bundle))
            records[position] = record
        if not items:
            return 0

        result = run_file_transfer(
            window, items, destination_dir, source_label=window._current_folder or ""
        )
        # An externally-supplied batch_id (e.g. from _apply_ai_culling, which
        # needs this move to join a batch shared with its Reject/recycle
        # half) joins that batch instead of minting a fresh one; every other
        # caller (drag-drop, the regular batch-move action) omits it and gets
        # the same freshly-minted-per-call id as before.
        batch_id = batch_id or uuid.uuid4().hex
        moved = 0
        moved_paths: list[str] = []
        for item in items:
            moves = result.moved.get(item.key)
            if moves is None:
                continue
            record = records[item.key]
            self.rekey_annotation_after_move(record, moves)
            self.push_undo(
                UndoAction(
                    kind="move",
                    primary_path=record.path,
                    file_moves=moves,
                    folder=window._current_folder,
                    session_id=window._session_id,
                    batch_id=batch_id,
                )
            )
            moved_paths.append(record.path)
            moved += 1
        if moved_paths:
            self.remove_records_by_paths(moved_paths)
        if moved:
            window._remember_recent_destination(destination_dir)
        if result.failed:
            first_key = next(iter(result.failed))
            QMessageBox.warning(
                window,
                "Move Failed",
                f"Could not move {len(result.failed)} item(s)." + chr(10) + chr(10)
                + f"{records[first_key].name}: {result.failed[first_key]}",
            )
        return moved

    def handle_record_drop(self, primary_paths: list[str], destination_dir: str, *, copy_requested: bool) -> None:
        window = self._window
        if window._collection_mode:
            return
        normalized_destination = normalize_filesystem_path(destination_dir)
        if not normalized_destination or window._dir_confirmed_missing(normalized_destination):
            return
        if not window._current_folder or normalized_path_key(normalized_destination) == normalized_path_key(window._current_folder):
            window.statusBar().showMessage("Choose a different folder to drop these images into.")
            return

        unique_paths: list[str] = []
        seen: set[str] = set()
        for path in primary_paths:
            key = normalized_path_key(path)
            if key in seen or window._record_index_for_path(path) is None:
                continue
            seen.add(key)
            unique_paths.append(path)
        if not unique_paths:
            return

        action_label = "Copied" if copy_requested else "Moved"
        if copy_requested:
            count = self.copy_records_by_paths(unique_paths, normalized_destination)
        else:
            count = self.move_records_by_paths(unique_paths, normalized_destination)
        window.statusBar().showMessage(f"{action_label} {count} image(s) to {normalized_destination}")

    def batch_copy_records(self, records: list[ImageRecord]) -> None:
        window = self._window
        if not records:
            return
        destination_dir = QFileDialog.getExistingDirectory(window, "Copy Selected Images", window._current_folder or QDir.homePath())
        if not destination_dir:
            return
        self._confirm_and_transfer(records, destination_dir, mode="copy")

    def batch_move_records(self, records: list[ImageRecord]) -> None:
        window = self._window
        if not records:
            return
        destination_dir = QFileDialog.getExistingDirectory(window, "Move Selected Images", window._current_folder or QDir.homePath())
        if not destination_dir:
            return
        self._confirm_and_transfer(records, destination_dir, mode="move")

    def batch_delete_records(self, records: list[ImageRecord]) -> None:
        window = self._window
        if not records:
            return
        deleted = sum(1 for record in records if self.delete_record_by_path(record.path))
        window.statusBar().showMessage(f"Removed {deleted} image(s)")

    def move_selected_records_to_destination(self, destination_dir: str) -> None:
        window = self._window
        records = window._selected_records_for_actions()
        self._confirm_and_transfer(records, destination_dir, mode="move")

    def copy_selected_records_to_destination(self, destination_dir: str) -> None:
        window = self._window
        records = window._selected_records_for_actions()
        self._confirm_and_transfer(records, destination_dir, mode="copy")

    def _confirm_and_transfer(self, records: list[ImageRecord], destination_dir: str, *, mode: str) -> None:
        """Shared confirm -> transfer -> completion flow for Move/Copy To
        Folder and To Recent Destination (WI-4.7). Drag-drop and single-record
        context-menu actions deliberately don't go through this -- they're
        already a visible, deliberate choice of destination, unlike picking a
        folder from a file dialog or a "recent destinations" submenu."""
        window = self._window
        if not records:
            return
        verb = "Copy" if mode == "copy" else "Move"
        include_default = window._settings.value(window.TRANSFER_INCLUDE_COMPANIONS_KEY, True, bool)
        confirmed, include_companions = confirm_transfer(
            window,
            verb=verb,
            count=len(records),
            destination=destination_dir,
            include_companions_default=include_default,
        )
        if not confirmed:
            return
        window._settings.setValue(window.TRANSFER_INCLUDE_COMPANIONS_KEY, include_companions)
        primary_paths = window._primary_paths_for_records(records)
        if mode == "copy":
            count = self.copy_records_by_paths(primary_paths, destination_dir, include_companions=include_companions)
            verb_past = "Copied"
        else:
            count = self.move_records_by_paths(primary_paths, destination_dir, include_companions=include_companions)
            verb_past = "Moved"
        window.statusBar().showMessage(f"{verb_past} {count} image(s) to {destination_dir}")
        if count >= 2:
            show_transfer_complete(window, verb_past=verb_past, count=count, destination=destination_dir)

    def batch_move_records_to_new_folder(self, records: list[ImageRecord]) -> None:
        window = self._window
        if not records or not window._current_folder:
            return
        destination_dir = window._create_folder_prompt(window._current_folder, select_created=False)
        if not destination_dir:
            return
        moved = self.move_records_by_paths(window._primary_paths_for_records(records), destination_dir)
        window.statusBar().showMessage(f"Moved {moved} image(s) to {destination_dir}")

    # -- Rename ------------------------------------------------------------

    def rename_record_prompt(self, index: int) -> str | None:
        window = self._window
        record = window._record_at(index)
        if record is None or window._is_recycle_folder() or window._is_winners_folder():
            return None

        requested_name, accepted = QInputDialog.getText(
            window,
            "Rename Image",
            "File name",
            text=record.name,
        )
        if not accepted:
            return None
        requested_name = (requested_name or "").strip()
        if not requested_name:
            return None
        return self.rename_record(index, requested_name)

    def rename_record(self, index: int, requested_name: str) -> str | None:
        window = self._window
        record = window._record_at(index)
        if record is None:
            return None

        try:
            moves = rename_bundle_paths(window._record_paths(record), record.path, requested_name)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(window, "Rename Failed", f"Could not rename {record.name}.\n\n{exc}")
            return None
        if not moves:
            return record.path

        self.rekey_annotation_after_move(record, moves)
        self.push_undo(
            UndoAction(
                kind="move",
                primary_path=record.path,
                file_moves=moves,
                folder=window._current_folder,
                session_id=window._session_id,
            )
        )
        renamed_record = self.record_after_moves(record, moves)
        self.replace_record(record.path, renamed_record)
        window._reset_filter_metadata_index(window._all_records)
        window._apply_records_view(current_path=renamed_record.path)
        window.statusBar().showMessage(f"Renamed {record.name} to {renamed_record.name}")
        return renamed_record.path

    @staticmethod
    def record_after_moves(record: ImageRecord, moves: tuple[FileMove, ...]) -> ImageRecord:
        moved_paths = {move.source_path: move.target_path for move in moves}
        return ImageRecord(
            path=moved_paths.get(record.path, record.path),
            name=Path(moved_paths.get(record.path, record.path)).name,
            size=record.size,
            modified_ns=record.modified_ns,
            companion_paths=tuple(moved_paths.get(path, path) for path in record.companion_paths),
            edited_paths=tuple(moved_paths.get(path, path) for path in record.edited_paths),
            variants=tuple(
                type(variant)(
                    path=moved_paths.get(variant.path, variant.path),
                    name=Path(moved_paths.get(variant.path, variant.path)).name,
                    size=variant.size,
                    modified_ns=variant.modified_ns,
                )
                for variant in record.variants
            ),
        )

    def replace_record(self, original_path: str, record: ImageRecord) -> None:
        window = self._window
        window._records_repo.replace_by_old_path({original_path: record})
        if window._current_folder:
            window._persist_folder_record_cache(window._current_folder, window._all_records, source="window-replace")

    def replace_records_after_moves(self, records_by_old_path: dict[str, ImageRecord]) -> None:
        window = self._window
        if not records_by_old_path:
            return
        window._records_repo.replace_by_old_path(records_by_old_path)
        if window._current_folder:
            window._persist_folder_record_cache(window._current_folder, window._all_records, source="window-move")

    def rekey_filter_metadata_after_moves(self, records_by_old_path: dict[str, ImageRecord]) -> None:
        self._window._records_view.rekey_filter_metadata_after_moves(records_by_old_path)

    def rekey_annotation_after_move(
        self,
        record: ImageRecord,
        moves: tuple[FileMove, ...],
        *,
        annotation_override: SessionAnnotation | None = None,
        update_live_cache: bool = True,
    ) -> None:
        window = self._window
        annotation = annotation_override
        if annotation is None:
            annotation = window._annotations.pop(record.path, None)
        elif update_live_cache:
            window._annotations.pop(record.path, None)
        if annotation is None:
            return
        if annotation.is_empty:
            window._decision_store.delete_annotation(window._session_id, record.path)
            return
        new_primary_path = next((move.target_path for move in moves if move.source_path == record.path), "")
        if not new_primary_path:
            if update_live_cache:
                window._annotations[record.path] = annotation
            return
        moved_record = window._record_from_path(new_primary_path)
        if moved_record is None:
            if update_live_cache:
                window._annotations[record.path] = annotation
            return
        if update_live_cache:
            window._annotations[new_primary_path] = annotation
        window._decision_store.move_annotation(window._session_id, record.path, moved_record, annotation)

    # -- Undo stack ----------------------------------------------------

    def push_undo(self, action: UndoAction) -> None:
        window = self._window
        window._undo_stack.append(action)
        window._update_action_states()

    def push_undo_actions(self, actions: list[UndoAction]) -> None:
        window = self._window
        if not actions:
            return
        window._undo_stack.extend(actions)
        window._update_action_states()

    def undo_last_action(self) -> None:
        window = self._window
        if not window._undo_stack:
            return

        action = window._undo_stack.pop()
        batch = [action]
        if action.batch_id:
            while window._undo_stack and window._undo_stack[-1].batch_id == action.batch_id:
                batch.append(window._undo_stack.pop())
        batch.reverse()  # undo oldest-pushed-first, matching how they happened

        if not window._undo_stack:
            window._update_action_states()

        reload_needed = False
        undone = 0
        try:
            for item in batch:
                if item.kind == "annotation":
                    self.undo_annotation(item)
                elif item.kind == "move":
                    if self.undo_move_files(item):
                        reload_needed = True
                elif item.kind == "delete":
                    if self.undo_delete_files(item):
                        reload_needed = True
                undone += 1
        except OSError as exc:
            # Push back whatever this batch hadn't gotten to yet, in the
            # order they'd be undone next.
            window._undo_stack.extend(reversed(batch[undone:]))
            window._update_action_states()
            if reload_needed:
                window._load_folder(window._current_folder)
            QMessageBox.warning(window, "Undo Failed", f"Could not undo the last action.\n\n{exc}")
            return

        if reload_needed:
            window._load_folder(window._current_folder)
        self.show_undo_batch_message(batch)

    def show_undo_batch_message(self, batch: list[UndoAction]) -> None:
        window = self._window
        if len(batch) == 1:
            action = batch[0]
            name = Path(action.primary_path).name
            if action.kind == "move":
                window.statusBar().showMessage(f"Undid move: {name}")
            elif action.kind == "delete":
                window.statusBar().showMessage(f"Restored {name} from safe trash")
            elif action.kind == "annotation":
                window.statusBar().showMessage(f"Undid annotation change: {name}")
            else:
                window.statusBar().showMessage(f"Undid {action.kind}: {name}")
            return
        kind_labels = {"move": "move", "delete": "restore from safe trash", "annotation": "annotation change"}
        kind_counts: dict[str, int] = {}
        for action in batch:
            kind_counts[action.kind] = kind_counts.get(action.kind, 0) + 1
        parts = [
            f"{count} {kind_labels.get(kind, kind)}{'s' if count != 1 else ''}"
            for kind, count in kind_counts.items()
        ]
        window.statusBar().showMessage(f"Undid {len(batch)} action(s): " + ", ".join(parts))

    def undo_annotation(self, action: UndoAction) -> None:
        window = self._window
        previous_annotation = window._annotation_snapshot(
            window._annotations.get(action.primary_path, SessionAnnotation())
        )
        annotation = self.annotation_from_action(action)
        if annotation.is_empty:
            window._annotations.pop(action.primary_path, None)
        else:
            window._annotations[action.primary_path] = annotation
        mode_override = None
        for mode in WinnerMode:
            if action.winner_mode in {mode.name, mode.value}:
                mode_override = mode
                break
        winner_sync = None
        if not window._is_winners_folder(action.folder):
            winner_sync = WinnerSyncRequest(
                winner_enabled=action.original_winner,
                folder=action.folder,
                winner_mode=mode_override or window._winner_mode,
                source_paths=action.source_paths,
            )
        record = window._all_records_by_path.get(action.primary_path) or window._record_from_path(action.primary_path)
        if record is not None:
            window._queue_annotation_persist(
                record,
                previous_annotation=previous_annotation,
                session_id=action.session_id or window._session_id,
                winner_sync=winner_sync,
            )
            window._sync_annotation_to_global_adapter_label(record, annotation)
        window._set_annotation_views()
        window._apply_records_view(current_path=action.primary_path)

    def undo_move_files(self, action: UndoAction) -> bool:
        """Restore this action's files and rekey its annotation. Returns
        whether the current folder needs reloading to show the result -
        callers batch this across several actions into one reload."""
        window = self._window
        for file_move in action.file_moves:
            target = Path(file_move.target_path)
            original = Path(file_move.source_path)
            if not target.exists():
                raise OSError(f"Moved file no longer exists: {target}")
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(original))
        target_primary = next((move.target_path for move in action.file_moves if move.source_path == action.primary_path), "")
        annotation = window._annotations.pop(target_primary, None) if target_primary else None
        if annotation is not None:
            restored_record = window._record_from_path(action.primary_path)
            if restored_record is not None:
                window._annotations[action.primary_path] = annotation
                window._decision_store.move_annotation(action.session_id or window._session_id, target_primary, restored_record, annotation)
        destination_dirs = {str(Path(file_move.target_path).parent) for file_move in action.file_moves}
        return window._current_folder == action.folder or window._current_folder in destination_dirs

    def undo_delete_files(self, action: UndoAction) -> bool:
        """Restore this action's files and rekey its annotation. Returns
        whether the current folder needs reloading to show the result -
        callers batch this across several actions into one reload."""
        window = self._window
        for file_move in action.file_moves:
            target = Path(file_move.target_path)
            original = Path(file_move.source_path)
            if not target.exists():
                raise OSError(f"Deleted file no longer exists in safe trash: {target}")
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(target), str(original))

        window._forget_recycle_origins(tuple(file_move.target_path for file_move in action.file_moves))
        annotation = self.annotation_from_action(action)
        restored_record = window._record_from_path(action.primary_path)
        if restored_record is not None:
            if annotation.is_empty:
                window._annotations.pop(action.primary_path, None)
            else:
                window._annotations[action.primary_path] = annotation
            window._queue_annotation_persist(restored_record, session_id=action.session_id or window._session_id)

        if window._current_folder == action.folder:
            return True
        window._set_annotation_views()
        window._update_status()
        window._refresh_recycle_button()
        return False

    @staticmethod
    def annotation_from_action(action: UndoAction) -> SessionAnnotation:
        return SessionAnnotation(
            winner=action.original_winner,
            reject=action.original_reject,
            photoshop=action.original_photoshop,
            rating=action.rating,
            tags=action.tags,
            review_round=action.original_review_round,
        )

    # -- Pure filesystem bundle helpers ------------------------------------

    @staticmethod
    def move_bundle(source_paths: tuple[str, ...], destination_dir: str) -> tuple[FileMove, ...]:
        return move_paths(source_paths, destination_dir)

    def move_bundle_to_recycle(self, source_paths: tuple[str, ...]) -> tuple[FileMove, ...]:
        window = self._window
        recycle_root = window._recycle_root_for_folder()
        recycle_root.mkdir(parents=True, exist_ok=True)
        file_moves: list[FileMove] = []
        moved_targets: list[FileMove] = []
        try:
            for source_path in source_paths:
                source = Path(source_path)
                destination = Path(window._unique_destination(str(recycle_root), source.name))
                shutil.move(str(source), str(destination))
                file_move = FileMove(source_path=str(source), target_path=str(destination))
                file_moves.append(file_move)
                moved_targets.append(file_move)
        except OSError as exc:
            for moved in reversed(moved_targets):
                if os.path.exists(moved.target_path):
                    Path(moved.source_path).parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(moved.target_path, moved.source_path)
            raise exc
        return tuple(file_moves)

    @staticmethod
    def copy_bundle(source_paths: tuple[str, ...], destination_dir: str) -> tuple[FileMove, ...]:
        return copy_paths(source_paths, destination_dir)

    @staticmethod
    def trash_or_delete_paths(source_paths: tuple[str, ...]) -> bool:
        moved_all = True
        for source_path in source_paths:
            if not os.path.exists(source_path):
                continue
            file = QFile(source_path)
            moved = file.moveToTrash() if hasattr(file, "moveToTrash") else False
            moved_all = moved_all and moved
        return moved_all

    @staticmethod
    def delete_paths_permanently(source_paths: tuple[str, ...]) -> None:
        for source_path in source_paths:
            if os.path.exists(source_path):
                os.remove(source_path)
