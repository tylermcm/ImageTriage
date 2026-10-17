from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QDir, Qt
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox, QProgressDialog

from .job_controller import JobSpec
from .library_store import CatalogRefreshSummary, CatalogRefreshTask, VirtualCollection
from .models import ImageRecord
from .record_ops_controller import UndoAction
from .scanner import normalize_filesystem_path, normalized_path_key, scan_folder
from .ui import CatalogSearchDialog, CollectionEditDialog, CollectionPickerChoice, CollectionPickerDialog, effective_shortcuts
from .ui.flash_toast import FlashToast
from .folder_session import FolderSession

if TYPE_CHECKING:
    from .window import MainWindow


@dataclass(slots=True)
class CatalogExecutionContext:
    """Carries the currently running catalog refresh request through async handlers."""
    root_paths: tuple[str, ...] = ()
    label: str = ""


class CatalogController:
    """The global catalog (indexed roots, background refresh, search) and
    virtual collections (create/rename/delete, membership, collection-mode
    UI). Shared state that other MainWindow code also reads directly
    (`_collection_mode` is checked in ~15 places outside this group,
    `_active_catalog_task` gates the refresh-catalog action's enabled state)
    stays on `window` rather than becoming private to this controller."""

    @property
    def _session(self) -> FolderSession:
        return self._window._folder_session

    def __init__(self, window: "MainWindow") -> None:
        self._window = window

    # -- Global catalog roots ------------------------------------------

    def browse_catalog(self, _checked: bool = False, *, root_path_override: str = "") -> None:
        window = self._window
        roots = tuple(window._library_store.list_catalog_roots())
        if not roots:
            window.statusBar().showMessage("Add one or more folders to the library first.")
            return
        search_text = ""
        root_path = normalize_filesystem_path(root_path_override)
        if not root_path_override:
            dialog = CatalogSearchDialog(roots, parent=window)
            if window._exec_dialog_with_geometry(dialog, "catalog_search") != dialog.DialogCode.Accepted:
                return
            result = dialog.result_data()
            search_text = result.search_text
            root_path = normalize_filesystem_path(result.root_path)
        records = window._library_store.search_catalog(search_text=search_text, root_path=root_path)
        if not records:
            window.statusBar().showMessage("No library matches were found.")
            return
        if root_path:
            root_label = Path(root_path).name or root_path
            scope_label = f"Library: {root_label}"
        else:
            scope_label = "Library: All Indexed Folders"
        if search_text:
            scope_label = f'{scope_label} | Search "{search_text}"'
        scope_id = f"{normalized_path_key(root_path)}|{search_text.casefold()}"
        window._records_view.load_virtual_scope_records(records, scope_kind="catalog", scope_id=scope_id, scope_label=scope_label)

    def add_current_folder_to_catalog(self) -> None:
        window = self._window
        if not self._session.folder:
            window.statusBar().showMessage("Open a real folder before adding it to the library.")
            return
        window._library_store.add_catalog_root(self._session.folder)
        window._catalog.refresh_catalog_menu()
        self.start_catalog_refresh((self._session.folder,), label="Indexing current folder for the library...")

    def add_folder_to_catalog_prompt(self) -> None:
        window = self._window
        folder = QFileDialog.getExistingDirectory(window, "Add Folder To Library", self._session.folder or QDir.homePath())
        if not folder:
            return
        window._library_store.add_catalog_root(folder)
        window._catalog.refresh_catalog_menu()
        self.start_catalog_refresh((folder,), label=f"Indexing {Path(folder).name} for the library...")

    def remove_catalog_root_prompt(self) -> None:
        window = self._window
        roots = window._library_store.list_catalog_roots()
        if not roots:
            window.statusBar().showMessage("No library folders are configured.")
            return
        labels = [f"{Path(root.path).name or root.path} ({root.indexed_record_count})" for root in roots]
        label_to_path = {label: root.path for label, root in zip(labels, roots)}
        choice, accepted = QInputDialog.getItem(window, "Remove Library Folder", "Library folder", labels, 0, False)
        if not accepted or not choice:
            return
        root_path = label_to_path[str(choice)]
        confirmation = QMessageBox.question(
            window,
            "Remove Library Folder?",
            f"Remove {root_path} from the optional library index?\n\nThis does not move or delete files.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return
        if window._library_store.remove_catalog_root(root_path):
            window._catalog.refresh_catalog_menu()
            window.statusBar().showMessage(f"Removed from library: {Path(root_path).name or root_path}")

    def refresh_catalog_index(self) -> None:
        window = self._window
        roots = window._library_store.list_catalog_roots()
        if not roots:
            window.statusBar().showMessage("Add one or more folders to the library first.")
            return
        self.start_catalog_refresh(tuple(root.path for root in roots), label="Refreshing library index...")

    def refresh_catalog_menu(self) -> None:
        window = self._window
        if not hasattr(window, "catalog_menu") or window.catalog_menu is None:
            return
        window.catalog_menu.clear()
        window.catalog_menu.setTitle("Library")
        window.catalog_menu.addAction(window.actions.browse_catalog)
        window.catalog_menu.addAction(window.actions.add_current_folder_to_catalog)
        window.catalog_menu.addAction(window.actions.add_folder_to_catalog)
        window.catalog_menu.addAction(window.actions.remove_catalog_folder)
        window.catalog_menu.addAction(window.actions.refresh_catalog)
        window.catalog_menu.addAction(window.actions.rebuild_folder_catalog_cache)
        window.catalog_menu.addSeparator()

        roots = window._library_store.list_catalog_roots()
        if roots:
            header = window.catalog_menu.addSection("Indexed Roots")
            header.setEnabled(False)
            for root in roots:
                label = Path(root.path).name or root.path
                action = window.catalog_menu.addAction(f"{label} ({root.indexed_record_count})")
                tooltip_parts = [root.path]
                if root.last_indexed_at:
                    tooltip_parts.append(f"Indexed: {root.last_indexed_at}")
                if root.last_error:
                    tooltip_parts.append(f"Status: {root.last_error}")
                action.setToolTip("\n".join(tooltip_parts))
                action.triggered.connect(lambda _checked=False, target=root.path: window._catalog.browse_catalog(root_path_override=target))
        else:
            empty_action = window.catalog_menu.addAction("No library folders yet")
            empty_action.setEnabled(False)
        if window.actions is not None:
            window._inspector.update_action_states()

    # -- Catalog refresh task lifecycle ---------------------------------

    def start_catalog_refresh(self, root_paths: tuple[str, ...] | list[str], *, label: str) -> bool:
        window = self._window
        roots = tuple(normalize_filesystem_path(path) for path in root_paths if normalize_filesystem_path(path))
        if not roots:
            return False
        if window._active_catalog_task is not None:
            QMessageBox.information(window, "Library Refresh Running", "A library refresh is already in progress.")
            return False
        dialog = self._show_progress_dialog(max(1, len(roots)))
        dialog.setLabelText(label)
        task = CatalogRefreshTask(roots)
        task.signals.started.connect(self._handle_started, Qt.ConnectionType.QueuedConnection)
        task.signals.progress.connect(self._handle_progress, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self._handle_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self._handle_failed, Qt.ConnectionType.QueuedConnection)
        window._active_catalog_task = task
        window._catalog_context = CatalogExecutionContext(root_paths=roots, label=label)
        window._catalog_pool.start(task)
        window.statusBar().showMessage(label)
        return True

    def _handle_started(self, total_roots: int) -> None:
        window = self._window
        dialog = self._show_progress_dialog(total_roots)
        context = window._catalog_context
        dialog.setLabelText(context.label if context is not None else "Refreshing library index...")

    def _handle_progress(self, current: int, total: int, message: str) -> None:
        window = self._window
        dialog = self._show_progress_dialog(total)
        window._export_jobs.update_progress_dialog(
            dialog,
            current=current,
            total=total,
            message=message,
            default_label="Refreshing library index...",
        )

    def _handle_finished(self, result: object) -> None:
        window = self._window
        summary = result if isinstance(result, CatalogRefreshSummary) else None
        window._active_catalog_task = None
        window._catalog_context = None
        self._close_progress_dialog()
        window._catalog.refresh_catalog_menu()
        if summary is None:
            window.statusBar().showMessage("Library refresh complete")
            return
        message = f"Library refreshed: {summary.record_count} image bundle(s) across {summary.folder_count} folder(s)"
        if summary.missing_roots:
            message = f"{message} | Missing roots: {len(summary.missing_roots)}"
        if summary.unreachable_roots:
            message = f"{message} | Not reachable right now (index kept): {len(summary.unreachable_roots)}"
        window.statusBar().showMessage(message)

    def _handle_failed(self, message: str) -> None:
        window = self._window
        window._active_catalog_task = None
        window._catalog_context = None
        self._close_progress_dialog()
        QMessageBox.warning(window, "Library Refresh Failed", f"Could not refresh the library.\n\n{message}")

    def _show_progress_dialog(self, total_steps: int) -> QProgressDialog:
        window = self._window
        dialog = window._export_jobs.show_job_progress_dialog(
            key="catalog",
            total_steps=total_steps,
            spec=JobSpec(
                title="Library",
                preparing_label="Refreshing library index...",
                running_label="Refreshing library index...",
                window_modality=Qt.WindowModality.NonModal,
                stays_on_top=True,
            ),
        )
        window._catalog_progress_dialog = dialog
        return dialog

    def _close_progress_dialog(self) -> None:
        window = self._window
        window._export_jobs.close_job_progress_dialog("catalog")
        window._catalog_progress_dialog = None

    # -- Folder-records cache glue (also used by move/copy/delete/undo) -

    def load_cached_folder_records(self, folder: str) -> tuple[list[ImageRecord] | None, str]:
        window = self._window
        normalized_folder = normalize_filesystem_path(folder)
        records = window._catalog_repository.load_folder_records(normalized_folder)
        if records is not None:
            return records, "catalog"
        return None, ""

    def persist_folder_record_cache(self, folder: str, records: list[ImageRecord], *, source: str = "window") -> None:
        window = self._window
        normalized_folder = normalize_filesystem_path(folder)
        if not normalized_folder:
            return
        window._catalog_repository.save_folder_records(normalized_folder, records, source=source)

    def rebuild_current_folder_catalog_cache(self) -> None:
        window = self._window
        if self._session.scope_kind != "folder" or not self._session.folder:
            window.statusBar().showMessage("Open a real folder before rebuilding its catalog cache.")
            return
        window.statusBar().showMessage(f"Rebuilding catalog cache for {self._session.folder}...")
        window._scan.load_folder(self._session.folder, force_refresh=True, bypass_catalog_cache=True)

    # -- Virtual collections ---------------------------------------------

    def selected_record_paths_for_library(self) -> tuple[str, ...]:
        return tuple(record.path for record in self._window._selected_records_for_workflow())

    RECENT_COLLECTIONS_KEY = "collections/recent_ids"
    _RECENT_LIMIT = 6

    def recent_collection_ids(self) -> tuple[str, ...]:
        raw = str(self._window._settings.value(self.RECENT_COLLECTIONS_KEY, "", str) or "")
        return tuple(part for part in raw.split("\n") if part)

    def remember_recent_collection(self, collection_id: str) -> None:
        if not collection_id:
            return
        recent = [collection_id, *(other for other in self.recent_collection_ids() if other != collection_id)]
        self._window._settings.setValue(self.RECENT_COLLECTIONS_KEY, "\n".join(recent[: self._RECENT_LIMIT]))

    def choose_virtual_collection(self, *, title: str, prompt: str, allow_new: bool = False) -> VirtualCollection | None:
        """Ask for a collection in a type-to-filter picker: recently used first, then alphabetical.
        The default is the collection on screen, else the active one. With ``allow_new`` the list
        ends in "New collection", named from the search text, so adding never dead-ends."""
        del prompt
        window = self._window
        collections = window._library_store.list_collections()
        if not collections and not allow_new:
            window.statusBar().showMessage("Create a collection first.")
            return None
        by_id = {collection.id: collection for collection in collections}
        recent = tuple(collection_id for collection_id in self.recent_collection_ids() if collection_id in by_id)
        ordered = [by_id[collection_id] for collection_id in recent]
        ordered += [collection for collection in collections if collection.id not in recent]
        default_id = ""
        if self._session.scope_kind == "collection" and self._session.scope_id in by_id:
            default_id = self._session.scope_id
        if not default_id:
            default_id = self.active_collection_id()
        choice = self.run_collection_picker(
            [(collection.id, collection.name, collection.item_count) for collection in ordered],
            title=title,
            default_id=default_id,
            recent_ids=recent,
            allow_new=allow_new,
        )
        if choice is None:
            return None
        if choice.collection_id:
            return window._library_store.load_collection(choice.collection_id)
        return self.prompt_new_collection(title=title, name=choice.new_name)

    def run_collection_picker(self, entries, *, title: str, default_id: str, recent_ids, allow_new: bool) -> CollectionPickerChoice | None:
        dialog = CollectionPickerDialog(
            entries, title=title, default_id=default_id, recent_ids=tuple(recent_ids), allow_new=allow_new, parent=self._window
        )
        if self._window._exec_dialog_with_geometry(dialog, "collection_picker") != dialog.DialogCode.Accepted:
            return None
        return dialog.choice()

    def prompt_new_collection(self, *, title: str = "New Collection", name: str = "") -> VirtualCollection | None:
        """Name-only creation for the add flows; an existing name is reused rather than replaced.
        A name typed into the picker's search box is used as-is."""
        window = self._window
        name = " ".join(str(name or "").split())
        if not name:
            name, accepted = QInputDialog.getText(window, title, "New collection name")
            name = " ".join(str(name or "").split())
            if not accepted or not name:
                return None
        existing = window._library_store.find_collection_by_name(name)
        if existing is not None:
            return existing
        try:
            collection = window._library_store.create_collection(name=name)
        except (ValueError, RuntimeError) as exc:
            window.statusBar().showMessage(str(exc))
            return None
        window._projects.refresh_collections_menu()
        return collection

    def resolve_records_for_paths(self, paths: tuple[str, ...] | list[str]) -> tuple[list[ImageRecord], int]:
        window = self._window
        ordered_paths: list[str] = []
        seen: set[str] = set()
        for path in paths:
            normalized = normalize_filesystem_path(path)
            key = normalized_path_key(normalized)
            if not normalized or key in seen:
                continue
            seen.add(key)
            ordered_paths.append(normalized)

        if not ordered_paths:
            return [], 0

        catalog_records = window._library_store.load_catalog_records_for_paths(ordered_paths)
        folder_record_maps: dict[str, dict[str, ImageRecord]] = {}
        for path in ordered_paths:
            folder = normalize_filesystem_path(str(Path(path).parent))
            folder_key = normalized_path_key(folder)
            if folder_key in folder_record_maps:
                continue
            records, _source = self.load_cached_folder_records(folder)
            if records is None:
                try:
                    records = scan_folder(folder)
                except Exception:
                    records = []
                else:
                    self.persist_folder_record_cache(folder, records, source="collection-resolve")
            folder_record_maps[folder_key] = {
                normalized_path_key(record.path): record
                for record in records
            }

        resolved: list[ImageRecord] = []
        missing = 0
        added: set[str] = set()
        for path in ordered_paths:
            key = normalized_path_key(path)
            folder_key = normalized_path_key(str(Path(path).parent))
            record = folder_record_maps.get(folder_key, {}).get(key) or catalog_records.get(key)
            if record is None or not os.path.exists(record.path):
                missing += 1
                continue
            record_key = normalized_path_key(record.path)
            if record_key in added:
                continue
            added.add(record_key)
            resolved.append(record)
        return resolved, missing

    def create_virtual_collection_from_selection(self) -> None:
        # Start with the selected images already checked, so "select, then create" works.
        window = self._window
        selected = [window._records[index] for index in window.grid.selected_indexes() if 0 <= index < len(window._records)]
        seed = tuple(record.path for record in selected if not record.is_folder)
        self.begin_collection_mode("create", seed_paths=seed)
        skipped = len(selected) - len(seed)
        if self._session.collection_mode and skipped:
            window.statusBar().showMessage(
                f"Collection mode: {len(seed)} selected image(s) checked; {skipped} folder(s) skipped (collections hold images)."
            )

    def begin_collection_mode(
        self,
        mode: str,
        *,
        collection: VirtualCollection | None = None,
        seed_paths: tuple[str, ...] = (),
    ) -> None:
        window = self._window
        if self._session.collection_mode:
            return
        if window._zen_mode_enabled:
            window._zen.set_zen_mode(False)
        if window._active_tool_mode or window.grid.tool_checkbox_mode():
            window._tool_mode.cancel_tool_mode(show_message=False)
        window.grid.clear_adapter_review_mode()
        window._collection_previous_view = self._session.browser_view_mode
        window._collection_previous_inspector_enabled = window.inspector_panel.isEnabled()
        self._session.collection_mode = mode
        window._collection_target_id = collection.id if collection is not None else ""
        window.grid.set_collection_checkbox_mode(
            True,
            paths=(collection.item_paths if collection is not None else ()) + tuple(seed_paths),
        )
        window.grid.clear_selection(keep_current=True)
        # An unbuilt popout picks collection mode up when it is built.
        preview = window._preview_ctl.preview_if_built()
        if preview is not None:
            preview.set_collection_browse_mode(True)
        window.inspector_panel.setEnabled(False)
        window._views.set_browser_view_mode("grid")
        self.refresh_collection_mode_ui()
        window._inspector.update_action_states()
        window.statusBar().showMessage("Collection mode: check images across folders, then save or cancel.")

    def refresh_collection_mode_ui(self) -> None:
        window = self._window
        if not self._session.collection_mode:
            return
        count = len(window.grid.collection_paths())
        window.collection_mode_bar.show()
        window.collection_mode_count.setText(f"{count} image{'s' if count != 1 else ''} checked")
        if self._session.collection_mode == "create":
            window.collection_mode_title.setText("New Collection")
            window.collection_mode_save_button.setText("Save Collection")
            window.collection_mode_save_button.setEnabled(count > 0)
        else:
            collection = window._library_store.load_collection(window._collection_target_id)
            window.collection_mode_title.setText(f"Edit: {collection.name}" if collection else "Edit Collection")
            window.collection_mode_save_button.setText("Save Changes")
            window.collection_mode_save_button.setEnabled(collection is not None)

    def check_visible_in_collection_mode(self, checked: bool = False) -> None:
        del checked
        self._set_visible_collection_checks(True)

    def uncheck_visible_in_collection_mode(self, checked: bool = False) -> None:
        del checked
        self._set_visible_collection_checks(False)

    def _set_visible_collection_checks(self, checked: bool) -> None:
        window = self._window
        if not self._session.collection_mode:
            return
        paths = [record.path for record in window._records if not record.is_folder]
        window.grid.set_collection_paths_checked(paths, checked)
        self.refresh_collection_mode_ui()

    def cancel_collection_mode(self, checked: bool = False, *, show_message: bool = True) -> None:
        del checked
        window = self._window
        if not self._session.collection_mode:
            return
        previous_view = window._collection_previous_view
        self._session.collection_mode = ""
        window._collection_target_id = ""
        window.grid.set_collection_checkbox_mode(False)
        preview = window._preview_ctl.preview_if_built()
        if preview is not None:
            preview.set_collection_browse_mode(False)
        window.inspector_panel.setEnabled(window._collection_previous_inspector_enabled)
        window.collection_mode_bar.hide()
        window._views.set_browser_view_mode(previous_view)
        window._inspector.update_action_states()
        if show_message:
            window.statusBar().showMessage("Collection mode canceled; no collection changes were saved.")

    def save_collection_mode(self, checked: bool = False) -> None:
        del checked
        window = self._window
        if not self._session.collection_mode:
            return
        paths = window.grid.collection_paths()
        if self._session.collection_mode == "edit":
            collection = window._library_store.load_collection(window._collection_target_id)
            if collection is None:
                window.statusBar().showMessage("That collection is no longer available.")
                return
            saved = window._library_store.replace_collection_paths(collection.id, paths)
            if saved is None:
                window.statusBar().showMessage("The collection could not be saved. Your picks are still checked.")
                return
            self.cancel_collection_mode(show_message=False)
            if getattr(window, "_scope_kind", "") == "collection" and self._session.scope_id == saved.id:
                records, _missing = self.resolve_records_for_paths(saved.item_paths)
                window._records_view.load_virtual_scope_records(
                    records,
                    scope_kind="collection",
                    scope_id=saved.id,
                    scope_label=f"Collection: {saved.name}",
                )
            window._projects.refresh_collections_menu()
            window.statusBar().showMessage(f"Saved collection: {saved.name} ({saved.item_count} items)")
            return
        if not paths:
            return
        dialog = CollectionEditDialog(selection_count=len(paths), parent=window)
        if window._exec_dialog_with_geometry(dialog, "collection_edit") != dialog.DialogCode.Accepted:
            return
        result = dialog.result_data()
        existing = window._library_store.find_collection_by_name(result.name)
        if existing is not None:
            overwrite = QMessageBox.question(
                window,
                "Replace Collection?",
                f"{existing.name} already exists.\n\nReplace its items with the current selection?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if overwrite != QMessageBox.StandardButton.Yes:
                return
            window._library_store.update_collection(
                VirtualCollection(
                    id=existing.id,
                    name=result.name,
                    description=result.description,
                    kind=result.kind,
                    item_paths=existing.item_paths,
                    item_count=existing.item_count,
                    created_at=existing.created_at,
                    updated_at=existing.updated_at,
                )
            )
            collection = window._library_store.replace_collection_paths(existing.id, paths)
        else:
            collection = window._library_store.create_collection(
                name=result.name,
                description=result.description,
                kind=result.kind,
                item_paths=paths,
            )
        window._projects.refresh_collections_menu()
        if collection is not None:
            self.cancel_collection_mode(show_message=False)
            window.statusBar().showMessage(f"Saved collection: {collection.name} ({collection.item_count} items)")

    def open_virtual_collection(self, collection_id: str) -> None:
        window = self._window
        collection = window._library_store.load_collection(collection_id)
        if collection is None:
            window._projects.refresh_collections_menu()
            window.statusBar().showMessage("That collection is no longer available.")
            return
        records, missing = self.resolve_records_for_paths(collection.item_paths)
        if not records:
            window.statusBar().showMessage(f"{collection.name} has no available files to open.")
            return
        window._records_view.load_virtual_scope_records(
            records,
            scope_kind="collection",
            scope_id=collection.id,
            scope_label=f"Collection: {collection.name}",
        )
        if missing:
            window.statusBar().showMessage(f"Loaded collection {collection.name} ({len(records)} available, {missing} missing)")

    def add_selection_to_virtual_collection(self) -> None:
        collection = self.choose_virtual_collection(title="Edit Collection Items", prompt="Collection")
        if collection is None:
            return
        self.begin_collection_mode("edit", collection=collection)

    def remove_selection_from_virtual_collection(self) -> None:
        window = self._window
        paths = self.selected_record_paths_for_library()
        if not paths:
            window.statusBar().showMessage("Select one or more images before removing them from a collection.")
            return
        collection = None
        if self._session.scope_kind == "collection" and self._session.scope_id:
            collection = window._library_store.load_collection(self._session.scope_id)
        if collection is None:
            collection = self.choose_virtual_collection(title="Remove From Collection", prompt="Collection")
        if collection is None:
            return
        # remove_paths_with_undo reloads the collection view when it is on screen.
        updated, removed = self.remove_paths_with_undo(collection.id, paths)
        if updated is None:
            return
        if not removed:
            self.show_collection_feedback(f"None of the selected images are in {updated.name}", updated.id)
            return
        self.show_collection_feedback(f"Removed {removed} image(s) from {updated.name}", updated.id)

    # -- Fast adds: selection, visible results, the active collection ----
    #
    # These write membership rows only (one batched insert) and never touch
    # thumbnails or decode anything, so adding thousands of images is instant.
    # Each add or removal is one undo step, and every change is confirmed with a
    # toast over the grid or popout (the status bar alone gets overwritten).

    ACTIVE_COLLECTION_KEY = "collections/active_id"
    AUTO_ADD_WINNERS_KEY = "collections/auto_add_winners"

    def active_collection_id(self) -> str:
        return str(self._window._settings.value(self.ACTIVE_COLLECTION_KEY, "", str) or "")

    def active_collection(self) -> VirtualCollection | None:
        collection_id = self.active_collection_id()
        if not collection_id:
            return None
        return self._window._library_store.load_collection_header(collection_id)

    def set_active_collection(self, collection_id: str, *, show_message: bool = True) -> None:
        window = self._window
        collection = window._library_store.load_collection_header(collection_id) if collection_id else None
        window._settings.setValue(self.ACTIVE_COLLECTION_KEY, collection.id if collection is not None else "")
        if collection is not None:
            self.remember_recent_collection(collection.id)
        window._projects.refresh_collections_menu()
        if show_message:
            if collection is None:
                self.show_collection_feedback("No active collection")
            else:
                key = effective_shortcuts(("collect_at_cursor",)).get("collect_at_cursor", "")
                self.show_collection_feedback(
                    f"Active collection: {collection.name}" + (f" ({key} adds or removes the current image)" if key else ""),
                    collection.id,
                )

    def choose_active_collection(self, checked: bool = False) -> None:
        del checked
        collection = self.choose_virtual_collection(title="Set Active Collection", prompt="Collection", allow_new=True)
        if collection is not None:
            self.set_active_collection(collection.id)

    def clear_active_collection(self, checked: bool = False) -> None:
        del checked
        self.set_active_collection("")

    def auto_add_winners_enabled(self) -> bool:
        return bool(self._window._settings.value(self.AUTO_ADD_WINNERS_KEY, False, bool))

    def set_auto_add_winners(self, enabled: bool) -> None:
        window = self._window
        enabled = bool(enabled)
        window._settings.setValue(self.AUTO_ADD_WINNERS_KEY, enabled)
        if not enabled:
            self.show_collection_feedback("Winners no longer go to the active collection")
            return
        collection = self.active_collection()
        if collection is None:
            self.show_collection_feedback("Winners will go to the active collection once you set one")
        else:
            self.show_collection_feedback(f"New winners will also go to {collection.name}", collection.id)

    # -- Feedback -------------------------------------------------------

    def show_collection_feedback(self, message: str, collection_id: str = "") -> None:
        """Status bar, a toast over whatever you are looking at, and a flash of the sidebar row."""
        window = self._window
        window.statusBar().showMessage(message)
        toast = getattr(self, "_toast", None)
        if toast is None:
            toast = self._toast = FlashToast()
        anchor = None
        preview_ctl = getattr(window, "_preview_ctl", None)
        preview = preview_ctl.preview_if_built() if preview_ctl is not None else None
        if preview is not None and preview.isVisible():
            anchor = getattr(preview, "panes_widget", None) or preview
        if anchor is None:
            anchor = getattr(window, "browser_stack", None)
        toast.flash(anchor, message)
        flash_row = getattr(window._projects, "flash_collection_row", None)
        if collection_id and flash_row is not None:
            flash_row(collection_id)

    # -- Every collection a photo is in (right-click submenu, popout chip) ----

    _SUBMENU_LIMIT = 10

    def collection_ids_for_path(self, path: str) -> tuple[str, ...]:
        """Cached: the popout asks on every step, so this must not touch the database each time."""
        cache = getattr(self, "_membership_cache", None)
        if cache is None:
            cache = self._membership_cache = self._window._library_store.collection_ids_by_path()
        return cache.get(normalized_path_key(path), ())

    def invalidate_membership_cache(self) -> None:
        self._membership_cache = None

    def collection_chip_text(self, path: str) -> tuple[str, str]:
        """(button text, tooltip) for the popout's collections chip."""
        ids = self.collection_ids_for_path(path) if path else ()
        if not ids:
            return "+ Collection", "Not in any collection. Click to add it to one."
        names = {collection.id: collection.name for collection in self._window._library_store.list_collections()}
        listed = [names[collection_id] for collection_id in ids if collection_id in names]
        if not listed:
            return "+ Collection", "Not in any collection. Click to add it to one."
        text = f"● {listed[0]}" if len(listed) == 1 else f"● {len(listed)} collections"
        return text, "In: " + ", ".join(listed) + "\nClick to change"

    def populate_collections_menu(self, menu, paths) -> None:
        """Checkable rows, one per collection (recent and active first): checked when every path is in it,
        a dash when some are. Picking a row adds them all, or removes them when all were in already."""
        window = self._window
        paths = [path for path in paths if path]
        store = window._library_store
        collections = store.list_collections()
        by_id = {collection.id: collection for collection in collections}
        active_id = self.active_collection_id()
        leading = [collection_id for collection_id in (active_id, *self.recent_collection_ids()) if collection_id in by_id]
        ordered = [by_id[collection_id] for collection_id in dict.fromkeys(leading)]
        ordered += [collection for collection in collections if collection.id not in leading]
        keys = [normalized_path_key(path) for path in paths]
        busy = bool(self._session.collection_mode) or not paths
        for collection in ordered[: self._SUBMENU_LIMIT]:
            held = sum(1 for key in keys if collection.id in self._membership_for_key(key))
            label = collection.name + ("  (active)" if collection.id == active_id else "")
            if 0 < held < len(keys):
                label += f"  — {held} of {len(keys)} in"
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(bool(keys) and held == len(keys))
            action.setEnabled(not busy)
            action.triggered.connect(
                lambda _checked=False, target=collection.id, all_in=(bool(keys) and held == len(keys)): self._toggle_paths_in(target, paths, all_in)
            )
        if len(ordered) > self._SUBMENU_LIMIT:
            more = menu.addAction("More collections...")
            more.setEnabled(not busy)
            more.triggered.connect(lambda _checked=False: self._add_paths_via_picker(paths))
        if ordered:
            menu.addSeparator()
        new_action = menu.addAction("New collection...")
        new_action.setEnabled(not busy)
        new_action.triggered.connect(lambda _checked=False: self._add_paths_to_new_collection(paths))

    def _membership_for_key(self, key: str) -> tuple[str, ...]:
        cache = getattr(self, "_membership_cache", None)
        if cache is None:
            cache = self._membership_cache = self._window._library_store.collection_ids_by_path()
        return cache.get(key, ())

    def _toggle_paths_in(self, collection_id: str, paths, all_in: bool) -> None:
        if all_in:
            updated, removed = self.remove_paths_with_undo(collection_id, paths)
            if updated is not None and removed:
                noun = Path(paths[0]).name if len(paths) == 1 else f"{removed} images"
                self.show_collection_feedback(f"Removed {noun} from {updated.name}", updated.id)
            return
        updated, added = self.add_paths_with_undo(collection_id, paths)
        if updated is not None:
            if len(paths) == 1:
                self.show_collection_feedback(f"Added {Path(paths[0]).name} to {updated.name}", updated.id)
            else:
                self._report_add(updated, added, len(paths), source="image(s)")

    def _add_paths_via_picker(self, paths) -> None:
        collection = self.choose_virtual_collection(title="Add to Collection", prompt="Collection", allow_new=True)
        if collection is not None:
            self._toggle_paths_in(collection.id, paths, all_in=False)

    def _add_paths_to_new_collection(self, paths) -> None:
        collection = self.prompt_new_collection(title="New Collection")
        if collection is not None:
            self._toggle_paths_in(collection.id, paths, all_in=False)

    def refresh_collection_markers(self) -> None:
        """Mark the active collection's members on grid tiles and popout captions."""
        self.invalidate_membership_cache()
        window = self._window
        grid = getattr(window, "grid", None)
        if grid is None or not hasattr(grid, "set_active_collection_members"):
            return
        collection_id = self.active_collection_id()
        collection = window._library_store.load_collection(collection_id) if collection_id else None
        paths = collection.item_paths if collection is not None else ()
        label = collection.name if collection is not None else ""
        grid.set_active_collection_members(paths, label)
        preview_ctl = getattr(window, "_preview_ctl", None)
        preview = preview_ctl.preview_if_built() if preview_ctl is not None else None
        if preview is not None and hasattr(preview, "set_active_collection_members"):
            preview.set_active_collection_members(paths, label)
        if preview is not None and hasattr(preview, "refresh_collection_chip"):
            preview.refresh_collection_chip()

    # -- Membership changes (each one undoable) ----------------------------

    def add_paths_with_undo(self, collection_id: str, paths, *, batch_id: str = "") -> tuple[VirtualCollection | None, int]:
        """Add paths as one undo step. Returns the collection and how many were new."""
        window = self._window
        new_paths = window._library_store.paths_missing_from_collection(collection_id, list(paths))
        if not new_paths:
            return window._library_store.load_collection_header(collection_id), 0
        updated = window._library_store.add_paths_to_collection(collection_id, new_paths)
        if updated is None:
            return None, 0
        window._record_ops.push_undo(
            UndoAction(
                kind="collection_add",
                primary_path=new_paths[0],
                folder=updated.id,
                source_paths=tuple(new_paths),
                batch_id=batch_id,
            )
        )
        self.remember_recent_collection(updated.id)
        self.after_collection_membership_change(updated)
        return updated, len(new_paths)

    def remove_paths_with_undo(self, collection_id: str, paths, *, batch_id: str = "") -> tuple[VirtualCollection | None, int]:
        """Remove paths as one undo step that puts them back where they were."""
        window = self._window
        store = window._library_store
        positions = store.collection_positions(collection_id, list(paths))
        if not positions:
            return store.load_collection_header(collection_id), 0
        removed = tuple(path for path, _position in positions)
        updated = store.remove_paths_from_collection(collection_id, removed)
        if updated is None:
            return None, 0
        window._record_ops.push_undo(
            UndoAction(
                kind="collection_remove",
                primary_path=removed[0],
                folder=updated.id,
                source_paths=removed,
                positions=tuple(position for _path, position in positions),
                batch_id=batch_id,
            )
        )
        self.after_collection_membership_change(updated)
        return updated, len(removed)

    def after_collection_membership_change(self, collection: VirtualCollection) -> None:
        window = self._window
        window._projects.refresh_collections_menu()
        if self._session.scope_kind == "collection" and self._session.scope_id == collection.id:
            full = window._library_store.load_collection(collection.id) or collection
            records, _missing = self.resolve_records_for_paths(full.item_paths)
            window._records_view.load_virtual_scope_records(
                records,
                scope_kind="collection",
                scope_id=full.id,
                scope_label=f"Collection: {full.name}",
            )

    def undo_collection_change(self, action: UndoAction) -> None:
        store = self._window._library_store
        if action.kind == "collection_add":
            updated = store.remove_paths_from_collection(action.folder, action.source_paths)
        elif action.positions:
            updated = store.insert_paths_at(action.folder, action.source_paths, action.positions)
        else:
            updated = store.add_paths_to_collection(action.folder, action.source_paths)
        if updated is not None:
            self.after_collection_membership_change(updated)

    def collection_name_for_undo(self, collection_id: str) -> str:
        collection = self._window._library_store.load_collection_header(collection_id)
        return collection.name if collection is not None else "the collection"

    # -- Commands -------------------------------------------------------

    def _report_add(self, collection: VirtualCollection, added: int, requested: int, *, source: str) -> None:
        already = requested - added
        message = f"Added {added} {source} to {collection.name}"
        if already:
            message += f" ({already} already there)"
        self.show_collection_feedback(message, collection.id)

    def _collection_for_add(self, collection_id: str, *, title: str) -> VirtualCollection | None:
        if collection_id:
            return self._window._library_store.load_collection_header(collection_id)
        return self.choose_virtual_collection(title=title, prompt="Collection", allow_new=True)

    def add_selected_to_collection(self, checked: bool = False, *, collection_id: str = "") -> None:
        del checked
        window = self._window
        if self._session.collection_mode:
            return
        paths = [record.path for record in window._selected_records_for_workflow() if not record.is_folder]
        if not paths:
            window.statusBar().showMessage("Select one or more images first.")
            return
        collection = self._collection_for_add(collection_id, title="Add Selection to Collection")
        if collection is None:
            return
        updated, added = self.add_paths_with_undo(collection.id, paths)
        if updated is not None:
            self._report_add(updated, added, len(paths), source="selected image(s)")

    def add_visible_to_collection(self, checked: bool = False, *, collection_id: str = "") -> None:
        """Add every image the current filter, regex or semantic search shows."""
        del checked
        window = self._window
        if self._session.collection_mode:
            return
        paths = [record.path for record in window._records if not record.is_folder]
        if not paths:
            window.statusBar().showMessage("No images are visible to add.")
            return
        collection = self._collection_for_add(collection_id, title=f"Add All {len(paths)} Visible to Collection")
        if collection is None:
            return
        updated, added = self.add_paths_with_undo(collection.id, paths)
        if updated is not None:
            self._report_add(updated, added, len(paths), source="visible image(s)")

    def add_dropped_paths_to_collection(self, collection_id: str, paths) -> None:
        paths = [path for path in paths if path]
        if not paths:
            return
        updated, added = self.add_paths_with_undo(collection_id, paths)
        if updated is not None:
            self._report_add(updated, added, len(paths), source="dropped image(s)")

    def add_selected_to_active_collection(self, checked: bool = False) -> None:
        del checked
        window = self._window
        index = window.grid.current_index()
        if index < 0:
            window.statusBar().showMessage("Select one or more images first.")
            return
        self.add_index_to_active_collection(index, toggle=False)

    def add_index_to_active_collection(self, index: int, *, whole_selection: bool = True, toggle: bool = True) -> None:
        """The collect key (B): the image at the cursor, or the whole selection when the cursor is in it.
        Like W, it toggles - when every one of them is already in the collection, it takes them out.
        The popout passes ``whole_selection=False``: there B means the photo on screen."""
        window = self._window
        if self._session.collection_mode:
            return
        if whole_selection:
            records = window._selected_records_for_context(index)
        else:
            record = window._record_at(index)
            records = [record] if record is not None else []
        records = [record for record in records if not record.is_folder]
        if not records:
            return
        collection = self.active_collection()
        if collection is None:
            collection = self.choose_virtual_collection(title="Choose the Active Collection", prompt="Collection", allow_new=True)
            if collection is None:
                return
            self.set_active_collection(collection.id, show_message=False)
        paths = [record.path for record in records]
        name = records[0].name if len(records) == 1 else f"{len(records)} images"
        if toggle and not window._library_store.paths_missing_from_collection(collection.id, paths):
            updated, removed = self.remove_paths_with_undo(collection.id, paths)
            if updated is not None and removed:
                self.show_collection_feedback(f"Removed {name} from {updated.name}", updated.id)
            return
        updated, added = self.add_paths_with_undo(collection.id, paths)
        if updated is None:
            return
        if len(paths) == 1:
            message = f"Added {name} to {updated.name}" if added else f"{name} is already in {updated.name}"
            self.show_collection_feedback(message, updated.id)
            return
        self._report_add(updated, added, len(paths), source="image(s)")

    # -- Winners and the active collection ---------------------------------
    #
    # With the toggle on, a new winner is added to the active collection. Taking
    # the winner back (W again, or rejecting it) takes it out again - but only if
    # it got there by being a winner this session, never one you added yourself.

    def winner_auto_add_batch_id(self) -> str:
        """A batch id for a winner change that will also add to the active collection, else ''."""
        if not self.auto_add_winners_enabled() or not self.active_collection_id():
            return ""
        return uuid.uuid4().hex

    def auto_add_winner_paths(self, paths, *, batch_id: str) -> str:
        """Add new winners to the active collection. Returns the collection name when any were added."""
        if not batch_id:
            return ""
        collection_id = self.active_collection_id()
        if not collection_id:
            return ""
        new_paths = self._window._library_store.paths_missing_from_collection(collection_id, list(paths))
        updated, added = self.add_paths_with_undo(collection_id, new_paths, batch_id=batch_id)
        if updated is None or not added:
            return ""
        auto_added = self._auto_added_winner_keys()
        auto_added.update((collection_id, normalized_path_key(path)) for path in new_paths)
        return updated.name

    def _auto_added_winner_keys(self) -> set[tuple[str, str]]:
        keys = getattr(self, "_auto_added_winners", None)
        if keys is None:
            keys = self._auto_added_winners = set()
        return keys

    def winner_auto_remove_batch_id(self, paths) -> str:
        """A batch id when taking back these winners will also take auto-added ones out, else ''."""
        collection_id = self.active_collection_id()
        if not collection_id:
            return ""
        auto_added = self._auto_added_winner_keys()
        if any((collection_id, normalized_path_key(path)) in auto_added for path in paths):
            return uuid.uuid4().hex
        return ""

    def auto_remove_unwinner_paths(self, paths, *, batch_id: str) -> str:
        """Take back auto-added winners that are no longer winners. Returns the collection name if any left."""
        if not batch_id:
            return ""
        collection_id = self.active_collection_id()
        auto_added = self._auto_added_winner_keys()
        targets = [path for path in paths if (collection_id, normalized_path_key(path)) in auto_added]
        if not collection_id or not targets:
            return ""
        updated, removed = self.remove_paths_with_undo(collection_id, targets, batch_id=batch_id)
        for path in targets:
            auto_added.discard((collection_id, normalized_path_key(path)))
        return updated.name if updated is not None and removed else ""

    def delete_virtual_collection(self) -> None:
        window = self._window
        collection = None
        if self._session.scope_kind == "collection" and self._session.scope_id:
            collection = window._library_store.load_collection(self._session.scope_id)
        if collection is None:
            collection = self.choose_virtual_collection(title="Delete Collection", prompt="Collection")
        if collection is None:
            return
        confirmation = QMessageBox.question(
            window,
            "Delete Collection?",
            f"Delete the collection \"{collection.name}\"?\n\nThis does not delete any files.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirmation != QMessageBox.StandardButton.Yes:
            return
        deleted = window._library_store.delete_collection(collection.id)
        if deleted and self.active_collection_id() == collection.id:
            window._settings.setValue(self.ACTIVE_COLLECTION_KEY, "")
        window._projects.refresh_collections_menu()
        if deleted and self._session.scope_kind == "collection" and self._session.scope_id == collection.id:
            last_folder = window._settings.value(window.LAST_FOLDER_KEY, "", str)
            if last_folder and not window._dir_confirmed_missing(last_folder):
                window._navigation.select_folder(last_folder)
            else:
                self._session.folder = ""
                window._projects.set_scope_state(kind="folder", scope_id="", label="")
                window._records_view.apply_loaded_records([])
        if deleted:
            window.statusBar().showMessage(f"Deleted collection: {collection.name}")
