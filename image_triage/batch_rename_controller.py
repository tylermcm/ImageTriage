from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QProgressDialog

from .batch_rename import BatchRenameApplyTask, BatchRenamePreview
from .job_controller import JobSpec
from .models import SessionAnnotation
from .scanner import normalized_path_key
from .folder_session import FolderSession

if TYPE_CHECKING:
    from .window import MainWindow


@dataclass(slots=True)
class BatchRenameExecutionContext:
    """Tracks rename-task state that must survive async completion handlers."""
    preview: BatchRenamePreview
    folder: str
    is_current_folder: bool
    loaded_annotations: dict[str, SessionAnnotation]
    current_path_before: str | None = None


class BatchRenameApplyController:
    """Runs a batch-rename preview on the background pool and drives its
    progress dialog. Owns only the task/dialog lifecycle; the actual record
    and annotation rekeying (`MainWindow._finalize_batch_rename`) stays on
    the window, since it reaches into core view-model state this controller
    has no business touching."""

    @property
    def _session(self) -> FolderSession:
        return self._window._folder_session

    def __init__(self, window: "MainWindow") -> None:
        self._window = window
        self._active_task: BatchRenameApplyTask | None = None
        self._context: BatchRenameExecutionContext | None = None
        self._progress_dialog: QProgressDialog | None = None

    @property
    def is_running(self) -> bool:
        return self._active_task is not None

    def apply_preview(self, preview: BatchRenamePreview, *, folder: str) -> bool:
        window = self._window
        if not preview.planned_moves:
            return False
        if self._active_task is not None:
            QMessageBox.information(window, "Batch Rename Running", "A batch rename is already in progress.")
            return False
        renamed_items = [item for item in preview.items if item.status == "Rename"]
        is_current_folder = normalized_path_key(folder) == normalized_path_key(self._session.folder)
        loaded_annotations: dict[str, SessionAnnotation] = {}
        if not is_current_folder:
            loaded_annotations = window._decision_store.load_annotations(
                self._session.session_id, [item.record for item in renamed_items]
            )
        self._context = BatchRenameExecutionContext(
            preview=preview,
            folder=folder,
            is_current_folder=is_current_folder,
            loaded_annotations=loaded_annotations,
            current_path_before=window._records_view.current_visible_record_path() if is_current_folder else None,
        )
        task = BatchRenameApplyTask(preview.planned_moves)
        task.signals.started.connect(self._handle_started, Qt.ConnectionType.QueuedConnection)
        task.signals.progress.connect(self._handle_progress, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self._handle_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self._handle_failed, Qt.ConnectionType.QueuedConnection)
        self._active_task = task
        window._batch_rename_pool.start(task)
        window.statusBar().showMessage(f"Applying batch rename for {len(renamed_items)} image bundle(s)...")
        return True

    def _handle_started(self, total_steps: int) -> None:
        dialog = self._show_progress_dialog(total_steps)
        dialog.setLabelText("Preparing batch rename...")

    def _handle_progress(self, current: int, total: int, message: str) -> None:
        dialog = self._show_progress_dialog(total)
        self._window._export_jobs.update_progress_dialog(
            dialog,
            current=current,
            total=total,
            message=message,
            default_label="Applying batch rename...",
        )

    def _handle_finished(self, _applied_moves: object) -> None:
        window = self._window
        context = self._context
        dialog = self._progress_dialog
        if dialog is not None:
            dialog.setRange(0, 0)
            dialog.setValue(0)
            dialog.setLabelText("Updating library...")

        try:
            if context is not None:
                window._finalize_batch_rename(context)
        except Exception as exc:
            QMessageBox.warning(window, "Batch Rename Finalize Failed", f"The files were renamed, but the library refresh failed.\n\n{exc}")
        finally:
            self._active_task = None
            self._context = None
            self._close_progress_dialog()

    def _handle_failed(self, message: str) -> None:
        window = self._window
        self._active_task = None
        self._context = None
        self._close_progress_dialog()
        QMessageBox.warning(window, "Batch Rename Failed", f"Could not apply the batch rename.\n\n{message}")

    def _show_progress_dialog(self, total_steps: int) -> QProgressDialog:
        dialog = self._window._export_jobs.show_job_progress_dialog(
            key="batch_rename",
            total_steps=total_steps,
            spec=JobSpec(
                title="Batch Rename",
                preparing_label="Preparing batch rename...",
                running_label="Applying batch rename...",
                indeterminate_label="Updating library...",
                window_modality=Qt.WindowModality.WindowModal,
                stays_on_top=False,
            ),
        )
        self._progress_dialog = dialog
        return dialog

    def _close_progress_dialog(self) -> None:
        self._window._export_jobs.close_job_progress_dialog("batch_rename")
        self._progress_dialog = None
