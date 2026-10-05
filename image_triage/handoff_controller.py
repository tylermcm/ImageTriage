"""Sending photos elsewhere and sorting them: the handoff and best-of-set builders, PocketDrop, the send-to-editor pipeline, the AI workflow center and semantic-folder sorting. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""
from __future__ import annotations

import os
import tempfile
import time

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMessageBox
from pathlib import Path

from .ai_workflow import ai_report_artifacts_ready, ai_semantic_artifacts_ready, build_ai_workflow_paths
from .ai_workflow_center import AIWorkflowCenterDialog
from .models import FilterMode, ImageRecord
from .semantic_sort import load_semantic_classifications, semantic_classification_for_record, semantic_folder_name
from .ui import BestOfSetDialog, HandoffBuilderDialog, default_theme
from .workflows import WorkflowRecipe, build_best_of_set_plan, built_in_workflow_recipes

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .window import MainWindow


def _pocketdrop_edited_export_dir() -> Path:
    """Scratch folder for edited-render temp files handed to PocketDrop.

    Sits next to PocketDrop's existing pasted-image scratch folder under the
    app's own AppData root (see pocketdrop.panel._pasted_images_dir), not a
    bare system temp dir, so it is easy to find and sweep.
    """

    from .scan_cache import app_data_root

    folder = app_data_root() / "PocketDrop" / "EditedExports"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _cleanup_pocketdrop_edited_exports() -> None:
    """Best-effort sweep of stale rendered-for-PocketDrop temp files.

    WI-5.3 (D3): PocketDrop's native send (``pd_add_paths``) is fire-and-forget
    -- the host gets no completion callback for an individual transfer (see
    ``pocketdrop/panel.py``), so a temp file written for an edited send cannot
    be deleted right after the call without risking deleting it before the
    transfer has actually read it. Rather than guess at a completion signal
    that does not exist, temp files live in their own scratch subfolder and
    are swept here for anything older than a day -- comfortably longer than
    any local transfer should take -- called opportunistically (startup and
    each PocketDrop send), never blocking on it.
    """

    try:
        folder = _pocketdrop_edited_export_dir()
        cutoff = time.time() - 24 * 60 * 60
        for path in folder.iterdir():
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue
    except OSError:
        pass


class HandoffController(QObject):
    """Sending photos elsewhere and sorting them: the handoff and best-of-set builders, PocketDrop, the send-to-editor pipeline, the AI workflow center and semantic-folder sorting. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self._window = window

    def apply_pocketdrop_background(self) -> None:
        """Paint the PocketDrop page in the same colour as the Library and
        Faces pages beside it."""
        panel = getattr(self._window, "pocketdrop_panel", None)
        if panel is None:
            return
        theme = getattr(self._window, "_theme", None) or default_theme()
        panel.set_background(theme.panel_bg.qcolor())

    def show_pocketdrop_page(self) -> None:
        docks = getattr(self._window, "workspace_docks", None)
        if docks is not None and docks.library.mode != "expanded":
            docks.expand_panel("library")
        self._window._appearance.show_left_nav_page("pocketdrop")

    def review_ai_disagreements(self) -> None:
        if self._window._ai_bundle is None:
            self._window.statusBar().showMessage("Load AI results first to review disagreement cases.")
            return
        self._window._toolbar.sync_chrome_to_manual_review()
        self._window._filter_query.quick_filter = FilterMode.AI_DISAGREEMENTS
        self._window._records_view.apply_filter_query_change()
        self._window.statusBar().showMessage("Showing AI disagreement cases for targeted review.")

    def open_handoff_builder(self, _checked: bool = False, *, initial_recipe: WorkflowRecipe | None = None) -> None:
        records = self._window._selected_records_for_workflow()
        if not records or not self._window._current_folder:
            self._window.statusBar().showMessage("Select one or more images before building a handoff workflow.")
            return
        dialog = HandoffBuilderDialog(
            built_in_recipes=built_in_workflow_recipes(),
            saved_recipes=tuple(self._window._saved_workflow_recipes),
            default_destination_root=self._window._current_folder,
            selection_count=len(records),
            initial_recipe=initial_recipe,
            parent=self._window,
        )
        if self._window._exec_dialog_with_geometry(dialog, "handoff_builder") != dialog.DialogCode.Accepted:
            return
        updated_recipes = list(dialog.saved_recipes())
        if updated_recipes != self._window._saved_workflow_recipes:
            self._window._saved_workflow_recipes = updated_recipes
            self._window._settings_ctl.save_saved_workflow_recipes()
            self._window._settings_ctl.refresh_workflow_recipe_menu()
        result = dialog.result_data()
        self._window._export_jobs.run_workflow_recipe(result.recipe, destination_root=result.destination_root, records=records)

    def pocketdrop_send_path_for(self, record: ImageRecord) -> str:
        """The path to hand PocketDrop for ``record``: the original file,
        unless the opt-in "apply edits" setting is on and this record has a
        real built-in editor session that renders successfully.

        Falls back to the original path whenever the setting is off, there is
        no sidecar, or the render fails -- sending to PocketDrop must never
        fail or silently drop a file just because a sidecar was unreadable.
        """

        if not self._window._apply_edits_to_pocketdrop:
            return record.path
        from .edit_storage import session_has_edits

        if not session_has_edits(record.path):
            return record.path
        try:
            from .edit_render_headless import render_edited_image

            rendered = render_edited_image(record.path)
            if rendered is None or rendered.isNull():
                return record.path
            folder = _pocketdrop_edited_export_dir()
            fd, temp_name = tempfile.mkstemp(prefix="pocketdrop_edit_", suffix=".jpg", dir=str(folder))
            os.close(fd)
            # JPEG: PocketDrop and the receiving end need a universally
            # supported format, and a RAW/PSD/etc. source can't be "rendered
            # with edits applied" back into its original format anyway.
            if not rendered.save(temp_name, "JPEG", quality=92):
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
                return record.path
            return temp_name
        except Exception:
            return record.path

    def send_selection_to_pocketdrop(self, _checked: bool = False) -> None:
        """Add the selected files to PocketDrop and bring its page forward."""
        records = [record for record in self._window._selected_records_for_workflow() if record.path]
        if not records:
            self._window.statusBar().showMessage("Select one or more files to send with PocketDrop.")
            return
        panel = getattr(self._window, "pocketdrop_panel", None)
        if panel is None or not panel.available:
            detail = panel.error if panel is not None else ""
            self._window.statusBar().showMessage(f"PocketDrop isn't available. {detail}".strip())
            return
        _cleanup_pocketdrop_edited_exports()
        paths = [self.pocketdrop_send_path_for(record) for record in records]
        self.show_pocketdrop_page()
        panel.add_paths(paths)
        noun = "file" if len(paths) == 1 else "files"
        self._window.statusBar().showMessage(f"Added {len(paths)} {noun} to PocketDrop")

    def open_send_to_editor_pipeline(self) -> None:
        recipe = next((item for item in built_in_workflow_recipes() if item.key == "send_to_editor"), None)
        self.open_handoff_builder(initial_recipe=recipe)

    def open_best_of_set_builder(self) -> None:
        if not self._window._records:
            self._window.statusBar().showMessage("Load a folder before assembling a best-of set.")
            return
        dialog = BestOfSetDialog(visible_count=len(self._window._records), parent=self._window)
        if self._window._exec_dialog_with_geometry(dialog, "best_of_set") != dialog.DialogCode.Accepted:
            return
        result = dialog.result_data()
        plan = build_best_of_set_plan(
            self._window._records,
            ai_bundle=self._window._ai_bundle,
            review_bundle=self._window._review_intelligence,
            burst_recommendations=self._window._burst_recommendations,
            annotations_by_path=self._window._annotations,
            limit=result.limit,
            strategy=result.strategy,
        )
        if not plan.candidates:
            self._window.statusBar().showMessage("No best-of candidates were available for the current view.")
            return
        selected_indexes = [
            index
            for candidate in plan.candidates
            for index in [self._window._record_index_by_path.get(candidate.path)]
            if index is not None
        ]
        if not selected_indexes:
            self._window.statusBar().showMessage("The proposed best-of picks are no longer visible in the current view.")
            return
        self._window.grid.set_selected_indexes(selected_indexes, current_index=selected_indexes[0])
        summary = plan.summary_lines[0] if plan.summary_lines else f"Selected {len(selected_indexes)} best-of pick(s)."
        self._window.statusBar().showMessage(summary)

    def open_ai_workflow_center(self) -> None:
        dialog = getattr(self, "_ai_workflow_center_dialog", None)
        if dialog is None:
            dialog = AIWorkflowCenterDialog(self._window)
            self._ai_workflow_center_dialog = dialog
        else:
            dialog.refresh()
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def open_people_search_dialog(self) -> None:
        self._window._records_view.open_people_search_dialog()

    def open_current_ai_review(self) -> None:
        if self._window._ai_bundle is None and not self._window._ai_run.load_hidden_ai_results_for_current_folder(show_message=True):
            self._window.statusBar().showMessage("Run Cull & Score first.")
            return
        self._window._toolbar.sync_chrome_to_manual_review()

    def sort_images_into_semantic_folders(self) -> None:
        if not self._window._current_folder:
            self._window.statusBar().showMessage("Open a source folder before sorting semantic classifications.")
            return
        if self._window._is_winners_folder() or self._window._is_recycle_folder():
            self._window.statusBar().showMessage("Semantic folder sorting runs from the source folder.")
            return
        if self._window._active_ai_task is not None:
            self._window.statusBar().showMessage("Wait for the current AI review run to finish before sorting.")
            return
        paths = build_ai_workflow_paths(self._window._current_folder)
        if not ai_semantic_artifacts_ready(paths):
            if ai_report_artifacts_ready(paths):
                rerun = QMessageBox.question(
                    self._window,
                    "Semantic Sort",
                    (
                        "Semantic classifications are missing or incomplete for this folder.\n\n"
                        "Run Cull & Score again now to generate the semantic classifications?"
                    ),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if rerun == QMessageBox.StandardButton.Yes:
                    self._window._ai_run.run_ai_pipeline()
                return
            self._window.statusBar().showMessage("Run Cull & Score before sorting into semantic folders.")
            return
        try:
            classifications = load_semantic_classifications(paths.semantic_export_path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self._window, "Semantic Folder Sort", f"Could not load semantic classifications.\n\n{exc}")
            return

        grouped: dict[str, list[ImageRecord]] = {}
        for record in self._window._all_records:
            if record.is_folder:
                continue
            classification = semantic_classification_for_record(record, classifications)
            if classification is None:
                continue
            folder_name = semantic_folder_name(classification.primary_label)
            grouped.setdefault(folder_name, []).append(record)

        total = sum(len(records) for records in grouped.values())
        if not total:
            self._window.statusBar().showMessage("No semantic classifications matched the current folder.")
            return

        destination_root = Path(self._window._current_folder) / "_semantic"
        confirmation = QMessageBox.question(
            self._window,
            "Sort Into Semantic Folders",
            (
                f"Move {total} image bundle(s) into {len(grouped)} semantic subfolder(s) under:\n"
                f"{destination_root}\n\n"
                "This uses the semantic classification CSV from the last AI Review and can be undone with Undo."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return

        moved = 0
        for folder_name, records in sorted(grouped.items(), key=lambda item: item[0].casefold()):
            destination_dir = str(destination_root / folder_name)
            os.makedirs(destination_dir, exist_ok=True)
            for record in records:
                if self._window._record_ops.move_record_to_path(record.path, destination_dir):
                    moved += 1
        if moved:
            self._window._navigation.remember_recent_destination(str(destination_root))
            self._window._recycle_bin.refresh_recycle_button()
            self._window.statusBar().showMessage(f"Moved {moved} image bundle(s) into semantic folders")
            return
        self._window.statusBar().showMessage("No images were moved into semantic folders.")
