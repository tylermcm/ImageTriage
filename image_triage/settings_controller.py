"""Saved state and settings: the settings dialog, persisted lists (favorites, recents, presets, recipes, folder view states), window and pane geometry, shortcut overrides and the normalisers for stored values. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""
from __future__ import annotations

import json

from PySide6.QtCore import QByteArray, QObject, QSignalBlocker, QTimer, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QApplication, QInputDialog, QWidget

from .ai_results import set_cull_thresholds
from .ai_runtime_packages import AI_RUNTIME_CPU_VARIANT, AI_RUNTIME_GPU_VARIANT
from .aiculler_workflow import default_aiculler_runtime
from .appearance_controller import window_opacity
from .filtering import SavedFilterPreset, deserialize_saved_filter_preset, serialize_saved_filter_preset
from .models import DeleteMode, SortMode, WinnerMode
from .perf import perf_logger, performance_log_dir
from .phash_prefilter import PHashPrefilterSettings, default_phash_prefilter_settings
from .records_view_controller import _memory_path_key
from .scanner import normalize_filesystem_path, scan_child_folders
from .settings_dialog import WorkflowPreset, WorkflowSettingsDialog
from .shell_actions import open_in_file_explorer
from .ui import SHORTCUT_REGISTRY, apply_shortcut_overrides, effective_shortcuts, load_shortcut_overrides, normalize_ui_gamma, save_shortcut_overrides, clear_window_layout, format_action_tooltip, parse_appearance_mode, restore_window_layout, save_window_layout
from .ui import layout_ratios
from .ui.display_metrics import STANDARD_DISPLAY, normalize_display_profile_preference
from .workflows import WorkflowRecipe, WorkspacePreset, built_in_workflow_recipes, built_in_workspace_presets, dump_saved_workflow_recipes, dump_saved_workspace_presets, load_saved_workflow_recipes, load_saved_workspace_presets, recipe_key_for_name

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .window import MainWindow


class SettingsController(QObject):
    """Saved state and settings: the settings dialog, persisted lists (favorites, recents, presets, recipes, folder view states), window and pane geometry, shortcut overrides and the normalisers for stored values. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.4)."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self._window = window

    def load_pane_width_ratios(self) -> None:
        """Make the widths the user last dragged to the live layout_ratios
        values. Runs before the shell is built, so nothing sizes from the
        defaults first and then jumps."""
        if not self._window._settings.value(self._window.PANE_RATIOS_RESET_KEY, False, bool):
            self._window._settings.setValue(self._window.PANE_RATIOS_RESET_KEY, True)
            self._window._settings.remove(self._window.PANE_RATIOS_KEY)
            return
        raw = self._window._settings.value(self._window.PANE_RATIOS_KEY, "", str)
        try:
            stored = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            stored = {}
        if not isinstance(stored, dict):
            return

        def usable(value) -> float | None:
            ok = isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
            return float(value) if ok else None

        # Out-of-range values are clamped, never discarded: discarding one put
        # the pane back to its default on every launch.
        layout_ratios.set_pane_widths(usable(stored.get("library")), usable(stored.get("inspector")))

    def pane_width_ratios(self) -> dict[str, float]:
        return {"library": layout_ratios.LIBRARY_PANEL_W, "inspector": layout_ratios.INSPECTOR_W}

    def remember_user_pane_widths(self, left: int, right: int) -> None:
        # The splitter reports moves when it is merely re-laid out too, so only
        # record a width the user is actually dragging.
        if QApplication.mouseButtons() == Qt.MouseButton.NoButton:
            return
        width = max(1, self._window.width())
        layout_ratios.set_pane_widths(
            left / width if left > 0 else None,
            right / width if right > 0 else None,
        )
        ratios = {key: round(value, 4) for key, value in self.pane_width_ratios().items()}
        self._window._settings.setValue(self._window.PANE_RATIOS_KEY, json.dumps(ratios))
        # The inspector's label column is a share of the pane, so it has to
        # follow a drag as well as a window resize.
        self._window._display.apply_inspector_text_ratios(width, max(1, self._window.height()))

    def refresh_action_shortcut_hint(self, action: QAction) -> None:
        base_text = action.property("imageTriageBaseText")
        if not isinstance(base_text, str) or not base_text:
            base_text = action.text().replace("&", "")
        shortcut_text = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
        hinted_text = format_action_tooltip(base_text, shortcut_text)
        action.setToolTip(hinted_text)
        action.setStatusTip(hinted_text)

    def apply_shortcut_overrides(self) -> None:
        """Push the single shortcut registry (ui/shortcuts.py) onto every
        surface that reads a keyboard shortcut: QActions, and the raw
        per-key review commands in grid/details/preview (WI-3.2)."""
        if self._window.actions is None:
            return
        overrides = load_shortcut_overrides(settings=self._window._settings)
        apply_shortcut_overrides(self._window.actions, overrides)
        for attr_name, _category, _default, _display in SHORTCUT_REGISTRY:
            action = getattr(self._window.actions, attr_name, None)
            if action is not None:
                self.refresh_action_shortcut_hint(action)

        surfaces = [self._window.grid, self._window.details_view.table]
        preview = self._window._preview_ctl.preview_if_built()
        if preview is not None:
            # An unbuilt viewer picks the same values up in _configure_new_preview.
            surfaces.append(preview)
        self.push_review_shortcuts(surfaces, overrides)

        self._window._command_palette.apply_shortcut(
            overrides.get(
                "open_command_palette",
                self._window.actions.open_command_palette.shortcut().toString(QKeySequence.SequenceFormat.PortableText),
            )
        )

    def _photocraft_camera_raw_first(self) -> bool:
        from .ui.native_image_layer import CAMERA_RAW_FIRST_KEY

        return bool(self._window._settings.value(CAMERA_RAW_FIRST_KEY, False, bool))

    def _photocraft_overlay_region(self) -> str:
        from .ui.native_image_layer import DEFAULT_REGION, SETTINGS_KEY, normalize_region

        return normalize_region(self._window._settings.value(SETTINGS_KEY, DEFAULT_REGION, str))

    def push_review_shortcuts(self, surfaces, overrides) -> None:
        review_keys = effective_shortcuts(self._window._REVIEW_KEY_BINDING_IDS, overrides)
        winner_shortcut = self._window.actions.accept_selection.shortcut()
        reject_shortcut = self._window.actions.reject_selection.shortcut()
        for surface in surfaces:
            surface.set_review_action_shortcuts(winner_shortcut, reject_shortcut)
            surface.set_review_key_shortcuts(review_keys)

    def load_saved_workflow_recipes(self) -> list[WorkflowRecipe]:
        raw = self._window._settings.value(self._window.WORKFLOW_RECIPES_KEY, "", str)
        return load_saved_workflow_recipes(raw)

    def save_saved_workflow_recipes(self) -> None:
        self._window._settings.setValue(
            self._window.WORKFLOW_RECIPES_KEY,
            dump_saved_workflow_recipes(self._window._saved_workflow_recipes),
        )

    def load_saved_workspace_presets(self) -> list[WorkspacePreset]:
        raw = self._window._settings.value(self._window.WORKSPACE_PRESETS_KEY, "", str)
        return load_saved_workspace_presets(raw)

    def save_saved_workspace_presets(self) -> None:
        self._window._settings.setValue(
            self._window.WORKSPACE_PRESETS_KEY,
            dump_saved_workspace_presets(self._window._saved_workspace_presets),
        )

    def refresh_workflow_recipe_menu(self) -> None:
        if not hasattr(self._window, "workflow_recipe_menu") or self._window.workflow_recipe_menu is None:
            return
        self._window.workflow_recipe_menu.clear()
        self._window.workflow_recipe_menu.setTitle("Run Recipe")
        self._window.workflow_recipe_menu.addAction(self._window.actions.share_to_phone)
        self._window.workflow_recipe_menu.addSeparator()
        self._window.workflow_recipe_menu.addAction(self._window.actions.handoff_builder)
        self._window.workflow_recipe_menu.addAction(self._window.actions.send_to_editor_pipeline)
        self._window.workflow_recipe_menu.addSeparator()

        builtins = built_in_workflow_recipes()
        if builtins:
            header = self._window.workflow_recipe_menu.addSection("Built-In Recipes")
            header.setEnabled(False)
            for recipe in builtins:
                action = self._window.workflow_recipe_menu.addAction(recipe.name)
                action.triggered.connect(lambda _checked=False, target=recipe: self._window._export_jobs.run_workflow_recipe(target))
            self._window.workflow_recipe_menu.addSeparator()

        saved_header = self._window.workflow_recipe_menu.addSection("Saved Recipes")
        saved_header.setEnabled(False)
        if self._window._saved_workflow_recipes:
            for recipe in self._window._saved_workflow_recipes:
                action = self._window.workflow_recipe_menu.addAction(recipe.name)
                action.triggered.connect(lambda _checked=False, target=recipe: self._window._export_jobs.run_workflow_recipe(target))
        else:
            empty_action = self._window.workflow_recipe_menu.addAction("No saved recipes yet")
            empty_action.setEnabled(False)

    def refresh_workspace_preset_menu(self) -> None:
        if not hasattr(self._window, "workspace_preset_menu") or self._window.workspace_preset_menu is None:
            return
        self._window.workspace_preset_menu.clear()
        self._window.workspace_preset_menu.setTitle("Workspace Presets")
        builtins = built_in_workspace_presets()
        if builtins:
            builtins_header = self._window.workspace_preset_menu.addSection("Built-In Presets")
            builtins_header.setEnabled(False)
            for preset in builtins:
                action = self._window.workspace_preset_menu.addAction(preset.name)
                action.setToolTip(preset.description)
                action.triggered.connect(lambda _checked=False, target=preset: self.apply_workspace_preset(target))
            self._window.workspace_preset_menu.addSeparator()
        saved_header = self._window.workspace_preset_menu.addSection("Saved Presets")
        saved_header.setEnabled(False)
        if self._window._saved_workspace_presets:
            for preset in self._window._saved_workspace_presets:
                action = self._window.workspace_preset_menu.addAction(preset.name)
                action.setToolTip(preset.description)
                action.triggered.connect(lambda _checked=False, target=preset: self.apply_workspace_preset(target))
        else:
            empty_action = self._window.workspace_preset_menu.addAction("No saved workspace presets yet")
            empty_action.setEnabled(False)

    def apply_default_workspace(self) -> None:
        if self._window.workspace_docks is None:
            return
        self._window.workspace_docks.reset_layout()

    def restore_window_state(self) -> None:
        restored, window_state = restore_window_layout(
            self._window,
            self._window._settings,
            self._window.GEOMETRY_KEY,
            self._window.STATE_KEY,
            self._window.workspace_docks,
        )
        # The layout is proportioned to a maximized window, so that is how it
        # opens (a saved fullscreen session still reopens fullscreen).
        self._window._startup_window_state = window_state if window_state == "fullscreen" else "maximized"
        if self._window._startup_window_state == "maximized":
            # Maximized before the first show, so the window appears at its
            # final size and the panes are laid out once, not at the restored
            # size first and again after a maximize.
            self._window.setWindowState(self._window.windowState() | Qt.WindowState.WindowMaximized)
        # TEMPORARY: translucent so the window can be laid over the design
        # reference while the ratios are tuned. Set WINDOW_OPACITY back to 1.0
        # (or run with IMAGE_TRIAGE_OPACITY=1) when that is done.
        self._window.setWindowOpacity(window_opacity())
        if not restored:
            self.apply_default_workspace()

    def save_window_state(self) -> None:
        self.save_details_view_state()
        save_window_layout(self._window, self._window._settings, self._window.GEOMETRY_KEY, self._window.STATE_KEY, self._window.workspace_docks)

    def restore_details_view_state(self) -> None:
        header_state = self._window._settings.value(self._window.DETAILS_HEADER_STATE_KEY, QByteArray())
        if isinstance(header_state, QByteArray):
            self._window.details_view.restore_header_state(header_state)
        try:
            sort_column = int(self._window._settings.value(self._window.DETAILS_SORT_COLUMN_KEY, 0, int))
        except (TypeError, ValueError):
            sort_column = 0
        sort_order_raw = str(self._window._settings.value(self._window.DETAILS_SORT_ORDER_KEY, "asc", str) or "asc")
        sort_order = Qt.SortOrder.DescendingOrder if sort_order_raw == "desc" else Qt.SortOrder.AscendingOrder
        self._window.details_view.set_sort_state(sort_column, sort_order)

    def save_details_view_state(self) -> None:
        if getattr(self._window, "details_view", None) is None:
            return
        self._window._settings.setValue(self._window.DETAILS_HEADER_STATE_KEY, self._window.details_view.save_header_state())
        sort_column, sort_order = self._window.details_view.sort_state()
        self._window._settings.setValue(self._window.DETAILS_SORT_COLUMN_KEY, sort_column)
        self._window._settings.setValue(
            self._window.DETAILS_SORT_ORDER_KEY,
            "desc" if sort_order == Qt.SortOrder.DescendingOrder else "asc",
        )

    def finish_startup_restore(self) -> None:
        if self._window._startup_launch_target and self._window._startup.open_launch_target(self._window._startup_launch_target, chunked_restore=True):
            self._window._startup_launch_target = ""
        else:
            self._window._startup_launch_target = ""
            if self._window._quick_view_mode:
                self._window._startup.show_main_window_after_quick_view_failure()
            self._window._load_start_folder()
            self._window._ai_run.restore_ai_results()
        if not self._window._quick_view_mode:
            QTimer.singleShot(0, self._window._ai_setup.maybe_prompt_for_ai_setup)

    def apply_base_score_blend_to_workflow(self) -> None:
        """Push the user's Base score weight slider into the aiculler_workflow
        module so the next bundle build / adapter export uses it."""

        from .aiculler_workflow import set_base_score_blend_weight
        set_base_score_blend_weight(self._window._ai_base_score_weight_percent_setting / 100.0)

    def apply_cull_thresholds_to_classifier(self) -> None:
        """Push the user's Keep/Review sliders into the ai_results module so
        the next bundle load uses them. Keep top X% becomes a >= (100 - X)
        keeper threshold; the review band sits just below that."""

        keep_top = float(self._window._ai_keep_top_percent_setting)
        review_band = float(self._window._ai_review_band_percent_setting)
        keeper_threshold = max(0.0, 100.0 - keep_top)
        reject_threshold = max(0.0, keeper_threshold - review_band)
        set_cull_thresholds(
            keeper_percentile=keeper_threshold,
            reject_percentile=reject_threshold,
        )

    def load_phash_prefilter_settings(self) -> PHashPrefilterSettings:
        defaults = default_phash_prefilter_settings()
        return PHashPrefilterSettings(
            enabled=self._window._settings.value(
                self._window.PHASH_PREFILTER_ENABLED_KEY,
                defaults.enabled,
                bool,
            ),
            hamming_threshold=max(
                0,
                min(
                    64,
                    int(
                        self._window._settings.value(
                            self._window.PHASH_PREFILTER_HAMMING_THRESHOLD_KEY,
                            defaults.hamming_threshold,
                            int,
                        )
                    ),
                ),
            ),
            cache_enabled=self._window._settings.value(
                self._window.PHASH_PREFILTER_CACHE_ENABLED_KEY,
                defaults.cache_enabled,
                bool,
            ),
            diagnostics_enabled=self._window._settings.value(
                self._window.PHASH_PREFILTER_DIAGNOSTICS_KEY,
                defaults.diagnostics_enabled,
                bool,
            ),
        ).normalized()

    def save_phash_prefilter_settings(self, settings: PHashPrefilterSettings) -> None:
        normalized = settings.normalized()
        self._window._settings.setValue(self._window.PHASH_PREFILTER_ENABLED_KEY, normalized.enabled)
        self._window._settings.setValue(self._window.PHASH_PREFILTER_HAMMING_THRESHOLD_KEY, normalized.hamming_threshold)
        self._window._settings.setValue(self._window.PHASH_PREFILTER_CACHE_ENABLED_KEY, normalized.cache_enabled)
        self._window._settings.setValue(self._window.PHASH_PREFILTER_DIAGNOSTICS_KEY, normalized.diagnostics_enabled)

    def default_ai_embed_batch_size(self) -> int:
        runtime_status = self._window._ai_setup.managed_ai_runtime_status()
        device = (self._window._ai_runtime.device or "auto").strip().lower()
        if device == "cpu":
            return self._window.AI_EMBED_BATCH_SIZE_CPU_AUTO
        if device.startswith("cuda"):
            return self._window.AI_EMBED_BATCH_SIZE_GPU_AUTO
        if (
            runtime_status.preferred_variant == AI_RUNTIME_CPU_VARIANT
            and AI_RUNTIME_GPU_VARIANT not in runtime_status.installed_variants
        ):
            return self._window.AI_EMBED_BATCH_SIZE_CPU_AUTO
        return self._window.AI_EMBED_BATCH_SIZE_GPU_AUTO

    def configured_ai_embed_batch_size(self) -> int:
        if self._window._ai_embed_batch_size_setting > 0:
            return self._window._ai_embed_batch_size_setting
        return self.default_ai_embed_batch_size()

    def ai_embed_batch_size_label(self) -> str:
        if self._window._ai_embed_batch_size_setting > 0:
            return str(self._window._ai_embed_batch_size_setting)
        return f"Auto ({self.configured_ai_embed_batch_size()})"

    def configured_aiculler_runtime(self, *, workers: int | None = None):
        return default_aiculler_runtime(
            workers=workers,
            device=self._window._ai_runtime.device,
        )

    def handle_performance_logging_toggled(self, checked: bool) -> None:
        self._window._performance_logging_enabled = bool(checked)
        self._window._settings.setValue(self._window.PERFORMANCE_LOGGING_KEY, self._window._performance_logging_enabled)
        perf_logger().set_enabled(self._window._performance_logging_enabled, reason="menu_toggle")
        if self._window._performance_logging_enabled:
            perf_logger().log("perf.menu_toggle_confirmed", log_dir=str(performance_log_dir()))
            perf_logger().flush()
            path = perf_logger().path
            self._window.statusBar().showMessage(f"Performance logging enabled: {path}")
        else:
            self._window.statusBar().showMessage("Performance logging disabled")
        self._window._inspector.update_action_states()

    def open_performance_log_folder(self) -> None:
        if self._window._performance_logging_enabled and not perf_logger().is_writing:
            perf_logger().set_enabled(True, reason="open_log_folder_resync")
        path = perf_logger().path
        target = path.parent if path is not None else performance_log_dir()
        target.mkdir(parents=True, exist_ok=True)
        open_in_file_explorer(str(target))

    def save_current_workspace_preset(self) -> None:
        if self._window.workspace_docks is None:
            return
        name, accepted = QInputDialog.getText(
            self._window,
            "Save Workspace Preset",
            "Preset name",
            text="Current Workspace",
        )
        if not accepted:
            return
        name = " ".join((name or "").split())
        if not name:
            return
        key = recipe_key_for_name(name) or "workspace_preset"
        preset = WorkspacePreset(
            key=key,
            name=name,
            description="Saved from the current workspace.",
            ui_mode="manual",
            columns=int(self._window.columns_combo.currentData() or 3),
            compare_enabled=self._window._compare_enabled,
            auto_advance=self._window._auto_advance_enabled,
            burst_groups=self._window._burst_groups_enabled,
            burst_stacks=self._window._burst_stacks_enabled,
            library_panel_mode=self._window.workspace_docks.library.mode,
            inspector_panel_mode=self._window.workspace_docks.inspector.mode,
            workspace_state=self._window.workspace_docks.save_state(),
        )
        existing_index = next((index for index, item in enumerate(self._window._saved_workspace_presets) if item.key == key), None)
        if existing_index is None:
            existing_index = next((index for index, item in enumerate(self._window._saved_workspace_presets) if item.name.casefold() == name.casefold()), None)
        if existing_index is not None:
            self._window._saved_workspace_presets[existing_index] = preset
        else:
            self._window._saved_workspace_presets.append(preset)
        self.save_saved_workspace_presets()
        self.refresh_workspace_preset_menu()
        self._window.statusBar().showMessage(f"Saved workspace preset: {preset.name}")

    def apply_workspace_preset(self, preset: WorkspacePreset) -> None:
        if self._window.workspace_docks is None:
            return
        if preset.workspace_state:
            self._window.workspace_docks.restore_state(preset.workspace_state)
        else:
            self._window.workspace_docks.reset_layout()
            if preset.library_panel_mode == "collapsed":
                self._window.workspace_docks.collapse_panel("library")
            elif preset.library_panel_mode == "hidden":
                self._window.workspace_docks.hide_panel("library")
            if preset.inspector_panel_mode == "collapsed":
                self._window.workspace_docks.collapse_panel("inspector")
            elif preset.inspector_panel_mode == "hidden":
                self._window.workspace_docks.hide_panel("inspector")
        self._window._toolbar.sync_chrome_to_manual_review()
        if self._window._compare_enabled != preset.compare_enabled:
            self._window._views.handle_compare_toggled(preset.compare_enabled)
        if self._window._auto_advance_enabled != preset.auto_advance:
            self._window._views.handle_auto_advance_toggled(preset.auto_advance)
        if self._window._burst_groups_enabled != preset.burst_groups:
            self._window._views.handle_burst_groups_toggled(preset.burst_groups)
        if self._window._burst_stacks_enabled != preset.burst_stacks:
            self._window._views.handle_burst_stacks_toggled(preset.burst_stacks)
        self._window._views.set_column_count(preset.columns)
        self._window.statusBar().showMessage(f"Applied workspace preset: {preset.name}")

    def load_winner_mode(self) -> WinnerMode:
        raw = self._window._settings.value(self._window.WINNER_MODE_KEY, WinnerMode.COPY.value, str)
        for mode in WinnerMode:
            if raw in {mode.name, mode.value}:
                return mode
        return WinnerMode.COPY

    def load_delete_mode(self) -> DeleteMode:
        raw = self._window._settings.value(self._window.DELETE_MODE_KEY, DeleteMode.SAFE_TRASH.value, str)
        for mode in DeleteMode:
            if raw in {mode.name, mode.value}:
                return mode
        return DeleteMode.SAFE_TRASH

    def load_workflow_presets(self) -> list[WorkflowPreset]:
        raw = self._window._settings.value(self._window.WORKFLOW_PRESETS_KEY, "", str)
        if not isinstance(raw, str) or not raw:
            return []
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return []
        if not isinstance(payload, list):
            return []
        presets: list[WorkflowPreset] = []
        seen: set[str] = set()
        for item in payload:
            if not isinstance(item, dict):
                continue
            name = " ".join(str(item.get("name") or item.get("session_id") or "").split())
            session_id = " ".join(str(item.get("session_id") or name).split())
            if not name or name.casefold() in seen:
                continue
            winner_raw = str(item.get("winner_mode") or WinnerMode.COPY.value)
            delete_raw = str(item.get("delete_mode") or DeleteMode.SAFE_TRASH.value)
            winner_mode = next((mode for mode in WinnerMode if winner_raw in {mode.name, mode.value}), WinnerMode.COPY)
            delete_mode = next((mode for mode in DeleteMode if delete_raw in {mode.name, mode.value}), DeleteMode.SAFE_TRASH)
            presets.append(
                WorkflowPreset(
                    name=name,
                    session_id=session_id or name,
                    winner_mode=winner_mode,
                    delete_mode=delete_mode,
                )
            )
            seen.add(name.casefold())
        return presets

    def save_workflow_presets(self) -> None:
        payload = [
            {
                "name": preset.name,
                "session_id": preset.session_id,
                "winner_mode": preset.winner_mode.value,
                "delete_mode": preset.delete_mode.value,
            }
            for preset in self._window._workflow_presets
        ]
        self._window._settings.setValue(self._window.WORKFLOW_PRESETS_KEY, json.dumps(payload))

    def load_fast_rating_hint_sessions(self) -> set[str]:
        raw = self._window._settings.value(self._window.FAST_RATING_HINT_SESSIONS_KEY, "", str)
        if not isinstance(raw, str) or not raw:
            return set()
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return set()
        if not isinstance(payload, list):
            return set()
        return {str(item) for item in payload if isinstance(item, str) and item}

    def load_favorites(self) -> list[str]:
        raw = self._window._settings.value(self._window.FAVORITES_KEY, [], list)
        if isinstance(raw, str):
            raw = [raw]
        favorites: list[str] = []
        for path in raw or []:
            # Only a folder provably gone from a local drive is dropped; a share that is asleep stays.
            if isinstance(path, str) and path and not self._window._dir_confirmed_missing(path) and path not in favorites:
                favorites.append(path)
        return favorites

    def load_recent_folders(self) -> list[str]:
        raw = self._window._settings.value(self._window.RECENT_FOLDERS_KEY, [], list)
        if isinstance(raw, str):
            raw = [raw]
        folders: list[str] = []
        seen: set[str] = set()
        for path in raw or []:
            if not isinstance(path, str) or not path or self._window._dir_confirmed_missing(path):
                continue
            # _memory_path_key, not normalized_path_key: that one resolves the path through the filesystem,
            # a network round trip per share path, and this is only for in-memory de-duplication.
            normalized = _memory_path_key(path)
            if normalized in seen:
                continue
            seen.add(normalized)
            folders.append(path)
        return folders[:12]

    def load_folder_view_states(self) -> dict[str, dict[str, object]]:
        raw = self._window._settings.value(self._window.FOLDER_VIEW_STATE_KEY, "", str)
        if not isinstance(raw, str) or not raw:
            return {}
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        if not isinstance(payload, dict):
            return {}
        states: dict[str, dict[str, object]] = {}
        for key, value in payload.items():
            if not isinstance(key, str) or not isinstance(value, dict):
                continue
            state: dict[str, object] = {}
            if "columns" in value:
                state["columns"] = self._window._normalize_column_count(value.get("columns"))
            sort_value = value.get("sort")
            if isinstance(sort_value, str) and any(sort_value in {mode.name, mode.value} for mode in SortMode):
                state["sort"] = sort_value
            scroll_value = value.get("scroll")
            try:
                state["scroll"] = max(0, int(scroll_value))
            except (TypeError, ValueError):
                pass
            if state:
                states[key] = state
        return states

    def load_recent_destinations(self) -> list[str]:
        raw = self._window._settings.value(self._window.RECENT_DESTINATIONS_KEY, [], list)
        if isinstance(raw, str):
            raw = [raw]
        destinations: list[str] = []
        seen: set[str] = set()
        for path in raw or []:
            if not isinstance(path, str) or not path or self._window._dir_confirmed_missing(path):
                continue
            normalized = _memory_path_key(path)  # pure; normalized_path_key would resolve a share path
            if normalized in seen:
                continue
            seen.add(normalized)
            destinations.append(path)
        return destinations[:10]

    def load_saved_filter_presets(self) -> list[SavedFilterPreset]:
        raw = self._window._settings.value(self._window.SAVED_FILTERS_KEY, "", str)
        if not isinstance(raw, str) or not raw:
            return []
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return []
        if not isinstance(payload, list):
            return []
        presets: list[SavedFilterPreset] = []
        seen_names: set[str] = set()
        for item in payload:
            preset = deserialize_saved_filter_preset(item if isinstance(item, dict) else None)
            if preset is None:
                continue
            normalized_name = preset.name.casefold()
            if normalized_name in seen_names:
                continue
            seen_names.add(normalized_name)
            presets.append(preset)
        return presets

    def load_recent_command_ids(self) -> list[str]:
        raw = self._window._settings.value(self._window.RECENT_COMMANDS_KEY, [], list)
        if isinstance(raw, str):
            raw = [raw]
        command_ids: list[str] = []
        for value in raw or []:
            if isinstance(value, str) and value and value not in command_ids:
                command_ids.append(value)
        return command_ids[:12]

    def save_favorites(self) -> None:
        self._window._settings.setValue(self._window.FAVORITES_KEY, self._window._favorites)

    def save_recent_folders(self) -> None:
        self._window._settings.setValue(self._window.RECENT_FOLDERS_KEY, self._window._recent_folders[:12])

    def save_folder_view_states(self) -> None:
        self._window._settings.setValue(self._window.FOLDER_VIEW_STATE_KEY, json.dumps(self._window._folder_view_states))

    def save_recent_destinations(self) -> None:
        self._window._settings.setValue(self._window.RECENT_DESTINATIONS_KEY, self._window._recent_destinations[:10])

    def save_saved_filter_presets(self) -> None:
        payload = [serialize_saved_filter_preset(preset) for preset in self._window._saved_filter_presets]
        self._window._settings.setValue(self._window.SAVED_FILTERS_KEY, json.dumps(payload))

    def save_recent_command_ids(self) -> None:
        self._window._settings.setValue(self._window.RECENT_COMMANDS_KEY, self._window._recent_command_ids[:12])

    def sort_mode_from_state(self, value: object) -> SortMode | None:
        if isinstance(value, SortMode):
            return value
        if not isinstance(value, str):
            return None
        for mode in SortMode:
            if value in {mode.name, mode.value}:
                return mode
        return None

    def current_folder_view_state(self) -> dict[str, object]:
        state: dict[str, object] = {
            "sort": self._window._sort_mode.value,
            "scroll": max(0, int(self._window.grid.current_scroll_value())),
            "current": self._window._pending_folder_focus_path or self._window._records_view.current_visible_record_path() or "",
        }
        return state

    def remember_current_folder_view_state(self) -> None:
        if not getattr(self._window, "_current_folder", ""):
            return
        key = _memory_path_key(self._window._current_folder)
        if not key:
            return
        self._window._folder_view_states[key] = self.current_folder_view_state()
        if len(self._window._folder_view_states) > 120:
            self._window._folder_view_states = dict(list(self._window._folder_view_states.items())[-120:])
        self.save_folder_view_states()

    def apply_folder_view_state(self, folder: str) -> None:
        state = self._window._folder_view_states.get(_memory_path_key(folder))
        self._window._pending_focus_scroll_top = False
        if not state:
            self._window._pending_folder_scroll_value = None
            return
        # Column count / zoom is program-wide, not per folder.
        sort_mode = self.sort_mode_from_state(state.get("sort"))
        if sort_mode is not None:
            self._window._sort_mode = sort_mode
            combo_index = self._window.sort_combo.findData(sort_mode)
            if combo_index >= 0:
                with QSignalBlocker(self._window.sort_combo):
                    self._window.sort_combo.setCurrentIndex(combo_index)
        if not self._window._restore_folder_position_enabled:
            self._window._pending_folder_scroll_value = None
            return
        saved_current = str(state.get("current") or "")
        if saved_current and not self._window._pending_folder_focus_path:
            self._window._pending_folder_focus_path = normalize_filesystem_path(saved_current)
            self._window._pending_focus_scroll_top = True
            self._window._pending_folder_scroll_value = None
            return
        try:
            self._window._pending_folder_scroll_value = max(0, int(state.get("scroll", 0)))
        except (TypeError, ValueError):
            self._window._pending_folder_scroll_value = None

    def adopt_menu_bar_shortcuts(self) -> None:
        pending = list(self._window.menuBar().actions())
        # Keyed by id() but holding the wrapper itself: QMenu.actions() returns temporary PySide
        # wrappers, and once one is dropped Python hands its address to another action's wrapper,
        # which a bare set of ids then skipped (random shortcuts were never adopted).
        seen: dict[int, object] = {}
        # The palette key already has the window's own QShortcut (CommandPaletteController, which
        # also follows remaps). The same key on an adopted action is ambiguous: Qt logs "Ambiguous
        # shortcut overload" and fires neither, so Ctrl+K would stop opening the palette.
        palette_action = getattr(getattr(self._window, "actions", None), "open_command_palette", None)
        while pending:
            action = pending.pop()
            if id(action) in seen:
                continue
            seen[id(action)] = action
            try:
                submenu = action.menu()
                if submenu is not None:
                    pending.extend(submenu.actions())
                elif (
                    not action.isSeparator()
                    and action.shortcuts()
                    and action is not palette_action
                    and action not in QWidget.actions(self._window)
                ):
                    self._window.addAction(action)
            except RuntimeError:
                # Rebuilt-on-open menus (recent folders, presets) can already
                # have dropped their entries; none of those carry shortcuts.
                continue

    def reset_window_layout(self) -> None:
        clear_window_layout(self._window._settings, self._window.GEOMETRY_KEY, self._window.STATE_KEY)
        self._window._settings.remove(self._window.WORKSPACE_BAR_STATE_KEY)
        self._window._settings.remove(self._window.WORKSPACE_BAR_POSITION_KEY)
        self._window._toolbar.set_workspace_bar_state("expanded")
        self._window._toolbar.set_workspace_bar_position("top")
        self._window.resize(1600, 960)
        self.apply_default_workspace()
        self._window.statusBar().showMessage("Reset window layout")

    def show_settings(self, initial_section: str | None = None) -> None:
        def persist_workflow_presets(presets: tuple[WorkflowPreset, ...]) -> None:
            self._window._workflow_presets = list(presets)
            self.save_workflow_presets()

        dialog = WorkflowSettingsDialog(
            sessions=self._window._decision_store.list_sessions(),
            current_session=self._window._session_id,
            winner_mode=self._window._winner_mode,
            delete_mode=self._window._delete_mode,
            loupe_card_style=self._window._effective_loupe_card_style,
            allowed_card_styles=self._window._display.allowed_card_styles(),
            ui_gamma=self._window._ui_gamma,
            interface_size=self._window._interface_size,
            free_smooth_scroll_enabled=self._window._free_smooth_scroll_enabled,
            preview_preload_batch_size=self._window._preview_preload_batch_size,
            photocraft_overlay_region=self._photocraft_overlay_region(),
            photocraft_camera_raw_first=self._photocraft_camera_raw_first(),
            show_hidden_folders=self._window._show_hidden_folders,
            single_drive_expansion_enabled=self._window._single_drive_expansion_enabled,
            auto_advance_enabled=self._window._auto_advance_enabled,
            burst_groups_enabled=self._window._burst_groups_enabled,
            burst_stacks_enabled=self._window._burst_stacks_enabled,
            catalog_cache_enabled=self._window._catalog_cache_enabled,
            watch_current_folder=self._window._watch_current_folder_enabled,
            restore_folder_position=self._window._restore_folder_position_enabled,
            check_updates_on_startup=self._window._check_updates_on_startup,
            theme=self._window._appearance_mode.value,
            performance_logging_enabled=self._window._performance_logging_enabled,
            show_ai_tags_in_grid=self._window._show_ai_tags_in_grid,
            apply_edits_to_pocketdrop=self._window._apply_edits_to_pocketdrop,
            ai_embed_batch_size=self._window._ai_embed_batch_size_setting,
            ai_review_detail_progress_enabled=self._window._ai_review_detail_progress_enabled,
            ai_dispute_weight=self._window._ai_dispute_weight_setting,
            ai_keep_top_percent=self._window._ai_keep_top_percent_setting,
            ai_review_band_percent=self._window._ai_review_band_percent_setting,
            ai_base_score_weight_percent=self._window._ai_base_score_weight_percent_setting,
            phash_prefilter_settings=self._window._phash_prefilter_settings,
            catalog_summary_text=self._window._scan.catalog_debug_summary(include_current=True),
            presets=self._window._workflow_presets,
            preset_save_callback=persist_workflow_presets,
            shortcut_overrides=load_shortcut_overrides(),
            initial_section=initial_section,
            display_profile=self._window._display_profile or STANDARD_DISPLAY,
            parent=self._window,
        )
        if self._window._exec_dialog_with_geometry(dialog, "settings_compact") != dialog.DialogCode.Accepted:
            return

        result = dialog.result_settings()
        self._window._workflow_presets = list(result.presets)
        self.save_workflow_presets()
        save_shortcut_overrides(dict(result.shortcut_overrides))
        self.apply_shortcut_overrides()
        new_session = self._window._decision_store.ensure_session(result.session_id)
        session_changed = new_session != self._window._session_id
        winner_changed = result.winner_mode != self._window._winner_mode
        delete_changed = result.delete_mode != self._window._delete_mode
        # Compare against the EFFECTIVE (possibly coerced) style so that opening
        # Settings on a restricted display and clicking OK without touching the
        # card style doesn't overwrite the saved preference (which is what lets
        # it come back on a larger display).
        card_style_changed = self._window._normalize_loupe_card_style(result.loupe_card_style) != self._window._effective_loupe_card_style
        new_ui_gamma = normalize_ui_gamma(result.ui_gamma)
        ui_gamma_changed = abs(new_ui_gamma - self._window._ui_gamma) > 1e-3
        new_interface_size = normalize_display_profile_preference(result.interface_size)
        interface_size_changed = new_interface_size != self._window._interface_size
        free_scroll_changed = result.free_smooth_scroll_enabled != self._window._free_smooth_scroll_enabled
        new_preview_preload_batch_size = self._window._normalize_preview_preload_batch_size(result.preview_preload_batch_size)
        preview_preload_changed = new_preview_preload_batch_size != self._window._preview_preload_batch_size
        hidden_changed = result.show_hidden_folders != self._window._show_hidden_folders
        single_drive_changed = (
            result.single_drive_expansion_enabled
            != self._window._single_drive_expansion_enabled
        )
        auto_advance_changed = result.auto_advance_enabled != self._window._auto_advance_enabled
        burst_groups_changed = result.burst_groups_enabled != self._window._burst_groups_enabled
        burst_stacks_changed = result.burst_stacks_enabled != self._window._burst_stacks_enabled
        catalog_changed = result.catalog_cache_enabled != self._window._catalog_cache_enabled
        watch_changed = result.watch_current_folder != self._window._watch_current_folder_enabled
        update_check_changed = result.check_updates_on_startup != self._window._check_updates_on_startup
        ai_batch_changed = result.ai_embed_batch_size != self._window._ai_embed_batch_size_setting
        ai_progress_detail_changed = result.ai_review_detail_progress_enabled != self._window._ai_review_detail_progress_enabled
        phash_prefilter_changed = result.phash_prefilter_settings.normalized() != self._window._phash_prefilter_settings

        self._window._folder_session.session_id = new_session
        self._window._winner_mode = result.winner_mode
        self._window._delete_mode = result.delete_mode
        # Only overwrite the saved preference when the user actively changed it,
        # so a coerced style on a small display never clobbers the real choice.
        if card_style_changed:
            self._window._loupe_card_style = self._window._normalize_loupe_card_style(result.loupe_card_style)
        self._window._ui_gamma = new_ui_gamma
        self._window._interface_size = new_interface_size
        self._window._free_smooth_scroll_enabled = result.free_smooth_scroll_enabled
        self._window._preview_preload_batch_size = new_preview_preload_batch_size
        self._window._show_hidden_folders = result.show_hidden_folders
        self._window._single_drive_expansion_enabled = result.single_drive_expansion_enabled
        self._window._auto_advance_enabled = result.auto_advance_enabled
        self._window._burst_groups_enabled = result.burst_groups_enabled
        self._window._burst_stacks_enabled = result.burst_stacks_enabled
        self._window._catalog_cache_enabled = result.catalog_cache_enabled
        self._window._watch_current_folder_enabled = result.watch_current_folder
        self._window._restore_folder_position_enabled = result.restore_folder_position
        self._window._check_updates_on_startup = result.check_updates_on_startup
        new_theme = parse_appearance_mode(result.theme)
        if new_theme != self._window._appearance_mode:
            self._window._appearance.set_appearance_mode(new_theme)
        if result.performance_logging_enabled != self._window._performance_logging_enabled:
            self.handle_performance_logging_toggled(result.performance_logging_enabled)
        if result.show_ai_tags_in_grid != self._window._show_ai_tags_in_grid:
            self._window._show_ai_tags_in_grid = result.show_ai_tags_in_grid
            self._window._settings.setValue(self._window.SHOW_AI_TAGS_IN_GRID_KEY, self._window._show_ai_tags_in_grid)
            self._window.grid.set_show_ai_annotations(self._window._show_ai_tags_in_grid)
        if result.apply_edits_to_pocketdrop != self._window._apply_edits_to_pocketdrop:
            self._window._apply_edits_to_pocketdrop = result.apply_edits_to_pocketdrop
            self._window._settings.setValue(self._window.APPLY_EDITS_TO_POCKETDROP_KEY, self._window._apply_edits_to_pocketdrop)
        self._window._ai_embed_batch_size_setting = self._window._normalize_ai_embed_batch_size(result.ai_embed_batch_size)
        self._window._ai_dispute_weight_setting = self._window._normalize_ai_dispute_weight(result.ai_dispute_weight)
        self._window._phash_prefilter_settings = result.phash_prefilter_settings.normalized()
        new_keep_top = self._window._normalize_ai_keep_top_percent(result.ai_keep_top_percent)
        new_review_band = self._window._normalize_ai_review_band_percent(result.ai_review_band_percent)
        cull_thresholds_changed = (
            new_keep_top != self._window._ai_keep_top_percent_setting
            or new_review_band != self._window._ai_review_band_percent_setting
        )
        self._window._ai_keep_top_percent_setting = new_keep_top
        self._window._ai_review_band_percent_setting = new_review_band
        if cull_thresholds_changed:
            self.apply_cull_thresholds_to_classifier()
        new_base_weight = self._window._normalize_ai_base_score_weight_percent(result.ai_base_score_weight_percent)
        if new_base_weight != self._window._ai_base_score_weight_percent_setting:
            self._window._ai_base_score_weight_percent_setting = new_base_weight
            self.apply_base_score_blend_to_workflow()
        self._window._ai_review_detail_progress_enabled = result.ai_review_detail_progress_enabled
        self._window._ai_setup.refresh_ai_runtime_preferences()
        self._window._settings.setValue(self._window.SESSION_KEY, self._window._session_id)
        self._window._settings.setValue(self._window.WINNER_MODE_KEY, self._window._winner_mode.value)
        self._window._settings.setValue(self._window.DELETE_MODE_KEY, self._window._delete_mode.value)
        self._window._settings.setValue(self._window.LOUPE_CARD_STYLE_KEY, self._window._loupe_card_style)
        self._window._settings.setValue(self._window.UI_GAMMA_KEY, self._window._ui_gamma)
        self._window._settings.setValue(self._window.INTERFACE_SIZE_KEY, self._window._interface_size)
        self._window._settings.setValue(self._window.FREE_SMOOTH_SCROLL_KEY, self._window._free_smooth_scroll_enabled)
        self._window._settings.setValue(self._window.PREVIEW_PRELOAD_BATCH_SIZE_KEY, self._window._preview_preload_batch_size)
        from .ui.native_image_layer import SETTINGS_KEY as overlay_key, normalize_region

        new_overlay_region = normalize_region(result.photocraft_overlay_region)
        self._window._settings.setValue(overlay_key, new_overlay_region)
        from .ui.native_image_layer import CAMERA_RAW_FIRST_KEY

        self._window._settings.setValue(CAMERA_RAW_FIRST_KEY, bool(result.photocraft_camera_raw_first))
        self._window._settings.setValue(self._window.SHOW_HIDDEN_FOLDERS_KEY, self._window._show_hidden_folders)
        self._window._settings.setValue(
            self._window.SINGLE_DRIVE_EXPANSION_KEY,
            self._window._single_drive_expansion_enabled,
        )
        self._window._settings.setValue(self._window.AUTO_ADVANCE_KEY, self._window._auto_advance_enabled)
        self._window._settings.setValue(self._window.BURST_GROUPS_KEY, self._window._burst_groups_enabled)
        self._window._settings.setValue(self._window.BURST_STACKS_KEY, self._window._burst_stacks_enabled)
        self._window._settings.setValue(self._window.CATALOG_CACHE_ENABLED_KEY, self._window._catalog_cache_enabled)
        self._window._settings.setValue(self._window.CATALOG_WATCH_CURRENT_FOLDER_KEY, self._window._watch_current_folder_enabled)
        self._window._settings.setValue(self._window.RESTORE_FOLDER_POSITION_KEY, self._window._restore_folder_position_enabled)
        self._window._settings.setValue(self._window.CHECK_UPDATES_ON_STARTUP_KEY, self._window._check_updates_on_startup)
        self._window._settings.setValue(self._window.AI_EMBED_BATCH_SIZE_KEY, self._window._ai_embed_batch_size_setting)
        self._window._settings.setValue(self._window.AI_DISPUTE_WEIGHT_KEY, self._window._ai_dispute_weight_setting)
        self._window._settings.setValue(self._window.AI_KEEP_TOP_PERCENT_KEY, self._window._ai_keep_top_percent_setting)
        self._window._settings.setValue(self._window.AI_REVIEW_BAND_PERCENT_KEY, self._window._ai_review_band_percent_setting)
        self._window._settings.setValue(self._window.AI_BASE_SCORE_WEIGHT_PERCENT_KEY, self._window._ai_base_score_weight_percent_setting)
        self.save_phash_prefilter_settings(self._window._phash_prefilter_settings)
        self._window._settings.setValue(self._window.AI_REVIEW_DETAIL_PROGRESS_KEY, self._window._ai_review_detail_progress_enabled)
        self._window._decision_store.touch_session(self._window._session_id)
        self._window.summary_session.setText(f"Profile: {self._window._session_id}")
        preview = self._window._preview_ctl.preview_if_built()
        if preview is not None:
            preview.set_auto_advance_enabled(self._window._auto_advance_enabled)
            preview.set_preload_batch_size(self._window._preview_preload_batch_size)
            preview.set_overlay_region(new_overlay_region)
        # Apply through the resolution policy so the effective (coerced) style
        # and column thresholds land on the grid.
        self._window._display.apply_display_style_policy(show_warning=False)
        self._window.grid.set_free_smooth_scroll_enabled(self._window._free_smooth_scroll_enabled)
        if ui_gamma_changed:
            self._window._appearance.apply_appearance()
        if interface_size_changed:
            self._window._display_profile = None
            self._window._appearance.apply_display_profile()
        self._window.folder_model.setFilter(self._window._navigation.folder_tree_filter())
        self._window.folder_tree.set_single_drive_expansion_enabled(
            self._window._single_drive_expansion_enabled
        )
        if hidden_changed and self._window._current_folder and self._window._scope_kind == "folder":
            current_path = self._window._records_view.current_visible_record_path()
            self._window._folder_records = scan_child_folders(self._window._current_folder, include_hidden=self._window._show_hidden_folders)
            self._window._navigation.refresh_directory_navigation_buttons()
            self._window._views.apply_records_view(current_path=current_path)
        if card_style_changed:
            self.remember_current_folder_view_state()
        if burst_groups_changed or burst_stacks_changed:
            self._window._annotation_ctl.refresh_burst_group_view()
        self._window._scan.refresh_current_folder_watch()
        self._window._scan.refresh_catalog_status_indicator()
        self._window._ai_run.update_ai_toolbar_state()

        if session_changed:
            self._window._undo_stack.clear()
            self._window._inspector.update_action_states()
            self._window._annotations = self._window._decision_store.load_annotations(self._window._session_id, self._window._all_records)
            self._window._views.apply_records_view()
            self._window._scan.start_scope_enrichment_task()

        if winner_changed:
            self._window.statusBar().showMessage(f"Winner handling set to {self._window._winner_mode.value}")
        elif delete_changed:
            self._window.statusBar().showMessage(f"Delete behavior set to {self._window._delete_mode.value}")
        elif session_changed:
            self._window.statusBar().showMessage(f"Switched to session: {self._window._session_id}")
        elif card_style_changed:
            label = {
                "detailed": "Detailed",
                "zen": "Zen",
                "gallery": "Gallery",
            }.get(self._window._loupe_card_style, self._window._loupe_card_style)
            self._window.statusBar().showMessage(f"Card style set to {label}")
        elif interface_size_changed:
            label = {
                "automatic": "Automatic",
                "compact": "Compact",
                "standard": "Comfortable",
                "spacious": "Large",
            }.get(self._window._interface_size, self._window._interface_size)
            self._window.statusBar().showMessage(f"Interface size set to {label}")
        elif free_scroll_changed:
            state = "enabled" if self._window._free_smooth_scroll_enabled else "disabled"
            self._window.statusBar().showMessage(f"Free smooth scrolling {state}")
        elif preview_preload_changed:
            if self._window._preview_preload_batch_size <= 0:
                self._window.statusBar().showMessage("Preview preloading disabled")
            else:
                self._window.statusBar().showMessage(f"Preview preload batch set to {self._window._preview_preload_batch_size} images")
        elif hidden_changed:
            state = "shown" if self._window._show_hidden_folders else "hidden"
            self._window.statusBar().showMessage(f"Hidden folders {state}")
        elif single_drive_changed:
            state = "enabled" if self._window._single_drive_expansion_enabled else "disabled"
            self._window.statusBar().showMessage(f"Single-branch expansion {state}")
        elif auto_advance_changed:
            state = "enabled" if self._window._auto_advance_enabled else "disabled"
            self._window.statusBar().showMessage(f"Auto-advance {state}")
        elif burst_groups_changed:
            state = "enabled" if self._window._burst_groups_enabled else "disabled"
            self._window.statusBar().showMessage(f"Smart groups {state}")
        elif burst_stacks_changed:
            state = "enabled" if self._window._burst_stacks_enabled else "disabled"
            self._window.statusBar().showMessage(f"Smart stacks {state}")
        elif catalog_changed:
            state = "enabled" if self._window._catalog_cache_enabled else "disabled"
            self._window.statusBar().showMessage(f"Catalog cache reads {state}")
        elif watch_changed:
            state = "enabled" if self._window._watch_current_folder_enabled else "disabled"
            self._window.statusBar().showMessage(f"Current-folder watch {state}")
        elif update_check_changed:
            state = "enabled" if self._window._check_updates_on_startup else "disabled"
            self._window.statusBar().showMessage(f"Startup update checks {state}")
        elif ai_batch_changed:
            self._window.statusBar().showMessage(f"AI embedding batch size set to {self.ai_embed_batch_size_label()}")
        elif ai_progress_detail_changed:
            state = "enabled" if self._window._ai_review_detail_progress_enabled else "disabled"
            self._window.statusBar().showMessage(f"Detailed AI Review progress {state}")
        elif phash_prefilter_changed:
            state = "enabled" if self._window._phash_prefilter_settings.enabled else "disabled"
            self._window.statusBar().showMessage(f"pHash Prefilter {state}")
