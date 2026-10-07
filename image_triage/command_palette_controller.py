from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtGui import QKeySequence, QShortcut

from .filtering import SavedFilterPreset, active_filter_labels, builtin_filter_presets
from .review_tools import FOCUS_ASSIST_COLORS, FOCUS_ASSIST_STRENGTHS
from .scanner import normalized_path_key
from .ui import CommandPaletteDialog, PaletteCommand, appearance_mode_label
from .workflows import built_in_workflow_recipes, built_in_workspace_presets
from .folder_session import FolderSession

if TYPE_CHECKING:
    from .ui.actions import MainWindowActions
    from .window import MainWindow

# ``add_action_command(command_id, action, *, section, title=None, subtitle="", keywords=())``
_AddActionCommand = Callable[..., None]


class CommandPaletteController:
    """Builds and drives the Ctrl+K command palette. Holds no state of its
    own beyond the window back-reference; the palette's open/visible state,
    shortcuts, cached per-context dialogs and recent-command list all live
    on MainWindow, the same way they did before this extraction."""

    @property
    def _session(self) -> FolderSession:
        return self._window._folder_session

    def __init__(self, window: "MainWindow") -> None:
        self._window = window

    def open(self, _checked: bool = False, *, context: str | None = None) -> None:
        window = self._window
        if self._session.collection_mode:
            window.statusBar().showMessage("Finish collection mode before using commands.")
            return
        preview = window._preview_ctl.preview_if_built()
        preview_is_active = preview is not None and preview.isVisible() and preview.isActiveWindow()
        palette_context = context or ("preview" if preview_is_active else "main")
        if window._active_command_palette is not None and window._active_command_palette.isVisible():
            return
        dialog = self.ensure_dialog(palette_context)
        commands = self.build_commands(palette_context)
        dialog.configure(
            commands,
            recent_command_ids=tuple(window._recent_command_ids),
            title="Zen Commands" if window._zen_mode_enabled else ("Preview Commands" if palette_context == "preview" else "Command Palette"),
        )
        dialog.set_prominent(window._zen_mode_enabled)
        window._command_palette_open = True
        window._active_command_palette = dialog
        self.set_shortcuts_enabled(False)
        dialog.present()

    def setup_shortcuts(self) -> None:
        window = self._window
        window._command_palette_shortcut_main = QShortcut(QKeySequence("Ctrl+K"), window)
        window._command_palette_shortcut_main.setAutoRepeat(False)
        window._command_palette_shortcut_main.activated.connect(lambda: self.open(context="main"))

    def attach_preview_shortcut(self, preview) -> None:
        """The popout's own palette shortcut, created with the (lazily built)
        popout. It joins in whatever key and enabled state the window's one
        currently has, since setup_shortcuts / apply_shortcut ran long ago."""
        window = self._window
        shortcut = QShortcut(QKeySequence("Ctrl+K"), preview)
        shortcut.setAutoRepeat(False)
        shortcut.activated.connect(lambda: self.open(context="preview"))
        main_shortcut = window._command_palette_shortcut_main
        if main_shortcut is not None:
            shortcut.setKey(main_shortcut.key())
            shortcut.setEnabled(main_shortcut.isEnabled())
        window._command_palette_shortcut_preview = shortcut

    def set_shortcuts_enabled(self, enabled: bool) -> None:
        window = self._window
        if window.actions is not None:
            window.actions.open_command_palette.setEnabled(enabled)
        if window._command_palette_shortcut_main is not None:
            window._command_palette_shortcut_main.setEnabled(enabled)
        if window._command_palette_shortcut_preview is not None:
            window._command_palette_shortcut_preview.setEnabled(enabled)

    def apply_shortcut(self, shortcut: str) -> None:
        window = self._window
        sequence = QKeySequence(shortcut)
        if window._command_palette_shortcut_main is not None:
            window._command_palette_shortcut_main.setKey(sequence)
        if window._command_palette_shortcut_preview is not None:
            window._command_palette_shortcut_preview.setKey(sequence)
        if window.actions is not None:
            window.actions.open_command_palette.setShortcut(sequence)
            window._settings_ctl.refresh_action_shortcut_hint(window.actions.open_command_palette)

    def ensure_dialog(self, context: str) -> CommandPaletteDialog:
        window = self._window
        existing = window._command_palette_dialogs.get(context)
        if existing is not None:
            return existing
        preview = window._preview_ctl.preview_if_built() if context == "preview" else None
        parent = preview if preview is not None and preview.isVisible() else window
        dialog = CommandPaletteDialog([], recent_command_ids=(), parent=parent)
        dialog.finished.connect(window._command_palette.handle_finished)
        window._command_palette_dialogs[context] = dialog
        return dialog

    def handle_finished(self, result: int) -> None:
        window = self._window
        dialog = window.sender()
        if not isinstance(dialog, CommandPaletteDialog):
            window._command_palette_open = False
            window._active_command_palette = None
            self.set_shortcuts_enabled(True)
            return
        window._command_palette_open = False
        window._active_command_palette = None
        self.set_shortcuts_enabled(True)
        if result != dialog.DialogCode.Accepted:
            return
        command = dialog.selected_command
        if command is None:
            return
        self.remember_recent_command(command.id)
        command.callback()

    def build_commands(self, context: str) -> list[PaletteCommand]:
        window = self._window
        commands: list[PaletteCommand] = []
        add_action_command = self._action_command_adder(commands)

        # The order below is the order commands appear in the palette's result list.
        if window.actions is not None:
            actions = window.actions
            self._add_file_and_edit_commands(add_action_command, actions)
            self._add_tool_commands(add_action_command, actions, window)
            self._add_export_commands(add_action_command, actions)
            self._add_library_commands(add_action_command, actions)
            self._add_review_commands(add_action_command, actions, window)
            self._add_view_commands(add_action_command, actions, window)
            self._add_search_commands(add_action_command, actions)
            self._add_ai_commands(add_action_command, actions)
            self._add_workspace_and_help_commands(add_action_command, actions)
            self._add_appearance_commands(add_action_command, actions)
            self._add_sort_commands(add_action_command, actions)
            self._add_quick_filter_commands(add_action_command, actions)
            self._add_column_commands(add_action_command, actions)

        self._add_dock_commands(commands, window)
        self._add_filter_preset_commands(commands, window)
        self._add_workflow_recipe_commands(commands, window)
        self._add_workspace_preset_commands(commands, window)
        self._add_collection_commands(commands, window)
        self._add_catalog_root_commands(commands, window)
        self._add_recent_destination_commands(commands, window)

        if context == "preview" and window._preview_ctl.preview_is_visible():
            self._add_preview_commands(commands, window)

        return commands

    def _action_command_adder(self, commands: list[PaletteCommand]) -> _AddActionCommand:
        """The ``add_action_command`` callable the action sections share: it appends a
        command that triggers ``action``, unless the action is missing or disabled."""

        def add_action_command(
            command_id: str,
            action,
            *,
            section: str,
            title: str | None = None,
            subtitle: str = "",
            keywords: tuple[str, ...] = (),
        ) -> None:
            if action is None or not action.isEnabled():
                return
            commands.append(
                PaletteCommand(
                    id=command_id,
                    title=title or self.clean_command_text(action.text()),
                    subtitle=subtitle,
                    section=section,
                    shortcut=action.shortcut().toString(),
                    keywords=keywords,
                    callback=action.trigger,
                )
            )

        return add_action_command

    def _add_preview_commands(self, commands: list[PaletteCommand], window: "MainWindow") -> None:
        """Commands for the popout viewer; only offered while it is on screen, so
        ``window.preview`` is already built."""
        focused_path = window.preview.focused_path()
        photoshop_path = window.preview.focused_photoshop_path()
        commands.extend(self._preview_view_commands(window))
        commands.extend(self._preview_focus_assist_commands(window))
        if focused_path:
            commands.extend(self._preview_focused_image_commands(window, focused_path))
        if photoshop_path and window._photoshop_executable:
            commands.append(self._preview_photoshop_command(window, photoshop_path))

    def _add_file_and_edit_commands(self, add_action_command: _AddActionCommand, actions: MainWindowActions) -> None:
        """File and Edit menu commands."""
        add_action_command("file.open_folder", actions.open_folder, section="File", keywords=("open directory", "browse folder"))
        add_action_command("file.refresh_folder", actions.refresh_folder, section="File", keywords=("reload folder", "rescan"))
        add_action_command("file.new_folder", actions.new_folder, section="File", keywords=("create folder", "new directory"))
        add_action_command("file.workflow_settings", actions.workflow_settings, section="File", keywords=("preferences", "settings"))
        add_action_command("edit.undo", actions.undo, section="Edit", keywords=("revert", "undo last action"))
        add_action_command("edit.rename_selection", actions.rename_selection, section="Edit", keywords=("rename image", "rename file"))

    def _add_tool_commands(self, add_action_command: _AddActionCommand, actions: MainWindowActions, window: "MainWindow") -> None:
        """Batch tools, archive extraction and performance diagnostics."""
        add_action_command("tools.batch_rename", actions.batch_rename_selection, section="Tools", keywords=("batch rename tool", "rename many"))
        add_action_command("tools.batch_resize", actions.batch_resize_selection, section="Tools", keywords=("batch resize tool", "resize many", "convert size"))
        add_action_command("tools.batch_convert", actions.batch_convert_selection, section="Tools", keywords=("batch convert tool", "convert format", "png jpg webp"))
        add_action_command("tools.extract_archive", actions.extract_archive, section="Tools", keywords=("extract archive", "unzip", "decompress", "7z"))
        add_action_command("tools.performance_logging", actions.performance_logging, section="Tools", subtitle=self.toggle_state_text(window._performance_logging_enabled), keywords=("diagnostics", "profiler", "performance log", "speed"))
        add_action_command("tools.open_performance_logs", actions.open_performance_log_folder, section="Tools", keywords=("diagnostics", "profiler", "logs", "performance"))

    def _add_export_commands(self, add_action_command: _AddActionCommand, actions: MainWindowActions) -> None:
        """Handoff and export workflow commands. The Export section also files the
        keyboard shortcuts dialog and workspace-preset saving, as it always has."""
        add_action_command("workflow.handoff_builder", actions.handoff_builder, section="Export", keywords=("delivery", "handoff", "export workflow"))
        add_action_command("workflow.share_to_phone", actions.share_to_phone, section="Export", keywords=("phone", "qr", "pocketdrop", "share", "transfer", "send"))
        add_action_command("workflow.send_to_editor", actions.send_to_editor_pipeline, section="Export", keywords=("retouch", "editor queue", "send to editor"))
        add_action_command("workflow.best_of", actions.best_of_set_auto_assembly, section="Export", keywords=("best of", "shortlist", "auto assembly"))
        add_action_command("workflow.keyboard_shortcuts", actions.keyboard_shortcuts, section="Export", keywords=("shortcuts", "keyboard mapping"))
        add_action_command("workflow.save_workspace", actions.save_workspace_preset, section="Export", keywords=("workspace preset", "save layout"))

    def _add_library_commands(self, add_action_command: _AddActionCommand, actions: MainWindowActions) -> None:
        """Virtual collections and the cross-folder catalog."""
        add_action_command("library.create_collection", actions.create_virtual_collection, section="Library", keywords=("virtual collection", "portfolio picks", "proofing set"))
        add_action_command("library.add_to_collection", actions.add_selection_to_collection, section="Library", keywords=("collection", "save picks"))
        add_action_command("library.remove_from_collection", actions.remove_selection_from_collection, section="Library", keywords=("collection", "remove picks"))
        add_action_command("library.delete_collection", actions.delete_virtual_collection, section="Library", keywords=("collection", "delete set"))
        add_action_command("library.browse_catalog", actions.browse_catalog, section="Library", keywords=("catalog", "cross folder search", "global index"))
        add_action_command("library.add_current_to_catalog", actions.add_current_folder_to_catalog, section="Library", keywords=("catalog root", "index current folder"))
        add_action_command("library.add_folder_to_catalog", actions.add_folder_to_catalog, section="Library", keywords=("catalog root", "index folder"))
        add_action_command("library.remove_catalog_root", actions.remove_catalog_folder, section="Library", keywords=("catalog root", "remove folder"))
        add_action_command("library.refresh_catalog", actions.refresh_catalog, section="Library", keywords=("refresh catalog", "reindex library"))
        add_action_command("library.rebuild_open_folder_cache", actions.rebuild_folder_catalog_cache, section="Library", keywords=("rebuild cache", "rebuild folder cache", "rescan without cache"))

    def _add_review_commands(self, add_action_command: _AddActionCommand, actions: MainWindowActions, window: "MainWindow") -> None:
        """Opening the preview and acting on the selection."""
        add_action_command("review.open_preview", actions.open_preview, section="Review", keywords=("viewer", "popout", "fullscreen"))
        add_action_command("review.accept_selection", actions.accept_selection, section="Review", keywords=("winner", "approve", "accept"))
        add_action_command("review.reject_selection", actions.reject_selection, section="Review", keywords=("reject", "decline"))
        add_action_command("review.keep_selection", actions.keep_selection, section="Review", keywords=("keep", "_keep"))
        add_action_command("review.move_selection", actions.move_selection, section="Review", keywords=("relocate", "move"))
        add_action_command(
            "review.move_selection_to_new_folder",
            actions.move_selection_to_new_folder,
            section="Review",
            keywords=("new folder", "move to new folder", "subfolder"),
        )
        add_action_command("review.delete_selection", actions.delete_selection, section="Review", keywords=("trash", "remove", "delete"))
        add_action_command("review.restore_selection", actions.restore_selection, section="Review", keywords=("recover", "restore"))
        add_action_command("review.reveal_in_explorer", actions.reveal_in_explorer, section="Review", keywords=("show in explorer", "reveal file"))
        add_action_command("review.photoshop", actions.open_in_photoshop, section="Review", keywords=("edit in photoshop",))
        add_action_command("review.compare_mode", actions.compare_mode, section="Review", subtitle=self.toggle_state_text(window._compare_enabled), keywords=("toggle compare",))
        add_action_command("review.auto_advance", actions.auto_advance, section="Review", subtitle=self.toggle_state_text(window._auto_advance_enabled), keywords=("toggle auto advance",))

    def _add_view_commands(self, add_action_command: _AddActionCommand, actions: MainWindowActions, window: "MainWindow") -> None:
        """Grid/details views, details navigation, Zen mode, smart groups and hidden folders."""
        add_action_command("view.grid_view", actions.grid_view, section="View", subtitle="Current view" if self._session.browser_view_mode == "grid" else "", keywords=("grid", "thumbnail grid", "tiles"))
        add_action_command("view.details_view", actions.details_view, section="View", subtitle="Current view" if self._session.browser_view_mode == "details" else "", keywords=("details", "list view", "file explorer"))
        add_action_command("view.details_density_compact", actions.details_density_compact, section="View", subtitle="Current density" if window._details_row_density == "compact" else "", keywords=("details density", "compact rows", "row density"))
        add_action_command("view.details_density_comfortable", actions.details_density_comfortable, section="View", subtitle="Current density" if window._details_row_density == "comfortable" else "", keywords=("details density", "comfortable rows", "row density"))
        add_action_command("view.details_next_unreviewed", actions.details_next_unreviewed, section="View", keywords=("details next unreviewed", "jump unreviewed"))
        add_action_command("view.details_next_kept", actions.details_next_kept, section="View", keywords=("details next kept", "jump kept", "jump winner"))
        add_action_command("view.details_next_rejected", actions.details_next_rejected, section="View", keywords=("details next rejected", "jump rejected"))
        add_action_command("view.zen_mode", actions.zen_mode, section="View", subtitle=self.toggle_state_text(window._zen_mode_enabled), keywords=("fullscreen", "focus mode", "hide panels"))
        add_action_command("view.burst_groups", actions.burst_groups, section="View", subtitle=self.toggle_state_text(window._burst_groups_enabled), keywords=("burst grouping", "burst shots", "toggle bursts", "capture sequence"))
        add_action_command("view.burst_stacks", actions.burst_stacks, section="View", subtitle=self.toggle_state_text(window._burst_stacks_enabled), keywords=("smart stacks", "cycle group", "stack shots", "duplicate stack"))
        add_action_command("view.show_hidden_folders", actions.show_hidden_folders, section="View", subtitle=self.toggle_state_text(window._show_hidden_folders), keywords=("hidden folders", "show hidden", "dot folders", "system folders"))

    def _add_search_commands(self, add_action_command: _AddActionCommand, actions: MainWindowActions) -> None:
        """Advanced filters and saved searches."""
        add_action_command("search.advanced_filters", actions.advanced_filters, section="Search", keywords=("metadata filters", "search filters"))
        add_action_command("search.save_current", actions.save_filter_preset, section="Search", keywords=("save search", "save preset"))
        add_action_command("search.delete_current", actions.delete_filter_preset, section="Search", keywords=("delete search", "remove preset"))
        add_action_command("search.clear_filters", actions.clear_filters, section="Search", keywords=("reset filters", "clear search"))

    def _add_ai_commands(self, add_action_command: _AddActionCommand, actions: MainWindowActions) -> None:
        """AI setup, workflow, results, review tools, people and adapter labels."""
        add_action_command("ai.setup", actions.install_ai_runtime, section="AI", keywords=("runtime", "dependencies", "install ai", "pytorch", "models", "clip", "topiq"))
        add_action_command("ai.workflow_center", actions.open_ai_workflow_center, section="AI", keywords=("workflow center", "ai workflow", "guide", "steps", "wizard"))
        add_action_command("ai.run_pipeline", actions.run_ai_culling, section="AI", keywords=("start ai", "run ai culler", "rank images"))
        add_action_command("ai.quick_rerank", actions.quick_rerank_ai_culling, section="AI", keywords=("quick rerank", "rerank", "re-rank", "rerun rank", "fast rerank", "rerank only"))
        add_action_command("ai.apply_culling", actions.apply_ai_culling, section="AI", keywords=("apply ai culling", "auto cull", "move ai picks", "recycle ai rejects"))
        add_action_command("ai.sort_semantic_folders", actions.sort_ai_semantic_folders, section="AI", keywords=("semantic folders", "classify folders", "sort by ai class", "sort by semantic label"))
        add_action_command("ai.reset_cache", actions.reset_ai_review_cache, section="AI", keywords=("reset ai cache", "rerun ai from scratch", "clear embeddings", "delete ai artifacts"))
        add_action_command("ai.load_saved", actions.load_saved_ai, section="AI", keywords=("load cached ai",))
        add_action_command("ai.load_results", actions.load_ai_results, section="AI", keywords=("import ai results",))
        add_action_command("ai.clear_results", actions.clear_ai_results, section="AI", keywords=("remove ai results",))
        add_action_command("ai.open_report", actions.open_ai_report, section="AI", keywords=("html report",))
        add_action_command("ai.review_summary", actions.show_ai_review_summary, section="AI", keywords=("ai review summary", "last ai run summary"))
        for mode, action in actions.ai_state_actions.items():
            add_action_command(f"ai.state_filter.{mode.name.lower()}", action, section="AI", keywords=("ai state filter", "ai quick filter"))
        add_action_command("ai.tag_legend", actions.ai_review_tag_legend, section="AI", keywords=("ai tags", "tag legend", "ai badges", "what do the ai tags mean"))
        add_action_command("ai.next_top_pick", actions.next_ai_pick, section="AI", keywords=("next ai pick", "jump ai"))
        add_action_command("ai.next_unreviewed_top_pick", actions.next_unreviewed_ai_pick, section="AI", keywords=("unreviewed ai pick",))
        add_action_command("ai.compare_group", actions.compare_ai_group, section="AI", keywords=("compare ai cluster", "group compare"))
        add_action_command("ai.people", actions.manage_people, section="AI", keywords=("people", "faces", "person names", "face search"))
        add_action_command("ai.review_disagreements", actions.review_ai_disagreements, section="AI", keywords=("disagreements", "ai vs review"))
        add_action_command("ai.download_model", actions.download_ai_model, section="AI", keywords=("download ai model", "set up ai"))
        add_action_command("ai.review_adapter_labels", actions.review_ai_adapter_labels, section="AI", keywords=("review adapter labels", "adapter training"))

    def _add_workspace_and_help_commands(self, add_action_command: _AddActionCommand, actions: MainWindowActions) -> None:
        """Window layout reset and the Help menu entries."""
        add_action_command("window.reset_layout", actions.reset_layout, section="Workspace", keywords=("restore layout", "default workspace"))
        add_action_command("help.keyboard_help", actions.keyboard_help, section="Help", keywords=("quick help", "shortcuts", "help"))
        add_action_command("help.ai_guide", actions.ai_guide, section="Help", keywords=("ai guide", "ai training guide", "model guide", "ai help"))
        add_action_command("help.ai_tag_legend", actions.ai_review_tag_legend, section="Help", keywords=("ai tags", "ai review tags", "tag legend", "badge legend"))
        add_action_command("help.advanced_help", actions.advanced_help, section="Help", keywords=("advanced help", "reference", "guide"))
        add_action_command("help.check_updates", actions.check_for_updates, section="Help", keywords=("update", "installer", "new version", "upgrade"))
        add_action_command("help.about", actions.about, section="Help", keywords=("about", "version"))

    def _add_appearance_commands(self, add_action_command: _AddActionCommand, actions: MainWindowActions) -> None:
        """One command per appearance profile."""
        for mode, action in actions.appearance_actions.items():
            label = appearance_mode_label(mode)
            add_action_command(
                f"appearance.{mode.value.casefold()}",
                action,
                section="Appearance",
                title=f"Set Theme: {label}",
                keywords=("theme", "appearance", mode.value.casefold(), label.casefold()),
            )

    def _add_sort_commands(self, add_action_command: _AddActionCommand, actions: MainWindowActions) -> None:
        """One command per sort mode."""
        for mode, action in actions.sort_actions.items():
            add_action_command(
                f"sort.{mode.name.casefold()}",
                action,
                section="View",
                title=f"View: Sort By {mode.value}",
                keywords=("view", "sort", mode.value.casefold()),
            )

    def _add_quick_filter_commands(self, add_action_command: _AddActionCommand, actions: MainWindowActions) -> None:
        """One command per quick filter mode."""
        for mode, action in actions.filter_actions.items():
            add_action_command(
                f"quick_filter.{mode.name.casefold()}",
                action,
                section="View",
                title=f"View: Quick Filter {mode.value}",
                keywords=("view", "quick filter", "filter", mode.value.casefold()),
            )

    def _add_column_commands(self, add_action_command: _AddActionCommand, actions: MainWindowActions) -> None:
        """One command per grid column count."""
        for count, action in actions.column_actions.items():
            add_action_command(
                f"columns.{count}",
                action,
                section="View",
                title=f"View: Columns {count} Across",
                keywords=("view", "columns", f"{count} across"),
            )

    def _add_dock_commands(self, commands: list[PaletteCommand], window: "MainWindow") -> None:
        """Show/hide commands for the workspace panels."""
        if window.workspace_docks is not None:
            for key, action in window.workspace_docks.toggle_actions.items():
                panel_title = key.title()
                commands.append(
                    PaletteCommand(
                        id=f"dock.{key}",
                        title=f"{'Hide' if action.isChecked() else 'Show'} {panel_title}",
                        subtitle="Workspace panel",
                        section="Workspace",
                        keywords=(panel_title.casefold(), "panel", "dock", "sidebar"),
                        callback=action.trigger,
                    )
                )

    def _add_filter_preset_commands(self, commands: list[PaletteCommand], window: "MainWindow") -> None:
        """Apply commands for the built-in smart filters and the user's saved searches."""
        for preset in builtin_filter_presets():
            commands.append(
                PaletteCommand(
                    id=f"smart_filter.{preset.name.casefold().replace(' ', '_')}",
                    title=f"Apply Smart Filter: {preset.name}",
                    subtitle=self.preset_subtitle(preset),
                    section="Search",
                    keywords=("smart filter", "saved search", preset.name.casefold()),
                    callback=lambda target=preset: window._records_view.apply_filter_preset(target),
                )
            )
        for preset in window._saved_filter_presets:
            commands.append(
                PaletteCommand(
                    id=f"saved_filter.{preset.name.casefold()}",
                    title=f"Apply Saved Search: {preset.name}",
                    subtitle=self.preset_subtitle(preset),
                    section="Search",
                    keywords=("saved search", "preset", preset.name.casefold()),
                    callback=lambda target=preset: window._records_view.apply_filter_preset(target),
                )
            )

    def _add_workflow_recipe_commands(self, commands: list[PaletteCommand], window: "MainWindow") -> None:
        """Run commands for the built-in and saved export recipes."""
        for recipe in built_in_workflow_recipes():
            commands.append(
                PaletteCommand(
                    id=f"workflow_recipe.{recipe.key}",
                    title=f"Run Export Recipe: {recipe.name}",
                    subtitle=recipe.description or "Built-in export recipe",
                    section="Export",
                    keywords=("workflow recipe", "export recipe", recipe.name.casefold(), recipe.key),
                    callback=lambda target=recipe: window._export_jobs.run_workflow_recipe(target),
                )
            )
        for recipe in window._saved_workflow_recipes:
            commands.append(
                PaletteCommand(
                    id=f"saved_workflow_recipe.{recipe.key}",
                    title=f"Run Saved Recipe: {recipe.name}",
                    subtitle=recipe.description or "Saved export recipe",
                    section="Export",
                    keywords=("saved recipe", "workflow recipe", "export recipe", recipe.name.casefold()),
                    callback=lambda target=recipe: window._export_jobs.run_workflow_recipe(target),
                )
            )

    def _add_workspace_preset_commands(self, commands: list[PaletteCommand], window: "MainWindow") -> None:
        """Apply commands for the built-in and saved workspace presets."""
        for preset in built_in_workspace_presets():
            commands.append(
                PaletteCommand(
                    id=f"workspace_preset.{preset.key}",
                    title=f"Apply Workspace Preset: {preset.name}",
                    subtitle=preset.description,
                    section="Workspace",
                    keywords=("workspace preset", preset.name.casefold(), preset.key),
                    callback=lambda target=preset: window._settings_ctl.apply_workspace_preset(target),
                )
            )
        for preset in window._saved_workspace_presets:
            commands.append(
                PaletteCommand(
                    id=f"saved_workspace_preset.{preset.key}",
                    title=f"Apply Saved Workspace: {preset.name}",
                    subtitle=preset.description or "Saved workspace preset",
                    section="Workspace",
                    keywords=("saved workspace", "workspace preset", preset.name.casefold()),
                    callback=lambda target=preset: window._settings_ctl.apply_workspace_preset(target),
                )
            )

    def _add_collection_commands(self, commands: list[PaletteCommand], window: "MainWindow") -> None:
        """Open commands for the saved virtual collections."""
        for collection in window._library_store.list_collections():
            commands.append(
                PaletteCommand(
                    id=f"collection.{collection.id}",
                    title=f"Open Collection: {collection.name}",
                    subtitle=collection.description or f"{collection.kind} | {collection.item_count} item(s)",
                    section="Library",
                    keywords=("collection", collection.name.casefold(), collection.kind.casefold()),
                    callback=lambda target=collection.id: window._catalog.open_virtual_collection(target),
                )
            )

    def _add_catalog_root_commands(self, commands: list[PaletteCommand], window: "MainWindow") -> None:
        """Browse commands for the configured catalog roots."""
        for root in window._library_store.list_catalog_roots():
            root_label = Path(root.path).name or root.path
            commands.append(
                PaletteCommand(
                    id=f"catalog.{normalized_path_key(root.path)}",
                    title=f"Browse Catalog Root: {root_label}",
                    subtitle=f"{root.indexed_record_count} indexed bundle(s)",
                    section="Library",
                    keywords=("catalog", "library", root_label.casefold()),
                    callback=lambda target=root.path: window._catalog.browse_catalog(root_path_override=target),
                )
            )

    def _add_recent_destination_commands(self, commands: list[PaletteCommand], window: "MainWindow") -> None:
        """Move-selection commands for the most recent destination folders."""
        for destination in window._navigation.recent_destination_paths(exclude_current_folder=True)[:6]:
            label = Path(destination).name or destination
            commands.append(
                PaletteCommand(
                    id=f"recent.move.{normalized_path_key(destination)}",
                    title=f"Move Selection To Recent Folder: {label}",
                    subtitle=destination,
                    section="Review",
                    keywords=("recent folder", "move recent", "destination"),
                    callback=lambda target=destination: window._record_ops.move_selected_records_to_destination(target),
                )
            )

    def _preview_view_commands(self, window: "MainWindow") -> list[PaletteCommand]:
        """Close, navigate, compare, zoom, loupe and focus-assist toggles for the open preview."""
        return [
            PaletteCommand(
                id="preview.close",
                title="Close Preview",
                subtitle="Close the preview window",
                section="Preview",
                shortcut="Esc",
                keywords=("close viewer", "exit preview"),
                callback=window.preview.close,
            ),
            PaletteCommand(
                id="preview.previous",
                title="Previous Image",
                subtitle="Move to the previous visible image",
                section="Preview",
                keywords=("previous", "back", "left"),
                callback=lambda: window.preview.navigate_relative(-1),
            ),
            PaletteCommand(
                id="preview.next",
                title="Next Image",
                subtitle="Move to the next visible image",
                section="Preview",
                keywords=("next", "forward", "right"),
                callback=lambda: window.preview.navigate_relative(1),
            ),
            PaletteCommand(
                id="preview.compare",
                title="Toggle Compare Mode",
                subtitle=self.toggle_state_text(window.preview.compare_mode_enabled()),
                section="Preview",
                shortcut="C",
                keywords=("compare", "compare mode"),
                callback=window.preview.toggle_compare_mode,
            ),
            PaletteCommand(
                id="preview.zoom",
                title="Toggle Zoom",
                subtitle="Switch between fit and manual zoom",
                section="Preview",
                shortcut="Z",
                keywords=("zoom", "magnify"),
                callback=window.preview.toggle_zoom_command,
            ),
            PaletteCommand(
                id="preview.fit",
                title="Fit To Screen",
                subtitle="Return the preview to fit mode",
                section="Preview",
                shortcut="0",
                keywords=("fit", "fit screen", "reset zoom"),
                callback=window.preview.fit_to_screen,
            ),
            PaletteCommand(
                id="preview.loupe",
                title="Toggle Loupe",
                subtitle="Enable or disable the loupe overlay",
                section="Preview",
                shortcut="L",
                keywords=("loupe", "magnifier"),
                callback=window.preview.toggle_loupe_command,
            ),
            PaletteCommand(
                id="preview.focus_assist",
                title="Toggle Focus Assist",
                subtitle=(
                    f"{self.toggle_state_text(window.preview.focus_assist_enabled())}"
                    f" | {window.preview.focus_assist_color().label}"
                    f" | {window.preview.focus_assist_strength().label}"
                ),
                section="Preview",
                shortcut="F",
                keywords=("focus assist", "focus", "inspection", "detail", "sensitivity"),
                callback=window.preview.toggle_focus_assist_command,
            ),
            PaletteCommand(
                id="preview.focus_assist_background",
                title="Toggle Focus Assist Background Filter",
                subtitle="Dimmed background" if window.preview.focus_assist_dim_background() else "Original image background",
                section="Preview",
                keywords=("focus assist", "background", "filter", "dim background", "overlay"),
                callback=window.preview.toggle_focus_assist_background_command,
            ),
        ]

    def _preview_focus_assist_commands(self, window: "MainWindow") -> list[PaletteCommand]:
        """Pick-a-colour and pick-a-sensitivity commands for focus assist."""
        commands: list[PaletteCommand] = []
        for color in FOCUS_ASSIST_COLORS:
            commands.append(
                PaletteCommand(
                    id=f"preview.focus_assist_color.{color.id}",
                    title=f"Set Focus Assist Color: {color.label}",
                    subtitle=(
                        "Current color"
                        if window.preview.focus_assist_color().id == color.id
                        else "Switch focus peaking color"
                    ),
                    section="Preview",
                    keywords=("focus assist", "focus peaking", "color", color.label.casefold()),
                    callback=lambda color_id=color.id: window.preview.set_focus_assist_color_by_id(color_id),
                )
            )
        for strength in FOCUS_ASSIST_STRENGTHS:
            commands.append(
                PaletteCommand(
                    id=f"preview.focus_assist_strength.{strength.id}",
                    title=f"Set Focus Assist Sensitivity: {strength.label}",
                    subtitle=(
                        "Current sensitivity"
                        if window.preview.focus_assist_strength().id == strength.id
                        else "Adjust focus peaking sensitivity"
                    ),
                    section="Preview",
                    keywords=("focus assist", "focus peaking", "sensitivity", strength.label.casefold()),
                    callback=lambda strength_id=strength.id: window.preview.set_focus_assist_strength_by_id(strength_id),
                )
            )
        return commands

    def _preview_focused_image_commands(self, window: "MainWindow", focused_path: str) -> list[PaletteCommand]:
        """Rename, rate, move, delete and tag the image the preview is showing."""
        return [
            PaletteCommand(
                id="preview.rename",
                title="Rename Focused Image...",
                subtitle="Rename the focused image bundle",
                section="Preview",
                shortcut="F2",
                keywords=("rename", "filename"),
                callback=lambda path=focused_path: window._preview_ctl.handle_preview_rename_requested(path),
            ),
            PaletteCommand(
                id="preview.accept",
                title="Mark Focused Image As Winner",
                subtitle="Mark the focused preview image as a winner",
                section="Preview",
                shortcut="W",
                keywords=("accept", "winner", "approve"),
                callback=lambda path=focused_path: window._preview_ctl.handle_preview_winner_requested(path),
            ),
            PaletteCommand(
                id="preview.reject",
                title="Reject Focused Image",
                subtitle="Mark the focused preview image as rejected",
                section="Preview",
                shortcut="X",
                keywords=("reject", "decline"),
                callback=lambda path=focused_path: window._preview_ctl.handle_preview_reject_requested(path),
            ),
            PaletteCommand(
                id="preview.keep",
                title="Move Focused Image To _keep",
                subtitle="Send the focused preview image to the keep folder",
                section="Preview",
                shortcut="K",
                keywords=("keep", "_keep"),
                callback=lambda path=focused_path: window._preview_ctl.handle_preview_keep_requested(path),
            ),
            PaletteCommand(
                id="preview.move",
                title="Move Focused Image...",
                subtitle="Move the focused preview image to another folder",
                section="Preview",
                shortcut="M",
                keywords=("move", "relocate"),
                callback=lambda path=focused_path: window._preview_ctl.handle_preview_move_requested(path),
            ),
            PaletteCommand(
                id="preview.delete",
                title="Delete Focused Image",
                subtitle="Delete the focused preview image",
                section="Preview",
                shortcut="Delete",
                keywords=("delete", "trash", "remove"),
                callback=lambda path=focused_path: window._preview_ctl.handle_preview_delete_requested(path),
            ),
            PaletteCommand(
                id="preview.tag",
                title="Tag Focused Image",
                subtitle="Edit tags for the focused preview image",
                section="Preview",
                shortcut="T",
                keywords=("tag", "keywords"),
                callback=lambda path=focused_path: window._preview_ctl.handle_preview_tag_requested(path),
            ),
        ]

    def _preview_photoshop_command(self, window: "MainWindow", photoshop_path: str) -> PaletteCommand:
        """Open the focused image in Photoshop."""
        return PaletteCommand(
            id="preview.photoshop",
            title="Open Focused Image In Photoshop",
            subtitle="Send the focused preview image to Photoshop",
            section="Preview",
            keywords=("photoshop", "edit"),
            callback=lambda path=photoshop_path: window._preview_ctl.open_preview_image_in_photoshop(path),
        )

    def preset_subtitle(self, preset: SavedFilterPreset) -> str:
        labels = active_filter_labels(preset.query)
        if not labels:
            return "All images"
        return " | ".join(labels[:3])

    @staticmethod
    def clean_command_text(text: str) -> str:
        return (text or "").replace("&", "").replace("...", "").strip()

    @staticmethod
    def toggle_state_text(enabled: bool) -> str:
        return "On" if enabled else "Off"

    def remember_recent_command(self, command_id: str) -> None:
        window = self._window
        window._recent_command_ids = [command_id, *[item for item in window._recent_command_ids if item != command_id]][:12]
        window._settings_ctl.save_recent_command_ids()
