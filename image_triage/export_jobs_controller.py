"""Resize, convert, workflow export and archive: their dialogs, background tasks, progress dialogs and results, plus saved-recipe runs. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""
from __future__ import annotations

import os

from PySide6.QtCore import QDir, QObject, Qt
from PySide6.QtWidgets import QFileDialog, QMessageBox, QProgressDialog
from pathlib import Path

from .archive_ops import EXTRACT_ARCHIVE_FILTER, CreateArchiveTask, ExtractArchiveTask, archive_format_for_key, ensure_archive_suffix
from .formats import FITS_SUFFIXES, MODEL_SUFFIXES, RAW_SUFFIXES, suffix_for_path
from .image_convert import ConvertApplyTask, ConvertOptions, ConvertPlan, ConvertSourceItem
from .image_resize import ResizeApplyTask, ResizeOptions, ResizePlan, ResizeSourceItem
from .job_controller import JobController, JobSpec
from .models import ImageRecord
from .scanner import normalize_filesystem_path, normalized_path_key
from .tasks.job_contexts import ArchiveExecutionContext, ConvertExecutionContext, ResizeExecutionContext, WorkflowExecutionContext
from .ui import ConvertDialog, ResizeDialog
from .workflows import RECIPE_TRANSFER_ARCHIVE, RECIPE_TRANSFER_MOVE, WorkflowExportPlan, WorkflowExportTask, WorkflowRecipe, build_workflow_export_plan, workflow_archive_path, workflow_destination_dir, workflow_record_folder_name

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .window import MainWindow


class ExportJobsController(QObject):
    """Resize, convert, workflow export and archive: their dialogs, background tasks, progress dialogs and results, plus saved-recipe runs. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self._window = window
        self._active_archive_task: CreateArchiveTask | ExtractArchiveTask | None = None
        self._active_convert_task: ConvertApplyTask | None = None
        self._active_resize_task: ResizeApplyTask | None = None
        self._active_workflow_export_task: WorkflowExportTask | None = None
        self._archive_context: ArchiveExecutionContext | None = None
        self._archive_progress_dialog: QProgressDialog | None = None
        self._convert_context: ConvertExecutionContext | None = None
        self._convert_progress_dialog: QProgressDialog | None = None
        self._resize_context: ResizeExecutionContext | None = None
        self._workflow_context: WorkflowExecutionContext | None = None
        self._workflow_progress_dialog: QProgressDialog | None = None

    def open_resize_dialog(
        self,
        sources: list[ResizeSourceItem],
        *,
        title: str,
        scope_label: str,
        show_preview: bool | None = None,
        raw_note: str = "",
    ) -> bool:
        if not sources:
            return False
        dialog = ResizeDialog(
            sources,
            title=title,
            scope_label=scope_label,
            show_preview=show_preview,
            raw_note=raw_note,
            parent=self._window,
        )
        if self._window._exec_dialog_with_geometry(dialog, "resize") != dialog.DialogCode.Accepted:
            return False
        plan = dialog.accepted_plan()
        if not plan.can_apply:
            return False
        options = dialog.accepted_options()
        return self.apply_resize_plan(
            plan,
            options,
            refresh_folder=self.resize_refresh_folder(plan),
        )

    def open_convert_dialog(
        self,
        sources: list[ConvertSourceItem],
        *,
        title: str,
        scope_label: str,
        show_preview: bool | None = None,
        raw_note: str = "",
    ) -> bool:
        if not sources:
            return False
        dialog = ConvertDialog(
            sources,
            title=title,
            scope_label=scope_label,
            show_preview=show_preview,
            raw_note=raw_note,
            parent=self._window,
        )
        if self._window._exec_dialog_with_geometry(dialog, "convert") != dialog.DialogCode.Accepted:
            return False
        plan = dialog.accepted_plan()
        if not plan.can_apply:
            return False
        options = dialog.accepted_options()
        return self.apply_convert_plan(
            plan,
            options,
            refresh_folder=self.resize_refresh_folder(plan),
        )

    def resize_refresh_folder(self, plan: ResizePlan | ConvertPlan) -> str:
        if not self._window._current_folder:
            return ""
        current_folder_key = normalized_path_key(self._window._current_folder)
        for item in plan.executable_items:
            source_folder = str(Path(item.source.source_path).parent)
            target_folder = str(Path(item.target_path).parent)
            if normalized_path_key(source_folder) == current_folder_key:
                return self._window._current_folder
            if normalized_path_key(target_folder) == current_folder_key:
                return self._window._current_folder
        return ""

    def apply_resize_plan(
        self,
        plan: ResizePlan,
        options: ResizeOptions,
        *,
        refresh_folder: str = "",
    ) -> bool:
        if not plan.executable_items:
            return False
        if self._active_resize_task is not None:
            QMessageBox.information(self._window, "Resize Running", "A resize task is already in progress.")
            return False
        dialog = self.show_resize_progress_dialog(max(1, len(plan.executable_items)))
        dialog.setLabelText("Preparing resize...")
        self._resize_context = ResizeExecutionContext(
            plan=plan,
            options=options,
            refresh_folder=refresh_folder,
        )
        task = ResizeApplyTask(plan, options)
        task.signals.started.connect(self.handle_resize_started, Qt.ConnectionType.QueuedConnection)
        task.signals.progress.connect(self.handle_resize_progress, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self.handle_resize_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self.handle_resize_failed, Qt.ConnectionType.QueuedConnection)
        self._active_resize_task = task
        self._window._resize_pool.start(task)
        self._window.statusBar().showMessage(f"Applying resize for {len(plan.executable_items)} image(s)...")
        return True

    def apply_convert_plan(
        self,
        plan: ConvertPlan,
        options: ConvertOptions,
        *,
        refresh_folder: str = "",
    ) -> bool:
        if not plan.executable_items:
            return False
        if self._active_convert_task is not None:
            QMessageBox.information(self._window, "Convert Running", "A convert task is already in progress.")
            return False
        dialog = self.show_convert_progress_dialog(max(1, len(plan.executable_items)))
        dialog.setLabelText("Preparing conversion...")
        self._convert_context = ConvertExecutionContext(
            plan=plan,
            options=options,
            refresh_folder=refresh_folder,
        )
        task = ConvertApplyTask(plan, options)
        task.signals.started.connect(self.handle_convert_started, Qt.ConnectionType.QueuedConnection)
        task.signals.progress.connect(self.handle_convert_progress, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self.handle_convert_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self.handle_convert_failed, Qt.ConnectionType.QueuedConnection)
        self._active_convert_task = task
        self._window._convert_pool.start(task)
        self._window.statusBar().showMessage(f"Applying convert for {len(plan.executable_items)} image(s)...")
        return True

    def workflow_refresh_folder(self, plan: WorkflowExportPlan) -> str:
        if not self._window._current_folder:
            return ""
        current_folder_key = normalized_path_key(self._window._current_folder)
        for item in plan.executable_items:
            target_folder = str(Path(item.target_path).parent)
            if normalized_path_key(target_folder) == current_folder_key:
                return self._window._current_folder
        return ""

    def start_workflow_export_task(self, plan: WorkflowExportPlan) -> bool:
        if not plan.executable_items:
            return False
        if self._active_workflow_export_task is not None:
            QMessageBox.information(self._window, "Workflow Running", "A deliver / handoff export is already in progress.")
            return False
        dialog = self.show_workflow_progress_dialog(max(1, len(plan.executable_items)))
        dialog.setLabelText("Preparing workflow export...")
        destination_root = plan.destination_dir
        if plan.recipe.destination_subfolder:
            destination_root = str(Path(plan.destination_dir).parent)
        self._workflow_context = WorkflowExecutionContext(
            recipe=plan.recipe,
            action="export",
            destination_root=destination_root,
            destination_dir=plan.destination_dir,
            refresh_folder=self.workflow_refresh_folder(plan),
            archive_after_export=plan.recipe.archive_after_export,
            archive_format=plan.recipe.archive_format,
        )
        task = WorkflowExportTask(plan)
        task.signals.started.connect(self.handle_workflow_export_started, Qt.ConnectionType.QueuedConnection)
        task.signals.progress.connect(self.handle_workflow_export_progress, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self.handle_workflow_export_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self.handle_workflow_export_failed, Qt.ConnectionType.QueuedConnection)
        self._active_workflow_export_task = task
        self._window._workflow_export_pool.start(task)
        self._window.statusBar().showMessage(f"Running export recipe: {plan.recipe.name}")
        return True

    def handle_workflow_export_started(self, total_steps: int) -> None:
        dialog = self.show_workflow_progress_dialog(total_steps)
        dialog.setLabelText("Preparing workflow export...")

    def handle_workflow_export_progress(self, current: int, total: int, message: str) -> None:
        dialog = self.show_workflow_progress_dialog(total)
        self.update_progress_dialog(
            dialog,
            current=current,
            total=total,
            message=message,
            default_label="Saving workflow outputs...",
        )

    def handle_workflow_export_finished(self, written_paths: object) -> None:
        context = self._workflow_context
        written = tuple(path for path in written_paths if isinstance(path, str)) if isinstance(written_paths, (list, tuple)) else ()
        dialog = self._workflow_progress_dialog
        if dialog is not None and context is not None and (context.refresh_folder or context.archive_after_export):
            dialog.setRange(0, 0)
            dialog.setValue(0)
            if context.archive_after_export:
                dialog.setLabelText("Packaging archive...")
            else:
                dialog.setLabelText("Refreshing library...")

        self._active_workflow_export_task = None
        self._workflow_context = None
        self.close_workflow_progress_dialog()

        if context is None:
            self._window.statusBar().showMessage(f"Exported {len(written)} image(s)")
            return

        if context.destination_dir:
            self._window._navigation.remember_recent_destination(context.destination_dir)
        elif context.destination_root:
            self._window._navigation.remember_recent_destination(context.destination_root)

        if context.archive_after_export and written:
            archive_path = self.workflow_archive_path(context.recipe, context.destination_root or context.destination_dir)
            if archive_path:
                self.start_archive_create_task(
                    written,
                    archive_path,
                    archive_key=context.archive_format,
                    root_dir=context.destination_dir or None,
                    refresh_folder=context.refresh_folder,
                    archive_label=f"workflow archive for {context.recipe.name}",
                )
                self._window.statusBar().showMessage(f"Exported {len(written)} image(s), packaging archive...")
                return

        if context.refresh_folder:
            self._window.statusBar().showMessage(f"Exported {len(written)} image(s), refreshing folder...")
            self._window._scan.load_folder(context.refresh_folder, force_refresh=True)
            return

        self._window.statusBar().showMessage(f"Exported {len(written)} image(s) with recipe: {context.recipe.name}")

    def handle_workflow_export_failed(self, message: str) -> None:
        self._active_workflow_export_task = None
        self._workflow_context = None
        self.close_workflow_progress_dialog()
        QMessageBox.warning(self._window, "Workflow Export Failed", f"Could not apply the workflow export.\n\n{message}")

    def handle_resize_started(self, total_steps: int) -> None:
        dialog = self.show_resize_progress_dialog(total_steps)
        dialog.setLabelText("Preparing resize...")

    def handle_resize_progress(self, current: int, total: int, message: str) -> None:
        dialog = self.show_resize_progress_dialog(total)
        self.update_progress_dialog(
            dialog,
            current=current,
            total=total,
            message=message,
            default_label="Saving resized images...",
        )

    def handle_resize_finished(self, written_paths: object) -> None:
        context = self._resize_context
        written = tuple(path for path in written_paths if isinstance(path, str)) if isinstance(written_paths, (list, tuple)) else ()
        dialog = self._window._resize_progress_dialog
        if dialog is not None and context is not None and context.refresh_folder:
            dialog.setRange(0, 0)
            dialog.setValue(0)
            dialog.setLabelText("Refreshing library...")

        self._active_resize_task = None
        self._resize_context = None
        self.close_resize_progress_dialog()

        if context is not None and context.refresh_folder:
            self._window.statusBar().showMessage(f"Resized {len(written)} image(s), refreshing folder...")
            self._window._scan.load_folder(context.refresh_folder, force_refresh=True)
            return

        self._window.statusBar().showMessage(f"Resized {len(written)} image(s)")

    def handle_resize_failed(self, message: str) -> None:
        self._active_resize_task = None
        self._resize_context = None
        self.close_resize_progress_dialog()
        QMessageBox.warning(self._window, "Resize Failed", f"Could not resize the selected image(s).\n\n{message}")

    def handle_convert_started(self, total_steps: int) -> None:
        dialog = self.show_convert_progress_dialog(total_steps)
        dialog.setLabelText("Preparing conversion...")

    def handle_convert_progress(self, current: int, total: int, message: str) -> None:
        dialog = self.show_convert_progress_dialog(total)
        self.update_progress_dialog(
            dialog,
            current=current,
            total=total,
            message=message,
            default_label="Saving converted images...",
        )

    def handle_convert_finished(self, written_paths: object) -> None:
        context = self._convert_context
        written = tuple(path for path in written_paths if isinstance(path, str)) if isinstance(written_paths, (list, tuple)) else ()
        dialog = self._convert_progress_dialog
        if dialog is not None and context is not None and context.refresh_folder:
            dialog.setRange(0, 0)
            dialog.setValue(0)
            dialog.setLabelText("Refreshing library...")

        self._active_convert_task = None
        self._convert_context = None
        self.close_convert_progress_dialog()

        if context is not None and context.refresh_folder:
            self._window.statusBar().showMessage(f"Converted {len(written)} image(s), refreshing folder...")
            self._window._scan.load_folder(context.refresh_folder, force_refresh=True)
            return

        self._window.statusBar().showMessage(f"Converted {len(written)} image(s)")

    def handle_convert_failed(self, message: str) -> None:
        self._active_convert_task = None
        self._convert_context = None
        self.close_convert_progress_dialog()
        QMessageBox.warning(self._window, "Convert Failed", f"Could not convert the selected image(s).\n\n{message}")

    def handle_archive_started(self, total_steps: int) -> None:
        context = self._archive_context
        dialog = self.show_archive_progress_dialog(total_steps, title="Extract Archive" if context and context.mode == "extract" else "Create Archive")
        dialog.setLabelText("Preparing archive..." if context and context.mode == "create" else "Preparing extraction...")

    def handle_archive_progress(self, current: int, total: int, message: str) -> None:
        context = self._archive_context
        dialog = self.show_archive_progress_dialog(total, title="Extract Archive" if context and context.mode == "extract" else "Create Archive")
        if context is not None and context.mode == "extract":
            self.update_progress_dialog(
                dialog,
                current=current,
                total=total,
                message=message,
                default_label="Extracting archive...",
            )
        else:
            self.update_progress_dialog(
                dialog,
                current=current,
                total=total,
                message=message,
                default_label="Creating archive...",
            )

    def handle_archive_finished(self, result: object) -> None:
        context = self._archive_context
        extracted = tuple(path for path in result if isinstance(path, str)) if isinstance(result, (list, tuple)) else ()
        created_path = result if isinstance(result, str) else ""
        dialog = self._archive_progress_dialog
        if dialog is not None and context is not None and context.refresh_folder:
            dialog.setRange(0, 0)
            dialog.setValue(0)
            dialog.setLabelText("Refreshing library...")

        self._active_archive_task = None
        self._archive_context = None
        self.close_archive_progress_dialog()

        if context is not None and context.mode == "extract":
            if context.destination_dir:
                self._window._navigation.remember_recent_destination(context.destination_dir)
            if context.refresh_folder:
                self._window.statusBar().showMessage(f"Extracted {len(extracted)} item(s), refreshing folder...")
                self._window._scan.load_folder(context.refresh_folder, force_refresh=True)
                return
            self._window.statusBar().showMessage(f"Extracted {len(extracted)} item(s) to {context.destination_dir}")
            return

        if created_path:
            self._window._navigation.remember_recent_destination(str(Path(created_path).parent))
            label = context.archive_label if context is not None and context.archive_label else "archive"
            if context is not None and context.refresh_folder:
                self._window.statusBar().showMessage(f"Created {label} {Path(created_path).name}, refreshing folder...")
                self._window._scan.load_folder(context.refresh_folder, force_refresh=True)
                return
            self._window.statusBar().showMessage(f"Created {label} {Path(created_path).name}")
            return

        self._window.statusBar().showMessage("Archive complete")

    def handle_archive_failed(self, message: str) -> None:
        context = self._archive_context
        mode = context.mode if context is not None else "create"
        archive_label = context.archive_label if context is not None else "archive"
        self._active_archive_task = None
        self._archive_context = None
        self.close_archive_progress_dialog()
        if mode == "extract":
            QMessageBox.warning(self._window, "Extract Archive Failed", f"Could not extract the archive.\n\n{message}")
            return
        extra_note = ""
        if archive_label.startswith("workflow archive") and context is not None and context.destination_dir:
            extra_note = f"\n\nThe exported files were kept in:\n{context.destination_dir}"
        QMessageBox.warning(self._window, "Create Archive Failed", f"Could not create the archive.\n\n{message}{extra_note}")

    def show_job_progress_dialog(self, *, key: str, total_steps: int, spec: JobSpec) -> QProgressDialog:
        controller = self._window._job_controllers.get(key)
        if controller is None:
            controller = JobController(self._window, spec)
            self._window._job_controllers[key] = controller
        dialog = controller.start(total_steps)
        return dialog

    def close_job_progress_dialog(self, key: str) -> None:
        controller = self._window._job_controllers.pop(key, None)
        if controller is None:
            return
        controller.close()

    def update_progress_dialog(
        self,
        dialog: QProgressDialog,
        *,
        current: int,
        total: int,
        message: str,
        default_label: str,
    ) -> None:
        upper = max(1, int(total))
        dialog.setRange(0, upper)
        dialog.setValue(min(max(int(current), 0), upper))
        dialog.setLabelText(message or default_label)

    def show_resize_progress_dialog(self, total_steps: int) -> QProgressDialog:
        dialog = self.show_job_progress_dialog(
            key="resize",
            total_steps=total_steps,
            spec=JobSpec(
                title="Resize Images",
                preparing_label="Preparing resize...",
                running_label="Saving resized images...",
                indeterminate_label="Refreshing library...",
                window_modality=Qt.WindowModality.NonModal,
                stays_on_top=True,
            ),
        )
        self._window._resize_progress_dialog = dialog
        return dialog

    def show_convert_progress_dialog(self, total_steps: int) -> QProgressDialog:
        dialog = self.show_job_progress_dialog(
            key="convert",
            total_steps=total_steps,
            spec=JobSpec(
                title="Convert Images",
                preparing_label="Preparing conversion...",
                running_label="Converting images...",
                indeterminate_label="Refreshing library...",
                window_modality=Qt.WindowModality.NonModal,
                stays_on_top=True,
            ),
        )
        self._convert_progress_dialog = dialog
        return dialog

    def close_resize_progress_dialog(self) -> None:
        self.close_job_progress_dialog("resize")
        self._window._resize_progress_dialog = None

    def close_convert_progress_dialog(self) -> None:
        self.close_job_progress_dialog("convert")
        self._convert_progress_dialog = None

    def show_workflow_progress_dialog(self, total_steps: int) -> QProgressDialog:
        dialog = self.show_job_progress_dialog(
            key="workflow",
            total_steps=total_steps,
            spec=JobSpec(
                title="Deliver / Handoff",
                preparing_label="Preparing workflow export...",
                running_label="Saving workflow outputs...",
                indeterminate_label="Refreshing library...",
                window_modality=Qt.WindowModality.NonModal,
                stays_on_top=True,
            ),
        )
        self._workflow_progress_dialog = dialog
        return dialog

    def close_workflow_progress_dialog(self) -> None:
        self.close_job_progress_dialog("workflow")
        self._workflow_progress_dialog = None

    def show_archive_progress_dialog(self, total_steps: int, *, title: str) -> QProgressDialog:
        key = f"archive:{title.casefold()}"
        if self._window._archive_job_key != key:
            self.close_job_progress_dialog(self._window._archive_job_key)
        self._window._archive_job_key = key
        dialog = self.show_job_progress_dialog(
            key=key,
            total_steps=total_steps,
            spec=JobSpec(
                title=title,
                preparing_label="Preparing archive...",
                running_label="Processing archive...",
                indeterminate_label="Refreshing library...",
                window_modality=Qt.WindowModality.NonModal,
                stays_on_top=True,
            ),
        )
        self._archive_progress_dialog = dialog
        return dialog

    def close_archive_progress_dialog(self) -> None:
        self.close_job_progress_dialog(self._window._archive_job_key)
        self._archive_progress_dialog = None

    def record_supports_resize(self, record: ImageRecord | None) -> bool:
        if record is None or record.is_folder:
            return False
        suffix = suffix_for_path(record.path)
        return suffix not in RAW_SUFFIXES and suffix not in FITS_SUFFIXES and suffix not in MODEL_SUFFIXES

    def record_supports_convert(self, record: ImageRecord | None) -> bool:
        if record is None or record.is_folder:
            return False
        suffix = suffix_for_path(record.path)
        return suffix not in RAW_SUFFIXES and suffix not in FITS_SUFFIXES and suffix not in MODEL_SUFFIXES

    def refresh_record_capability_cache(self, records: list[ImageRecord] | None = None) -> None:
        source_records = self._window._all_records if records is None else records
        self._window._records_have_resizable = any(self.record_supports_resize(record) for record in source_records)
        self._window._records_have_convertible = any(self.record_supports_convert(record) for record in source_records)

    def resize_source_for_index(self, index: int) -> ResizeSourceItem | None:
        record = self._window._record_at(index)
        if record is None or not self.record_supports_resize(record):
            return None

        displayed_path = self._window.grid.displayed_variant_path(index)
        candidates: list[str] = []
        if displayed_path and displayed_path in record.edited_paths:
            candidates.append(displayed_path)
        candidates.append(record.path)
        preview_source = self._window._preview_ctl.preview_source_path(record)
        if preview_source:
            candidates.append(preview_source)
        candidates.extend(record.companion_paths)
        candidates.extend(record.edited_paths)
        if displayed_path:
            candidates.append(displayed_path)

        source_path = next((path for path in candidates if path and os.path.exists(path)), "")
        if not source_path:
            source_path = next((path for path in candidates if path), "")
        if not source_path:
            return None

        return ResizeSourceItem(
            source_path=source_path,
            source_name=Path(source_path).name,
        )

    def convert_source_for_index(self, index: int) -> ConvertSourceItem | None:
        record = self._window._record_at(index)
        if record is None or not self.record_supports_convert(record):
            return None

        displayed_path = self._window.grid.displayed_variant_path(index)
        candidates: list[str] = []
        if displayed_path and displayed_path in record.edited_paths:
            candidates.append(displayed_path)
        candidates.append(record.path)
        preview_source = self._window._preview_ctl.preview_source_path(record)
        if preview_source:
            candidates.append(preview_source)
        candidates.extend(record.companion_paths)
        candidates.extend(record.edited_paths)
        if displayed_path:
            candidates.append(displayed_path)

        source_path = next((path for path in candidates if path and os.path.exists(path)), "")
        if not source_path:
            source_path = next((path for path in candidates if path), "")
        if not source_path:
            return None

        return ConvertSourceItem(
            source_path=source_path,
            source_name=Path(source_path).name,
        )

    def workflow_export_source_for_record(self, record: ImageRecord) -> ResizeSourceItem | None:
        preferred = record.preferred_edit_path or ""
        candidates: list[str] = []
        if preferred:
            candidates.append(preferred)
        preview_source = self._window._preview_ctl.preview_source_path(record)
        if preview_source:
            candidates.append(preview_source)
        candidates.append(record.path)
        candidates.extend(record.companion_paths)
        candidates.extend(record.edited_paths)

        source_path = next((path for path in candidates if path and os.path.exists(path)), "")
        if not source_path:
            source_path = next((path for path in candidates if path), "")
        if not source_path:
            return None
        return ResizeSourceItem(source_path=source_path, source_name=Path(source_path).name)

    def workflow_export_sources_for_records(self, records: list[ImageRecord]) -> list[ResizeSourceItem]:
        sources: list[ResizeSourceItem] = []
        seen: set[str] = set()
        for record in records:
            source = self.workflow_export_source_for_record(record)
            if source is None:
                continue
            key = normalized_path_key(source.source_path)
            if key in seen:
                continue
            seen.add(key)
            sources.append(source)
        return sources

    def workflow_destination_dir(self, recipe: WorkflowRecipe, destination_root: str | None = None) -> str:
        return workflow_destination_dir(recipe, destination_root or self._window._current_folder or "")

    def workflow_archive_path(self, recipe: WorkflowRecipe, destination_root: str | None = None) -> str:
        return workflow_archive_path(recipe, destination_root or self._window._current_folder or "")

    def workflow_record_folder_name(self, record: ImageRecord) -> str:
        return workflow_record_folder_name(record.name)

    def run_workflow_recipe(
        self,
        recipe: WorkflowRecipe,
        *,
        destination_root: str | None = None,
        records: list[ImageRecord] | None = None,
    ) -> None:
        selected_records = records if records is not None else self._window._selected_records_for_workflow()
        if not selected_records:
            self._window.statusBar().showMessage("Select one or more images before running an export recipe.")
            return

        destination_dir = self.workflow_destination_dir(recipe, destination_root)
        if recipe.uses_transform_export:
            if not destination_dir:
                self._window.statusBar().showMessage("Choose a destination folder for this handoff recipe.")
                return
            sources = self.workflow_export_sources_for_records(selected_records)
            if not sources:
                self._window.statusBar().showMessage("No exportable sources were available for the selected records.")
                return
            plan = build_workflow_export_plan(sources, recipe, destination_dir=destination_dir)
            if not plan.can_apply:
                if plan.general_error:
                    QMessageBox.warning(self._window, "Export Recipe", plan.general_error)
                else:
                    self._window.statusBar().showMessage("The export plan could not be built.")
                return
            self.start_workflow_export_task(plan)
            return

        if recipe.transfer_mode == RECIPE_TRANSFER_ARCHIVE:
            archive_path = self.workflow_archive_path(recipe, destination_root)
            source_paths = self.archive_source_paths_for_records(selected_records)
            if not archive_path or not source_paths:
                self._window.statusBar().showMessage("No bundle files were available to archive for this recipe.")
                return
            self.start_archive_create_task(
                source_paths,
                archive_path,
                archive_key=recipe.archive_format,
                root_dir=self._window._current_folder or None,
            )
            return

        if not destination_dir:
            self._window.statusBar().showMessage("Choose a destination folder for this export recipe.")
            return

        destructive = recipe.transfer_mode == RECIPE_TRANSFER_MOVE
        if destructive:
            confirmation = QMessageBox.question(
                self._window,
                "Run Export Recipe?",
                f"This recipe moves the selected bundles into:\n\n{destination_dir}\n\nContinue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirmation != QMessageBox.StandardButton.Yes:
                return

        processed = 0
        for record in list(selected_records):
            target_dir = destination_dir
            if recipe.group_by_record_folder:
                target_dir = normalize_filesystem_path(str(Path(destination_dir) / self.workflow_record_folder_name(record)))
            if recipe.transfer_mode == RECIPE_TRANSFER_MOVE:
                if self._window._record_ops.move_record_to_path(record.path, target_dir):
                    processed += 1
            else:
                if self._window._record_ops.copy_record_to_path(record.path, target_dir):
                    processed += 1
        if processed:
            self._window._navigation.remember_recent_destination(destination_dir)
        action_label = "Moved" if recipe.transfer_mode == RECIPE_TRANSFER_MOVE else "Copied"
        self._window.statusBar().showMessage(f"{action_label} {processed} bundle(s) with recipe: {recipe.name}")

    def resize_record_prompt(self, index: int) -> bool:
        if self._window._is_recycle_folder():
            return False
        source = self.resize_source_for_index(index)
        if source is None:
            self._window.statusBar().showMessage("Resize can't be used on RAW files.")
            return False
        return self.open_resize_dialog(
            [source],
            title="Resize Image",
            scope_label=f"Selected image: {source.source_name}",
            show_preview=False,
            raw_note="Resize can't be used on RAW files.",
        )

    def convert_record_prompt(self, index: int) -> bool:
        if self._window._is_recycle_folder():
            return False
        source = self.convert_source_for_index(index)
        if source is None:
            self._window.statusBar().showMessage("Convert can't be used on RAW files.")
            return False
        return self.open_convert_dialog(
            [source],
            title="Convert Image",
            scope_label=f"Selected image: {source.source_name}",
            show_preview=False,
            raw_note="Convert can't be used on RAW files.",
        )

    def archive_source_paths_for_records(self, records: list[ImageRecord]) -> tuple[str, ...]:
        ordered: list[str] = []
        seen: set[str] = set()
        for record in records:
            for path in self._window._record_paths(record):
                key = normalized_path_key(path)
                if key in seen or not os.path.exists(path):
                    continue
                seen.add(key)
                ordered.append(path)
        return tuple(ordered)

    def default_archive_base_name(self, records: list[ImageRecord]) -> str:
        if len(records) == 1:
            return Path(records[0].name).stem or "archive"
        folder_name = Path(self._window._current_folder).name if self._window._current_folder else "selection"
        return f"{folder_name} selection".strip()

    def archive_output_path_for_records(self, records: list[ImageRecord], archive_key: str) -> str:
        archive_format = archive_format_for_key(archive_key)
        initial_directory = self._window._current_folder or QDir.homePath()
        initial_path = str(Path(initial_directory) / f"{self.default_archive_base_name(records)}{archive_format.suffix}")
        chosen_path, _selected_filter = QFileDialog.getSaveFileName(
            self._window,
            f"Create {archive_format.label} Archive",
            initial_path,
            archive_format.save_filter,
        )
        if not chosen_path:
            return ""
        try:
            archive_path = ensure_archive_suffix(chosen_path, archive_format)
        except ValueError as exc:
            QMessageBox.warning(self._window, "Archive Path", str(exc))
            return ""
        if os.path.exists(archive_path):
            replace = QMessageBox.question(
                self._window,
                "Replace Archive?",
                f"{Path(archive_path).name} already exists.\n\nDo you want to replace it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if replace != QMessageBox.StandardButton.Yes:
                return ""
        return archive_path

    def create_archive_for_records(self, records: list[ImageRecord], archive_key: str) -> None:
        if not records:
            return
        archive_path = self.archive_output_path_for_records(records, archive_key)
        if not archive_path:
            return
        source_paths = self.archive_source_paths_for_records(records)
        if not source_paths:
            self._window.statusBar().showMessage("No files were available to archive.")
            return
        self.start_archive_create_task(
            source_paths,
            archive_path,
            archive_key=archive_key,
            root_dir=self._window._current_folder or None,
        )

    def extract_archive_prompt(self) -> None:
        initial_directory = self._window._current_folder or QDir.homePath()
        archive_path, _selected_filter = QFileDialog.getOpenFileName(
            self._window,
            "Extract Archive",
            initial_directory,
            EXTRACT_ARCHIVE_FILTER,
        )
        if not archive_path:
            return
        default_destination = self._window._current_folder or str(Path(archive_path).parent)
        destination_dir = QFileDialog.getExistingDirectory(self._window, "Extract Archive To", default_destination)
        if not destination_dir:
            return
        self.start_archive_extract_task(archive_path, destination_dir)

    def extract_archive_into_folder_prompt(self, destination_dir: str) -> None:
        if not destination_dir:
            return
        archive_path, _selected_filter = QFileDialog.getOpenFileName(
            self._window,
            "Extract Archive Here",
            destination_dir,
            EXTRACT_ARCHIVE_FILTER,
        )
        if not archive_path:
            return
        self.start_archive_extract_task(archive_path, destination_dir)

    def start_archive_create_task(
        self,
        source_paths: tuple[str, ...],
        archive_path: str,
        *,
        archive_key: str,
        root_dir: str | None,
        refresh_folder: str = "",
        archive_label: str = "",
    ) -> None:
        if self._active_archive_task is not None:
            self._window.statusBar().showMessage("An archive task is already running.")
            return
        archive_format = archive_format_for_key(archive_key)
        task = CreateArchiveTask(source_paths, archive_path, archive_key=archive_key, root_dir=root_dir)
        task.signals.started.connect(self.handle_archive_started, Qt.ConnectionType.QueuedConnection)
        task.signals.progress.connect(self.handle_archive_progress, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self.handle_archive_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self.handle_archive_failed, Qt.ConnectionType.QueuedConnection)
        self._active_archive_task = task
        self._archive_context = ArchiveExecutionContext(
            mode="create",
            archive_path=archive_path,
            destination_dir=str(Path(archive_path).parent),
            archive_label=archive_label or f"{archive_format.label} archive",
            refresh_folder=refresh_folder,
        )
        self._window._archive_pool.start(task)

    def start_archive_extract_task(self, archive_path: str, destination_dir: str) -> None:
        if self._active_archive_task is not None:
            self._window.statusBar().showMessage("An archive task is already running.")
            return
        normalized_destination = normalize_filesystem_path(destination_dir)
        if not normalized_destination:
            return
        task = ExtractArchiveTask(archive_path, normalized_destination)
        task.signals.started.connect(self.handle_archive_started, Qt.ConnectionType.QueuedConnection)
        task.signals.progress.connect(self.handle_archive_progress, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self.handle_archive_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self.handle_archive_failed, Qt.ConnectionType.QueuedConnection)
        self._active_archive_task = task
        self._archive_context = ArchiveExecutionContext(
            mode="extract",
            archive_path=archive_path,
            destination_dir=normalized_destination,
            refresh_folder=normalized_destination if normalized_path_key(normalized_destination) == normalized_path_key(self._window._current_folder) else "",
        )
        self._window._archive_pool.start(task)
