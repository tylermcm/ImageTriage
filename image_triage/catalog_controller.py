from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QDir, Qt
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox, QProgressDialog

from .job_controller import JobSpec
from .library_store import CatalogRefreshSummary, CatalogRefreshTask, VirtualCollection
from .models import ImageRecord
from .scanner import normalize_filesystem_path, normalized_path_key, scan_folder
from .ui import CatalogSearchDialog, CollectionEditDialog

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
        window._load_virtual_scope_records(records, scope_kind="catalog", scope_id=scope_id, scope_label=scope_label)

    def add_current_folder_to_catalog(self) -> None:
        window = self._window
        if not window._current_folder:
            window.statusBar().showMessage("Open a real folder before adding it to the library.")
            return
        window._library_store.add_catalog_root(window._current_folder)
        window._refresh_catalog_menu()
        self.start_catalog_refresh((window._current_folder,), label="Indexing current folder for the library...")

    def add_folder_to_catalog_prompt(self) -> None:
        window = self._window
        folder = QFileDialog.getExistingDirectory(window, "Add Folder To Library", window._current_folder or QDir.homePath())
        if not folder:
            return
        window._library_store.add_catalog_root(folder)
        window._refresh_catalog_menu()
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
            window._refresh_catalog_menu()
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
                action.triggered.connect(lambda _checked=False, target=root.path: window._browse_catalog(root_path_override=target))
        else:
            empty_action = window.catalog_menu.addAction("No library folders yet")
            empty_action.setEnabled(False)
        if window.actions is not None:
            window._update_action_states()

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
        window._update_progress_dialog(
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
        window._refresh_catalog_menu()
        if summary is None:
            window.statusBar().showMessage("Library refresh complete")
            return
        message = f"Library refreshed: {summary.record_count} image bundle(s) across {summary.folder_count} folder(s)"
        if summary.missing_roots:
            message = f"{message} | Missing roots: {len(summary.missing_roots)}"
        window.statusBar().showMessage(message)

    def _handle_failed(self, message: str) -> None:
        window = self._window
        window._active_catalog_task = None
        window._catalog_context = None
        self._close_progress_dialog()
        QMessageBox.warning(window, "Library Refresh Failed", f"Could not refresh the library.\n\n{message}")

    def _show_progress_dialog(self, total_steps: int) -> QProgressDialog:
        window = self._window
        dialog = window._show_job_progress_dialog(
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
        window._close_job_progress_dialog("catalog")
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
        if window._scope_kind != "folder" or not window._current_folder:
            window.statusBar().showMessage("Open a real folder before rebuilding its catalog cache.")
            return
        window.statusBar().showMessage(f"Rebuilding catalog cache for {window._current_folder}...")
        window._load_folder(window._current_folder, force_refresh=True, bypass_catalog_cache=True)

    # -- Virtual collections ---------------------------------------------

    def selected_record_paths_for_library(self) -> tuple[str, ...]:
        return tuple(record.path for record in self._window._selected_records_for_workflow())

    def choose_virtual_collection(self, *, title: str, prompt: str) -> VirtualCollection | None:
        window = self._window
        collections = window._library_store.list_collections()
        if not collections:
            window.statusBar().showMessage("Create a collection first.")
            return None
        labels: list[str] = []
        label_to_id: dict[str, str] = {}
        for collection in collections:
            label = f"{collection.name} ({collection.item_count})"
            if label in label_to_id:
                label = f"{label} [{collection.id}]"
            labels.append(label)
            label_to_id[label] = collection.id
        default_label = labels[0]
        if window._scope_kind == "collection" and window._scope_id:
            current_collection = window._library_store.load_collection(window._scope_id)
            if current_collection is not None:
                for label, collection_id in label_to_id.items():
                    if collection_id == current_collection.id:
                        default_label = label
                        break
        choice, accepted = QInputDialog.getItem(window, title, prompt, labels, labels.index(default_label), False)
        if not accepted or not choice:
            return None
        return window._library_store.load_collection(label_to_id[str(choice)])

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
        self.begin_collection_mode("create")

    def begin_collection_mode(self, mode: str, *, collection: VirtualCollection | None = None) -> None:
        window = self._window
        if window._collection_mode:
            return
        if window._zen_mode_enabled:
            window._set_zen_mode(False)
        if window._active_tool_mode or window.grid.tool_checkbox_mode():
            window._cancel_tool_mode(show_message=False)
        window.grid.clear_adapter_review_mode()
        window._collection_previous_view = window._browser_view_mode
        window._collection_previous_inspector_enabled = window.inspector_panel.isEnabled()
        window._collection_mode = mode
        window._collection_target_id = collection.id if collection is not None else ""
        window.grid.set_collection_checkbox_mode(True, paths=collection.item_paths if collection is not None else ())
        window.grid.clear_selection(keep_current=True)
        # An unbuilt popout picks collection mode up when it is built.
        preview = window._preview_if_built()
        if preview is not None:
            preview.set_collection_browse_mode(True)
        window.inspector_panel.setEnabled(False)
        window._set_browser_view_mode("grid")
        self.refresh_collection_mode_ui()
        window._update_action_states()
        window.statusBar().showMessage("Collection mode: check images across folders, then save or cancel.")

    def refresh_collection_mode_ui(self) -> None:
        window = self._window
        if not window._collection_mode:
            return
        count = len(window.grid.collection_paths())
        window.collection_mode_bar.show()
        window.collection_mode_count.setText(f"{count} image{'s' if count != 1 else ''} checked")
        if window._collection_mode == "create":
            window.collection_mode_title.setText("New Collection")
            window.collection_mode_save_button.setText("Save Collection")
            window.collection_mode_save_button.setEnabled(count > 0)
        else:
            collection = window._library_store.load_collection(window._collection_target_id)
            window.collection_mode_title.setText(f"Edit: {collection.name}" if collection else "Edit Collection")
            window.collection_mode_save_button.setText("Save Changes")
            window.collection_mode_save_button.setEnabled(collection is not None)

    def cancel_collection_mode(self, checked: bool = False, *, show_message: bool = True) -> None:
        del checked
        window = self._window
        if not window._collection_mode:
            return
        previous_view = window._collection_previous_view
        window._collection_mode = ""
        window._collection_target_id = ""
        window.grid.set_collection_checkbox_mode(False)
        preview = window._preview_if_built()
        if preview is not None:
            preview.set_collection_browse_mode(False)
        window.inspector_panel.setEnabled(window._collection_previous_inspector_enabled)
        window.collection_mode_bar.hide()
        window._set_browser_view_mode(previous_view)
        window._update_action_states()
        if show_message:
            window.statusBar().showMessage("Collection mode canceled; no collection changes were saved.")

    def save_collection_mode(self, checked: bool = False) -> None:
        del checked
        window = self._window
        if not window._collection_mode:
            return
        paths = window.grid.collection_paths()
        if window._collection_mode == "edit":
            collection = window._library_store.load_collection(window._collection_target_id)
            if collection is None:
                window.statusBar().showMessage("That collection is no longer available.")
                return
            saved = window._library_store.replace_collection_paths(collection.id, paths)
            if saved is None:
                window.statusBar().showMessage("The collection could not be saved. Your picks are still checked.")
                return
            self.cancel_collection_mode(show_message=False)
            if getattr(window, "_scope_kind", "") == "collection" and window._scope_id == saved.id:
                records, _missing = self.resolve_records_for_paths(saved.item_paths)
                window._load_virtual_scope_records(
                    records,
                    scope_kind="collection",
                    scope_id=saved.id,
                    scope_label=f"Collection: {saved.name}",
                )
            window._refresh_collections_menu()
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
        window._refresh_collections_menu()
        if collection is not None:
            self.cancel_collection_mode(show_message=False)
            window.statusBar().showMessage(f"Saved collection: {collection.name} ({collection.item_count} items)")

    def open_virtual_collection(self, collection_id: str) -> None:
        window = self._window
        collection = window._library_store.load_collection(collection_id)
        if collection is None:
            window._refresh_collections_menu()
            window.statusBar().showMessage("That collection is no longer available.")
            return
        records, missing = self.resolve_records_for_paths(collection.item_paths)
        if not records:
            window.statusBar().showMessage(f"{collection.name} has no available files to open.")
            return
        window._load_virtual_scope_records(
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
        if window._scope_kind == "collection" and window._scope_id:
            collection = window._library_store.load_collection(window._scope_id)
        if collection is None:
            collection = self.choose_virtual_collection(title="Remove From Collection", prompt="Collection")
        if collection is None:
            return
        updated = window._library_store.remove_paths_from_collection(collection.id, paths)
        window._refresh_collections_menu()
        if updated is None:
            return
        if window._scope_kind == "collection" and window._scope_id == updated.id:
            records, missing = self.resolve_records_for_paths(updated.item_paths)
            window._load_virtual_scope_records(
                records,
                scope_kind="collection",
                scope_id=updated.id,
                scope_label=f"Collection: {updated.name}",
            )
            if missing:
                window.statusBar().showMessage(f"Updated {updated.name} ({len(records)} available, {missing} missing)")
            return
        window.statusBar().showMessage(f"Removed selected items from {updated.name}")

    def delete_virtual_collection(self) -> None:
        window = self._window
        collection = None
        if window._scope_kind == "collection" and window._scope_id:
            collection = window._library_store.load_collection(window._scope_id)
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
        window._refresh_collections_menu()
        if deleted and window._scope_kind == "collection" and window._scope_id == collection.id:
            last_folder = window._settings.value(window.LAST_FOLDER_KEY, "", str)
            if last_folder and os.path.isdir(last_folder):
                window._select_folder(last_folder)
            else:
                window._current_folder = ""
                window._set_scope_state(kind="folder", scope_id="", label="")
                window._apply_loaded_records([])
        if deleted:
            window.statusBar().showMessage(f"Deleted collection: {collection.name}")
