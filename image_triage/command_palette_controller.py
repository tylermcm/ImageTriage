from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtGui import QKeySequence, QShortcut

from .filtering import SavedFilterPreset, active_filter_labels, builtin_filter_presets
from .review_tools import FOCUS_ASSIST_COLORS, FOCUS_ASSIST_STRENGTHS
from .scanner import normalized_path_key
from .ui import CommandPaletteDialog, PaletteCommand, appearance_mode_label
from .workflows import built_in_workflow_recipes, built_in_workspace_presets

if TYPE_CHECKING:
    from .window import MainWindow


class CommandPaletteController:
    """Builds and drives the Ctrl+K command palette. Holds no state of its
    own beyond the window back-reference; the palette's open/visible state,
    shortcuts, cached per-context dialogs and recent-command list all live
    on MainWindow, the same way they did before this extraction."""

    def __init__(self, window: "MainWindow") -> None:
        self._window = window

    def open(self, _checked: bool = False, *, context: str | None = None) -> None:
        window = self._window
        if window._collection_mode:
            window.statusBar().showMessage("Finish collection mode before using commands.")
            return
        palette_context = context or ("preview" if window.preview.isVisible() and window.preview.isActiveWindow() else "main")
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
        window._command_palette_shortcut_preview = QShortcut(QKeySequence("Ctrl+K"), window.preview)
        window._command_palette_shortcut_preview.setAutoRepeat(False)
        window._command_palette_shortcut_preview.activated.connect(lambda: self.open(context="preview"))

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
            window._refresh_action_shortcut_hint(window.actions.open_command_palette)

    def ensure_dialog(self, context: str) -> CommandPaletteDialog:
        window = self._window
        existing = window._command_palette_dialogs.get(context)
        if existing is not None:
            return existing
        parent = window.preview if context == "preview" and window.preview.isVisible() else window
        dialog = CommandPaletteDialog([], recent_command_ids=(), parent=parent)
        dialog.finished.connect(window._handle_command_palette_finished)
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

        if window.actions is not None:
            add_action_command("file.open_folder", window.actions.open_folder, section="File", keywords=("open directory", "browse folder"))
            add_action_command("file.refresh_folder", window.actions.refresh_folder, section="File", keywords=("reload folder", "rescan"))
            add_action_command("file.new_folder", window.actions.new_folder, section="File", keywords=("create folder", "new directory"))
            add_action_command("file.workflow_settings", window.actions.workflow_settings, section="File", keywords=("preferences", "settings"))
            add_action_command("edit.undo", window.actions.undo, section="Edit", keywords=("revert", "undo last action"))
            add_action_command("edit.rename_selection", window.actions.rename_selection, section="Edit", keywords=("rename image", "rename file"))
            add_action_command("tools.batch_rename", window.actions.batch_rename_selection, section="Tools", keywords=("batch rename tool", "rename many"))
            add_action_command("tools.batch_resize", window.actions.batch_resize_selection, section="Tools", keywords=("batch resize tool", "resize many", "convert size"))
            add_action_command("tools.batch_convert", window.actions.batch_convert_selection, section="Tools", keywords=("batch convert tool", "convert format", "png jpg webp"))
            add_action_command("tools.extract_archive", window.actions.extract_archive, section="Tools", keywords=("extract archive", "unzip", "decompress", "7z"))
            add_action_command("tools.performance_logging", window.actions.performance_logging, section="Tools", subtitle=self.toggle_state_text(window._performance_logging_enabled), keywords=("diagnostics", "profiler", "performance log", "speed"))
            add_action_command("tools.open_performance_logs", window.actions.open_performance_log_folder, section="Tools", keywords=("diagnostics", "profiler", "logs", "performance"))
            add_action_command("workflow.handoff_builder", window.actions.handoff_builder, section="Workflow", keywords=("delivery", "handoff", "export workflow"))
            add_action_command("workflow.share_to_phone", window.actions.share_to_phone, section="Workflow", keywords=("phone", "qr", "pocketdrop", "share", "transfer", "send"))
            add_action_command("workflow.send_to_editor", window.actions.send_to_editor_pipeline, section="Workflow", keywords=("retouch", "editor queue", "send to editor"))
            add_action_command("workflow.best_of", window.actions.best_of_set_auto_assembly, section="Workflow", keywords=("best of", "shortlist", "auto assembly"))
            add_action_command("workflow.keyboard_shortcuts", window.actions.keyboard_shortcuts, section="Workflow", keywords=("shortcuts", "keyboard mapping"))
            add_action_command("workflow.save_workspace", window.actions.save_workspace_preset, section="Workflow", keywords=("workspace preset", "save layout"))
            add_action_command("library.create_collection", window.actions.create_virtual_collection, section="Library", keywords=("virtual collection", "portfolio picks", "proofing set"))
            add_action_command("library.add_to_collection", window.actions.add_selection_to_collection, section="Library", keywords=("collection", "save picks"))
            add_action_command("library.remove_from_collection", window.actions.remove_selection_from_collection, section="Library", keywords=("collection", "remove picks"))
            add_action_command("library.delete_collection", window.actions.delete_virtual_collection, section="Library", keywords=("collection", "delete set"))
            add_action_command("library.browse_catalog", window.actions.browse_catalog, section="Library", keywords=("catalog", "cross folder search", "global index"))
            add_action_command("library.add_current_to_catalog", window.actions.add_current_folder_to_catalog, section="Library", keywords=("catalog root", "index current folder"))
            add_action_command("library.add_folder_to_catalog", window.actions.add_folder_to_catalog, section="Library", keywords=("catalog root", "index folder"))
            add_action_command("library.remove_catalog_root", window.actions.remove_catalog_folder, section="Library", keywords=("catalog root", "remove folder"))
            add_action_command("library.refresh_catalog", window.actions.refresh_catalog, section="Library", keywords=("refresh catalog", "reindex library"))
            add_action_command("library.rebuild_open_folder_cache", window.actions.rebuild_folder_catalog_cache, section="Library", keywords=("rebuild cache", "rebuild folder cache", "rescan without cache"))
            add_action_command("review.open_preview", window.actions.open_preview, section="Review", keywords=("viewer", "popout", "fullscreen"))
            add_action_command("review.accept_selection", window.actions.accept_selection, section="Review", keywords=("winner", "approve", "accept"))
            add_action_command("review.reject_selection", window.actions.reject_selection, section="Review", keywords=("reject", "decline"))
            add_action_command("review.keep_selection", window.actions.keep_selection, section="Review", keywords=("keep", "_keep"))
            add_action_command("review.move_selection", window.actions.move_selection, section="Review", keywords=("relocate", "move"))
            add_action_command(
                "review.move_selection_to_new_folder",
                window.actions.move_selection_to_new_folder,
                section="Review",
                keywords=("new folder", "move to new folder", "subfolder"),
            )
            add_action_command("review.delete_selection", window.actions.delete_selection, section="Review", keywords=("trash", "remove", "delete"))
            add_action_command("review.restore_selection", window.actions.restore_selection, section="Review", keywords=("recover", "restore"))
            add_action_command("review.reveal_in_explorer", window.actions.reveal_in_explorer, section="Review", keywords=("show in explorer", "reveal file"))
            add_action_command("review.photoshop", window.actions.open_in_photoshop, section="Review", keywords=("edit in photoshop",))
            add_action_command("review.compare_mode", window.actions.compare_mode, section="Review", subtitle=self.toggle_state_text(window._compare_enabled), keywords=("toggle compare",))
            add_action_command("review.auto_advance", window.actions.auto_advance, section="Review", subtitle=self.toggle_state_text(window._auto_advance_enabled), keywords=("toggle auto advance",))
            add_action_command("view.grid_view", window.actions.grid_view, section="View", subtitle="Current view" if window._browser_view_mode == "grid" else "", keywords=("grid", "thumbnail grid", "tiles"))
            add_action_command("view.details_view", window.actions.details_view, section="View", subtitle="Current view" if window._browser_view_mode == "details" else "", keywords=("details", "list view", "file explorer"))
            add_action_command("view.details_density_compact", window.actions.details_density_compact, section="View", subtitle="Current density" if window._details_row_density == "compact" else "", keywords=("details density", "compact rows", "row density"))
            add_action_command("view.details_density_comfortable", window.actions.details_density_comfortable, section="View", subtitle="Current density" if window._details_row_density == "comfortable" else "", keywords=("details density", "comfortable rows", "row density"))
            add_action_command("view.details_next_unreviewed", window.actions.details_next_unreviewed, section="View", keywords=("details next unreviewed", "jump unreviewed"))
            add_action_command("view.details_next_kept", window.actions.details_next_kept, section="View", keywords=("details next kept", "jump kept", "jump winner"))
            add_action_command("view.details_next_rejected", window.actions.details_next_rejected, section="View", keywords=("details next rejected", "jump rejected"))
            add_action_command("view.zen_mode", window.actions.zen_mode, section="View", subtitle=self.toggle_state_text(window._zen_mode_enabled), keywords=("fullscreen", "focus mode", "hide panels"))
            add_action_command("view.burst_groups", window.actions.burst_groups, section="View", subtitle=self.toggle_state_text(window._burst_groups_enabled), keywords=("burst grouping", "burst shots", "toggle bursts", "capture sequence"))
            add_action_command("view.burst_stacks", window.actions.burst_stacks, section="View", subtitle=self.toggle_state_text(window._burst_stacks_enabled), keywords=("smart stacks", "cycle group", "stack shots", "duplicate stack"))
            add_action_command("view.show_hidden_folders", window.actions.show_hidden_folders, section="View", subtitle=self.toggle_state_text(window._show_hidden_folders), keywords=("hidden folders", "show hidden", "dot folders", "system folders"))
            add_action_command("search.advanced_filters", window.actions.advanced_filters, section="Search", keywords=("metadata filters", "search filters"))
            add_action_command("search.save_current", window.actions.save_filter_preset, section="Search", keywords=("save search", "save preset"))
            add_action_command("search.delete_current", window.actions.delete_filter_preset, section="Search", keywords=("delete search", "remove preset"))
            add_action_command("search.clear_filters", window.actions.clear_filters, section="Search", keywords=("reset filters", "clear search"))
            add_action_command("ai.setup", window.actions.install_ai_runtime, section="AI", keywords=("runtime", "dependencies", "install ai", "pytorch", "models", "clip", "topiq"))
            add_action_command("ai.workflow_center", window.actions.open_ai_workflow_center, section="AI", keywords=("workflow center", "ai workflow", "guide", "steps", "wizard"))
            add_action_command("ai.run_pipeline", window.actions.run_ai_culling, section="AI", keywords=("start ai", "run ai culler", "rank images"))
            add_action_command("ai.quick_rerank", window.actions.quick_rerank_ai_culling, section="AI", keywords=("quick rerank", "rerank", "re-rank", "rerun rank", "fast rerank", "rerank only"))
            add_action_command("ai.apply_culling", window.actions.apply_ai_culling, section="AI", keywords=("apply ai culling", "auto cull", "move ai picks", "recycle ai rejects"))
            add_action_command("ai.sort_semantic_folders", window.actions.sort_ai_semantic_folders, section="AI", keywords=("semantic folders", "classify folders", "sort by ai class", "sort by semantic label"))
            add_action_command("ai.reset_cache", window.actions.reset_ai_review_cache, section="AI", keywords=("reset ai cache", "rerun ai from scratch", "clear embeddings", "delete ai artifacts"))
            add_action_command("ai.load_saved", window.actions.load_saved_ai, section="AI", keywords=("load cached ai",))
            add_action_command("ai.load_results", window.actions.load_ai_results, section="AI", keywords=("import ai results",))
            add_action_command("ai.clear_results", window.actions.clear_ai_results, section="AI", keywords=("remove ai results",))
            add_action_command("ai.open_report", window.actions.open_ai_report, section="AI", keywords=("html report",))
            add_action_command("ai.review_summary", window.actions.show_ai_review_summary, section="AI", keywords=("ai review summary", "last ai run summary"))
            for mode, action in window.actions.ai_state_actions.items():
                add_action_command(f"ai.state_filter.{mode.name.lower()}", action, section="AI", keywords=("ai state filter", "ai quick filter"))
            add_action_command("ai.tag_legend", window.actions.ai_review_tag_legend, section="AI", keywords=("ai tags", "tag legend", "ai badges", "what do the ai tags mean"))
            add_action_command("ai.next_top_pick", window.actions.next_ai_pick, section="AI", keywords=("next ai pick", "jump ai"))
            add_action_command("ai.next_unreviewed_top_pick", window.actions.next_unreviewed_ai_pick, section="AI", keywords=("unreviewed ai pick",))
            add_action_command("ai.compare_group", window.actions.compare_ai_group, section="AI", keywords=("compare ai cluster", "group compare"))
            add_action_command("ai.people", window.actions.manage_people, section="AI", keywords=("people", "faces", "person names", "face search"))
            add_action_command("ai.review_disagreements", window.actions.review_ai_disagreements, section="AI", keywords=("disagreements", "ai vs review"))
            add_action_command("ai.download_model", window.actions.download_ai_model, section="AI", keywords=("download ai model", "set up ai"))
            add_action_command("ai.review_adapter_labels", window.actions.review_ai_adapter_labels, section="AI", keywords=("review adapter labels", "adapter training"))
            add_action_command("window.reset_layout", window.actions.reset_layout, section="Workspace", keywords=("restore layout", "default workspace"))
            add_action_command("help.keyboard_help", window.actions.keyboard_help, section="Help", keywords=("quick help", "shortcuts", "help"))
            add_action_command("help.ai_guide", window.actions.ai_guide, section="Help", keywords=("ai guide", "ai training guide", "model guide", "ai help"))
            add_action_command("help.ai_tag_legend", window.actions.ai_review_tag_legend, section="Help", keywords=("ai tags", "ai review tags", "tag legend", "badge legend"))
            add_action_command("help.advanced_help", window.actions.advanced_help, section="Help", keywords=("advanced help", "reference", "guide"))
            add_action_command("help.check_updates", window.actions.check_for_updates, section="Help", keywords=("update", "installer", "new version", "upgrade"))
            add_action_command("help.about", window.actions.about, section="Help", keywords=("about", "version"))

            for mode, action in window.actions.appearance_actions.items():
                label = appearance_mode_label(mode)
                add_action_command(
                    f"appearance.{mode.value.casefold()}",
                    action,
                    section="Appearance",
                    title=f"Set Theme: {label}",
                    keywords=("theme", "appearance", mode.value.casefold(), label.casefold()),
                )
            for mode, action in window.actions.sort_actions.items():
                add_action_command(
                    f"sort.{mode.name.casefold()}",
                    action,
                    section="View",
                    title=f"View: Sort By {mode.value}",
                    keywords=("view", "sort", mode.value.casefold()),
                )
            for mode, action in window.actions.filter_actions.items():
                add_action_command(
                    f"quick_filter.{mode.name.casefold()}",
                    action,
                    section="View",
                    title=f"View: Quick Filter {mode.value}",
                    keywords=("view", "quick filter", "filter", mode.value.casefold()),
                )
            for count, action in window.actions.column_actions.items():
                add_action_command(
                    f"columns.{count}",
                    action,
                    section="View",
                    title=f"View: Columns {count} Across",
                    keywords=("view", "columns", f"{count} across"),
                )

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

        for preset in builtin_filter_presets():
            commands.append(
                PaletteCommand(
                    id=f"smart_filter.{preset.name.casefold().replace(' ', '_')}",
                    title=f"Apply Smart Filter: {preset.name}",
                    subtitle=self.preset_subtitle(preset),
                    section="Search",
                    keywords=("smart filter", "saved search", preset.name.casefold()),
                    callback=lambda target=preset: window._apply_filter_preset(target),
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
                    callback=lambda target=preset: window._apply_filter_preset(target),
                )
            )

        for recipe in built_in_workflow_recipes():
            commands.append(
                PaletteCommand(
                    id=f"workflow_recipe.{recipe.key}",
                    title=f"Run Workflow Recipe: {recipe.name}",
                    subtitle=recipe.description or "Built-in workflow recipe",
                    section="Workflow",
                    keywords=("workflow recipe", recipe.name.casefold(), recipe.key),
                    callback=lambda target=recipe: window._run_workflow_recipe(target),
                )
            )
        for recipe in window._saved_workflow_recipes:
            commands.append(
                PaletteCommand(
                    id=f"saved_workflow_recipe.{recipe.key}",
                    title=f"Run Saved Recipe: {recipe.name}",
                    subtitle=recipe.description or "Saved workflow recipe",
                    section="Workflow",
                    keywords=("saved recipe", "workflow recipe", recipe.name.casefold()),
                    callback=lambda target=recipe: window._run_workflow_recipe(target),
                )
            )

        for preset in built_in_workspace_presets():
            commands.append(
                PaletteCommand(
                    id=f"workspace_preset.{preset.key}",
                    title=f"Apply Workspace Preset: {preset.name}",
                    subtitle=preset.description,
                    section="Workspace",
                    keywords=("workspace preset", preset.name.casefold(), preset.key),
                    callback=lambda target=preset: window._apply_workspace_preset(target),
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
                    callback=lambda target=preset: window._apply_workspace_preset(target),
                )
            )

        for collection in window._library_store.list_collections():
            commands.append(
                PaletteCommand(
                    id=f"collection.{collection.id}",
                    title=f"Open Collection: {collection.name}",
                    subtitle=collection.description or f"{collection.kind} | {collection.item_count} item(s)",
                    section="Library",
                    keywords=("collection", collection.name.casefold(), collection.kind.casefold()),
                    callback=lambda target=collection.id: window._open_virtual_collection(target),
                )
            )

        for root in window._library_store.list_catalog_roots():
            root_label = Path(root.path).name or root.path
            commands.append(
                PaletteCommand(
                    id=f"catalog.{normalized_path_key(root.path)}",
                    title=f"Browse Catalog Root: {root_label}",
                    subtitle=f"{root.indexed_record_count} indexed bundle(s)",
                    section="Library",
                    keywords=("catalog", "library", root_label.casefold()),
                    callback=lambda target=root.path: window._browse_catalog(root_path_override=target),
                )
            )

        for destination in window._recent_destination_paths(exclude_current_folder=True)[:6]:
            label = Path(destination).name or destination
            commands.append(
                PaletteCommand(
                    id=f"recent.move.{normalized_path_key(destination)}",
                    title=f"Move Selection To Recent Folder: {label}",
                    subtitle=destination,
                    section="Review",
                    keywords=("recent folder", "move recent", "destination"),
                    callback=lambda target=destination: window._move_selected_records_to_destination(target),
                )
            )

        if context == "preview" and window.preview.isVisible():
            focused_path = window.preview.focused_path()
            photoshop_path = window.preview.focused_photoshop_path()
            commands.extend(
                [
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
            )
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
            if focused_path:
                commands.extend(
                    [
                        PaletteCommand(
                            id="preview.rename",
                            title="Rename Focused Image...",
                            subtitle="Rename the focused image bundle",
                            section="Preview",
                            shortcut="F2",
                            keywords=("rename", "filename"),
                            callback=lambda path=focused_path: window._handle_preview_rename_requested(path),
                        ),
                        PaletteCommand(
                            id="preview.accept",
                            title="Mark Focused Image As Winner",
                            subtitle="Mark the focused preview image as a winner",
                            section="Preview",
                            shortcut="W",
                            keywords=("accept", "winner", "approve"),
                            callback=lambda path=focused_path: window._handle_preview_winner_requested(path),
                        ),
                        PaletteCommand(
                            id="preview.reject",
                            title="Reject Focused Image",
                            subtitle="Mark the focused preview image as rejected",
                            section="Preview",
                            shortcut="X",
                            keywords=("reject", "decline"),
                            callback=lambda path=focused_path: window._handle_preview_reject_requested(path),
                        ),
                        PaletteCommand(
                            id="preview.keep",
                            title="Move Focused Image To _keep",
                            subtitle="Send the focused preview image to the keep folder",
                            section="Preview",
                            shortcut="K",
                            keywords=("keep", "_keep"),
                            callback=lambda path=focused_path: window._handle_preview_keep_requested(path),
                        ),
                        PaletteCommand(
                            id="preview.move",
                            title="Move Focused Image...",
                            subtitle="Move the focused preview image to another folder",
                            section="Preview",
                            shortcut="M",
                            keywords=("move", "relocate"),
                            callback=lambda path=focused_path: window._handle_preview_move_requested(path),
                        ),
                        PaletteCommand(
                            id="preview.delete",
                            title="Delete Focused Image",
                            subtitle="Delete the focused preview image",
                            section="Preview",
                            shortcut="Delete",
                            keywords=("delete", "trash", "remove"),
                            callback=lambda path=focused_path: window._handle_preview_delete_requested(path),
                        ),
                        PaletteCommand(
                            id="preview.tag",
                            title="Tag Focused Image",
                            subtitle="Edit tags for the focused preview image",
                            section="Preview",
                            shortcut="T",
                            keywords=("tag", "keywords"),
                            callback=lambda path=focused_path: window._handle_preview_tag_requested(path),
                        ),
                    ]
                )
            if photoshop_path and window._photoshop_executable:
                commands.append(
                    PaletteCommand(
                        id="preview.photoshop",
                        title="Open Focused Image In Photoshop",
                        subtitle="Send the focused preview image to Photoshop",
                        section="Preview",
                        keywords=("photoshop", "edit"),
                        callback=lambda path=photoshop_path: window._open_preview_image_in_photoshop(path),
                    )
                )

        return commands

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
        window._save_recent_command_ids()
