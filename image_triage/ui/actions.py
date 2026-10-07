from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QStyle

from ..filtering import AIStateFilter
from ..models import FilterMode, SortMode
from .shortcuts import (
    SHORTCUT_REGISTRY,
    apply_shortcut_overrides,
    effective_shortcuts,
    load_shortcut_overrides,
    save_shortcut_overrides,
)
from .theme import AppearanceMode, appearance_mode_label, appearance_profile_modes

if TYPE_CHECKING:
    from ..window import MainWindow


__all__ = (
    "MainWindowActions",
    "SHORTCUT_REGISTRY",
    "apply_shortcut_overrides",
    "build_main_window_actions",
    "effective_shortcuts",
    "format_action_tooltip",
    "load_shortcut_overrides",
    "save_shortcut_overrides",
)


@dataclass(slots=True)
class MainWindowActions:
    open_folder: QAction
    refresh_folder: QAction
    empty_recycle_bin: QAction
    new_folder: QAction
    workflow_settings: QAction
    file_associations: QAction
    reset_layout: QAction
    exit_app: QAction
    undo: QAction
    rename_selection: QAction
    batch_rename_selection: QAction
    batch_resize_selection: QAction
    batch_convert_selection: QAction
    extract_archive: QAction
    accept_selection: QAction
    reject_selection: QAction
    keep_selection: QAction
    move_selection: QAction
    move_selection_to_new_folder: QAction
    delete_selection: QAction
    restore_selection: QAction
    open_preview: QAction
    reveal_in_explorer: QAction
    open_in_photoshop: QAction
    compare_mode: QAction
    auto_advance: QAction
    burst_groups: QAction
    burst_stacks: QAction
    show_hidden_folders: QAction
    grid_view: QAction
    details_view: QAction
    details_density_compact: QAction
    details_density_comfortable: QAction
    details_next_unreviewed: QAction
    details_next_kept: QAction
    details_next_rejected: QAction
    zen_mode: QAction
    install_ai_runtime: QAction
    download_ai_model: QAction
    repair_ai: QAction
    check_ai_readiness: QAction
    copy_ai_diagnostics: QAction
    uninstall_ai_components: QAction
    guided_ai_cull_preferences: QAction
    open_ai_workflow_center: QAction
    run_ai_culling: QAction
    quick_rerank_ai_culling: QAction
    apply_ai_culling: QAction
    sort_ai_semantic_folders: QAction
    reset_ai_review_cache: QAction
    load_saved_ai: QAction
    load_ai_results: QAction
    clear_ai_results: QAction
    open_ai_report: QAction
    manage_people: QAction
    show_ai_review_summary: QAction
    review_ai_adapter_labels: QAction
    next_ai_pick: QAction
    next_unreviewed_ai_pick: QAction
    compare_ai_group: QAction
    review_ai_disagreements: QAction
    winner_ladder_mode: QAction
    create_virtual_collection: QAction
    add_selection_to_collection: QAction
    remove_selection_from_collection: QAction
    delete_virtual_collection: QAction
    browse_catalog: QAction
    add_current_folder_to_catalog: QAction
    add_folder_to_catalog: QAction
    remove_catalog_folder: QAction
    refresh_catalog: QAction
    rebuild_folder_catalog_cache: QAction
    share_to_phone: QAction
    handoff_builder: QAction
    send_to_editor_pipeline: QAction
    best_of_set_auto_assembly: QAction
    keyboard_shortcuts: QAction
    save_workspace_preset: QAction
    show_workspace_toolbar: QAction
    open_command_palette: QAction
    performance_logging: QAction
    open_performance_log_folder: QAction
    advanced_filters: QAction
    save_filter_preset: QAction
    delete_filter_preset: QAction
    clear_filters: QAction
    documentation: QAction
    keyboard_help: QAction
    ai_guide: QAction
    ai_review_tag_legend: QAction
    advanced_help: QAction
    check_for_updates: QAction
    about: QAction
    appearance_actions: dict[AppearanceMode, QAction] = field(default_factory=dict)
    toolbar_placement_actions: dict[str, QAction] = field(default_factory=dict)
    sort_actions: dict[SortMode, QAction] = field(default_factory=dict)
    filter_actions: dict[FilterMode, QAction] = field(default_factory=dict)
    ai_state_actions: dict[AIStateFilter, QAction] = field(default_factory=dict)
    column_actions: dict[int, QAction] = field(default_factory=dict)


def format_action_tooltip(text: str, shortcut: str | QKeySequence | None = None) -> str:
    shortcut_text = (
        shortcut.toString(QKeySequence.SequenceFormat.NativeText)
        if isinstance(shortcut, QKeySequence)
        else str(shortcut or "").strip()
    )
    return f"{text}\nShortcut: {shortcut_text}" if shortcut_text else text


def _create_action(
    window: "MainWindow",
    text: str,
    *,
    slot=None,
    icon: QStyle.StandardPixmap | None = None,
    shortcut: str | QKeySequence.StandardKey | None = None,
    checkable: bool = False,
    auto_repeat: bool = True,
) -> QAction:
    action = QAction(text, window)
    action.setProperty("imageTriageBaseText", text)
    action.setAutoRepeat(auto_repeat)
    if icon is not None:
        action.setIcon(window.style().standardIcon(icon))
    if shortcut is not None:
        action.setShortcut(shortcut)
    shortcut_text = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
    hinted_text = format_action_tooltip(text, shortcut_text)
    action.setToolTip(hinted_text)
    action.setStatusTip(hinted_text)
    try:
        action.setShortcutVisibleInContextMenu(True)
    except AttributeError:
        pass
    if checkable:
        action.setCheckable(True)
    if slot is not None:
        signal = action.toggled if checkable else action.triggered
        signal.connect(slot)
    return action


def _file_actions(window: "MainWindow") -> dict[str, QAction]:
    """File menu, folder management, window layout and exit."""
    return dict(
        open_folder=_create_action(
            window,
            "Open Folder...",
            slot=window._navigation.choose_folder,
            icon=QStyle.StandardPixmap.SP_DialogOpenButton,
            shortcut=QKeySequence.StandardKey.Open,
        ),
        refresh_folder=_create_action(
            window,
            "Refresh Folder",
            slot=window._scan.refresh_folder,
            icon=QStyle.StandardPixmap.SP_BrowserReload,
            shortcut=QKeySequence.StandardKey.Refresh,
        ),
        empty_recycle_bin=_create_action(window, "Empty Recycle Bin", slot=window._recycle_bin.empty_recycle_bin),
        new_folder=_create_action(window, "New Folder...", slot=window._create_folder_in_current_folder, shortcut="Ctrl+Shift+N"),
        workflow_settings=_create_action(
            window,
            "Settings...",
            slot=window._settings_ctl.show_settings,
            shortcut="Ctrl+,",
        ),
        file_associations=_create_action(
            window,
            "File Associations...",
            slot=window._open_file_associations_dialog,
        ),
        reset_layout=_create_action(window, "Reset Window Layout", slot=window._settings_ctl.reset_window_layout),
        exit_app=_create_action(window, "Exit", slot=window.close),
    )


def _edit_and_batch_actions(window: "MainWindow") -> dict[str, QAction]:
    """Undo, rename, the batch tools and archive extraction."""
    return dict(
        undo=_create_action(
            window,
            "Undo",
            slot=window._record_ops.undo_last_action,
            shortcut=QKeySequence.StandardKey.Undo,
        ),
        rename_selection=_create_action(window, "Rename Image...", slot=window._rename_selected_record, shortcut="F2"),
        batch_rename_selection=_create_action(
            window,
            "Batch Rename...",
            slot=window._tool_mode.start_batch_rename_tool_mode,
            shortcut="Ctrl+Shift+R",
        ),
        batch_resize_selection=_create_action(
            window,
            "Batch Resize...",
            slot=window._tool_mode.start_batch_resize_tool_mode,
            shortcut="Ctrl+Shift+E",
        ),
        batch_convert_selection=_create_action(
            window,
            "Batch Convert...",
            slot=window._tool_mode.start_batch_convert_tool_mode,
            shortcut="Ctrl+Shift+C",
        ),
        extract_archive=_create_action(window, "Extract Archive...", slot=window._export_jobs.extract_archive_prompt),
    )


def _selection_review_actions(window: "MainWindow") -> dict[str, QAction]:
    """What happens to the selected images, and opening them elsewhere."""
    return dict(
        accept_selection=_create_action(window, "Mark Winner", slot=window._accept_selected_records),
        reject_selection=_create_action(window, "Reject Selection", slot=window._reject_selected_records),
        keep_selection=_create_action(window, "Move Selection To _keep", slot=window._keep_selected_records),
        move_selection=_create_action(window, "Move Selection...", slot=window._move_selected_records),
        move_selection_to_new_folder=_create_action(
            window,
            "Move Selection To New Folder...",
            slot=window._move_selected_records_to_new_folder,
        ),
        delete_selection=_create_action(
            window,
            "Delete Selection",
            slot=window._delete_selected_records,
            icon=QStyle.StandardPixmap.SP_TrashIcon,
        ),
        restore_selection=_create_action(window, "Restore Selection", slot=window._restore_selected_records),
        open_preview=_create_action(window, "Open Preview", slot=window._preview_ctl.open_current_preview),
        reveal_in_explorer=_create_action(window, "Reveal In File Explorer", slot=window._reveal_current_selection),
        open_in_photoshop=_create_action(window, "Open In Photoshop", slot=window._open_selected_in_photoshop),
    )


def _view_toggle_actions(window: "MainWindow") -> dict[str, QAction]:
    """Compare, auto-advance, smart groups and stacks, and hidden folders."""
    return dict(
        compare_mode=_create_action(
            window,
            "Compare",
            slot=window._views.handle_compare_toggled,
            checkable=True,
            shortcut="C",
        ),
        auto_advance=_create_action(
            window,
            "Auto-Advance",
            slot=window._views.handle_auto_advance_toggled,
            checkable=True,
        ),
        burst_groups=_create_action(
            window,
            "Smart Groups",
            slot=window._views.handle_burst_groups_toggled,
            checkable=True,
        ),
        burst_stacks=_create_action(
            window,
            "Smart Stacks",
            slot=window._views.handle_burst_stacks_toggled,
            checkable=True,
        ),
        show_hidden_folders=_create_action(
            window,
            "Show Hidden Folders",
            slot=window._navigation.handle_show_hidden_folders_toggled,
            checkable=True,
        ),
    )


def _layout_actions(window: "MainWindow") -> dict[str, QAction]:
    """Grid and details views, details row density and navigation, and Zen mode."""
    return dict(
        grid_view=_create_action(
            window,
            "Grid View",
            slot=lambda _checked=False: window._views.set_browser_view_mode("grid"),
            checkable=True,
            shortcut="Ctrl+1",
        ),
        details_view=_create_action(
            window,
            "Details View",
            slot=lambda _checked=False: window._views.set_browser_view_mode("details"),
            checkable=True,
            shortcut="Ctrl+2",
        ),
        details_density_compact=_create_action(
            window,
            "Compact Details Rows",
            slot=lambda _checked=False: window._views.set_details_row_density("compact"),
            checkable=True,
        ),
        details_density_comfortable=_create_action(
            window,
            "Comfortable Details Rows",
            slot=lambda _checked=False: window._views.set_details_row_density("comfortable"),
            checkable=True,
        ),
        details_next_unreviewed=_create_action(
            window,
            "Next Unreviewed In Details",
            slot=lambda _checked=False: window._views.jump_details_to_review_state("unreviewed"),
        ),
        details_next_kept=_create_action(
            window,
            "Next Kept In Details",
            slot=lambda _checked=False: window._views.jump_details_to_review_state("kept"),
        ),
        details_next_rejected=_create_action(
            window,
            "Next Rejected In Details",
            slot=lambda _checked=False: window._views.jump_details_to_review_state("rejected"),
        ),
        zen_mode=_create_action(
            window,
            "Zen Mode",
            slot=window._zen.handle_zen_mode_toggled,
            checkable=True,
            shortcut="F11",
        ),
    )


def _ai_setup_actions(window: "MainWindow") -> dict[str, QAction]:
    """AI runtime and model installation, repair and diagnostics."""
    return dict(
        install_ai_runtime=_create_action(
            window,
            "Set Up AI Runtime...",
            slot=window._ai_setup.install_ai_runtime,
        ),
        download_ai_model=_create_action(
            window,
            "Download AI Models...",
            slot=window._ai_setup.download_ai_model,
        ),
        repair_ai=_create_action(
            window,
            "Repair AI...",
            slot=window._ai_setup.repair_ai_components,
        ),
        check_ai_readiness=_create_action(
            window,
            "Check AI Readiness...",
            slot=window._ai_setup.check_ai_readiness,
        ),
        copy_ai_diagnostics=_create_action(
            window,
            "Copy AI Diagnostics",
            slot=window._ai_setup.copy_ai_diagnostics,
        ),
        uninstall_ai_components=_create_action(
            window,
            "Uninstall AI Runtime & Models...",
            slot=window._ai_setup.uninstall_ai_components,
        ),
    )


def _ai_workflow_actions(window: "MainWindow") -> dict[str, QAction]:
    """Running, applying and loading AI culling results."""
    return dict(
        guided_ai_cull_preferences=_create_action(
            window,
            "Guided AI Cull...",
            slot=window._ai_run.open_guided_ai_cull_preferences,
            icon=QStyle.StandardPixmap.SP_MediaPlay,
        ),
        open_ai_workflow_center=_create_action(
            window,
            "AI Workflow Center...",
            slot=window._handoff.open_ai_workflow_center,
            shortcut="Ctrl+Shift+W",
        ),
        run_ai_culling=_create_action(
            window,
            "Run Review",
            slot=window._handoff.open_ai_workflow_center,
            icon=QStyle.StandardPixmap.SP_MediaPlay,
        ),
        quick_rerank_ai_culling=_create_action(
            window,
            "Quick Rerank",
            slot=window._ai_run.rerank_ai_pipeline,
            shortcut="Ctrl+Shift+Y",
        ),
        apply_ai_culling=_create_action(
            window,
            "Apply AI Decisions",
            slot=window._ai_run.apply_ai_culling,
        ),
        sort_ai_semantic_folders=_create_action(
            window,
            "Move To AI Category Folders...",
            slot=window._handoff.sort_images_into_semantic_folders,
        ),
        reset_ai_review_cache=_create_action(
            window,
            "Reset AI Review Cache...",
            slot=window._ai_run.reset_ai_review_cache,
        ),
        load_saved_ai=_create_action(window, "Load Saved AI For Folder", slot=window._ai_run.load_hidden_ai_results_for_current_folder),
        load_ai_results=_create_action(window, "Load AI Results...", slot=window._ai_run.choose_ai_results),
        clear_ai_results=_create_action(window, "Clear AI Results", slot=window._ai_run.clear_ai_results),
        open_ai_report=_create_action(window, "Open AI Report", slot=window._ai_run.open_ai_report),
        manage_people=_create_action(window, "People...", slot=window._handoff.open_people_search_dialog),
        show_ai_review_summary=_create_action(window, "Show AI Review Summary", slot=window._ai_run.show_last_ai_review_summary),
        review_ai_adapter_labels=_create_action(
            window,
            "Review Adapter Labels...",
            slot=window._aiculler.review_aiculler_adapter_labels,
        ),
    )


def _ai_review_tool_actions(window: "MainWindow") -> dict[str, QAction]:
    """Jumping between and comparing AI picks."""
    return dict(
        next_ai_pick=_create_action(window, "Next AI Top Pick", slot=window._ai_run.jump_to_next_ai_top_pick, shortcut="Ctrl+Alt+N"),
        next_unreviewed_ai_pick=_create_action(
            window,
            "Next Unreviewed AI Top Pick",
            slot=lambda _checked=False: window._ai_run.jump_to_next_ai_top_pick(unreviewed_only=True),
        ),
        compare_ai_group=_create_action(
            window,
            "Compare Current AI Group",
            slot=window._ai_run.open_current_ai_group_compare,
            shortcut="Ctrl+Alt+G",
        ),
        review_ai_disagreements=_create_action(
            window,
            "Review AI Disagreements",
            slot=window._handoff.review_ai_disagreements,
        ),
        winner_ladder_mode=_create_action(
            window,
            "Winner Ladder",
            slot=window._preview_ctl.open_winner_ladder,
            shortcut="Ctrl+Alt+W",
        ),
    )


def _collection_actions(window: "MainWindow") -> dict[str, QAction]:
    """Virtual collections."""
    return dict(
        create_virtual_collection=_create_action(
            window,
            "Create Collection...",
            slot=window._catalog.create_virtual_collection_from_selection,
        ),
        add_selection_to_collection=_create_action(
            window,
            "Edit Collection Items...",
            slot=window._catalog.add_selection_to_virtual_collection,
        ),
        remove_selection_from_collection=_create_action(
            window,
            "Remove Selection From Collection...",
            slot=window._catalog.remove_selection_from_virtual_collection,
        ),
        delete_virtual_collection=_create_action(
            window,
            "Delete Collection...",
            slot=window._catalog.delete_virtual_collection,
        ),
    )


def _catalog_actions(window: "MainWindow") -> dict[str, QAction]:
    """The cross-folder library catalog."""
    return dict(
        browse_catalog=_create_action(
            window,
            "Browse Library...",
            slot=window._catalog.browse_catalog,
        ),
        add_current_folder_to_catalog=_create_action(
            window,
            "Add Current Folder To Library",
            slot=window._catalog.add_current_folder_to_catalog,
        ),
        add_folder_to_catalog=_create_action(
            window,
            "Add Folder To Library...",
            slot=window._catalog.add_folder_to_catalog_prompt,
        ),
        remove_catalog_folder=_create_action(
            window,
            "Remove Folder From Library...",
            slot=window._catalog.remove_catalog_root_prompt,
        ),
        refresh_catalog=_create_action(
            window,
            "Refresh Library Index",
            slot=window._catalog.refresh_catalog_index,
        ),
        rebuild_folder_catalog_cache=_create_action(
            window,
            "Rebuild Open Folder Cache",
            slot=window._scan.rebuild_current_folder_catalog_cache,
        ),
    )


def _delivery_actions(window: "MainWindow") -> dict[str, QAction]:
    """Sharing and handing images off."""
    return dict(
        share_to_phone=_create_action(
            window,
            "Send to PocketDrop",
            slot=window._handoff.send_selection_to_pocketdrop,
            shortcut="Ctrl+Alt+P",
        ),
        handoff_builder=_create_action(
            window,
            "Deliver / Handoff Builder...",
            slot=window._handoff.open_handoff_builder,
            shortcut="Ctrl+Alt+H",
        ),
        send_to_editor_pipeline=_create_action(
            window,
            "Send To Editor...",
            slot=window._handoff.open_send_to_editor_pipeline,
            shortcut="Ctrl+Alt+E",
        ),
        best_of_set_auto_assembly=_create_action(
            window,
            "Best-of-Set Auto Assembly...",
            slot=window._handoff.open_best_of_set_builder,
            shortcut="Ctrl+Alt+B",
        ),
    )


def _workspace_and_tool_actions(window: "MainWindow") -> dict[str, QAction]:
    """Keyboard shortcuts, workspace presets/toolbar, the command palette and diagnostics."""
    return dict(
        keyboard_shortcuts=_create_action(
            window,
            "Keyboard Shortcuts...",
            slot=window._help_update.open_keyboard_shortcuts_dialog,
        ),
        save_workspace_preset=_create_action(
            window,
            "Save Current Workspace Preset...",
            slot=window._settings_ctl.save_current_workspace_preset,
            shortcut="Ctrl+Alt+S",
        ),
        show_workspace_toolbar=_create_action(
            window,
            "Show Workspace Toolbar",
            slot=window._toolbar.handle_workspace_toolbar_visibility_action,
            checkable=True,
        ),
        open_command_palette=_create_action(
            window,
            "Command Palette...",
            slot=window._command_palette.open,
            auto_repeat=False,
        ),
        performance_logging=_create_action(
            window,
            "Performance Logging",
            slot=window._settings_ctl.handle_performance_logging_toggled,
            checkable=True,
        ),
        open_performance_log_folder=_create_action(
            window,
            "Open Performance Log Folder",
            slot=window._settings_ctl.open_performance_log_folder,
        ),
    )


def _filter_actions(window: "MainWindow") -> dict[str, QAction]:
    """Advanced filters and saved searches."""
    return dict(
        advanced_filters=_create_action(window, "Advanced Filters...", slot=window._records_view.open_advanced_filters_dialog),
        save_filter_preset=_create_action(window, "Save Current Search...", slot=window._records_view.save_current_filter_preset),
        delete_filter_preset=_create_action(window, "Delete Saved Search", slot=window._records_view.delete_current_filter_preset),
        clear_filters=_create_action(window, "Clear Filters", slot=window._records_view.clear_record_filters, shortcut="Ctrl+Shift+X"),
    )


def _help_actions(window: "MainWindow") -> dict[str, QAction]:
    """Help menu entries and the update check."""
    return dict(
        documentation=_create_action(window, "Documentation", slot=window._help_update.show_documentation, shortcut="F1"),
        keyboard_help=_create_action(window, "Quick Help", slot=window._help_update.show_help),
        ai_guide=_create_action(window, "AI Guide", slot=window._help_update.show_ai_guide),
        ai_review_tag_legend=_create_action(window, "AI Review Tag Legend", slot=window._help_update.show_ai_review_tag_legend),
        advanced_help=_create_action(window, "Advanced Help", slot=window._help_update.show_advanced_help),
        check_for_updates=_create_action(window, "Check For Updates...", slot=window._help_update.check_for_updates),
        about=_create_action(window, "About Image Triage", slot=window._help_update.show_about_dialog),
    )


def _appearance_actions(window: "MainWindow") -> dict[AppearanceMode, QAction]:
    """One exclusive, checkable action per appearance profile."""
    group = QActionGroup(window)
    group.setExclusive(True)
    actions: dict[AppearanceMode, QAction] = {}
    for mode in appearance_profile_modes():
        action = _create_action(
            window,
            appearance_mode_label(mode),
            slot=lambda _checked=False, selected=mode: window._appearance.set_appearance_mode(selected),
            checkable=True,
        )
        group.addAction(action)
        actions[mode] = action
    return actions


def _toolbar_placement_actions(window: "MainWindow") -> dict[str, QAction]:
    """Floating (bottom) versus docked (top) toolbar, keyed by placement name."""
    group = QActionGroup(window)
    group.setExclusive(True)
    actions: dict[str, QAction] = {}
    for placement, label in (("floating", "Floating Toolbar (Bottom)"), ("docked", "Docked Toolbar (Top)")):
        action = _create_action(
            window,
            label,
            slot=lambda _checked=False, selected=placement: window._toolbar.set_toolbar_placement(selected),
            checkable=True,
        )
        group.addAction(action)
        actions[placement] = action
    return actions


def _sort_actions(window: "MainWindow") -> dict[SortMode, QAction]:
    """One exclusive, checkable action per sort mode."""
    group = QActionGroup(window)
    group.setExclusive(True)
    actions: dict[SortMode, QAction] = {}
    for mode in SortMode:
        action = _create_action(
            window,
            mode.value,
            slot=lambda _checked=False, selected=mode: window._views.set_sort_mode(selected),
            checkable=True,
        )
        group.addAction(action)
        actions[mode] = action
    return actions


def _quick_filter_actions(window: "MainWindow") -> dict[FilterMode, QAction]:
    """One exclusive, checkable action per quick filter mode."""
    group = QActionGroup(window)
    group.setExclusive(True)
    actions: dict[FilterMode, QAction] = {}
    for mode in FilterMode:
        action = _create_action(
            window,
            mode.value,
            slot=lambda _checked=False, selected=mode: window._records_view.set_filter_mode(selected),
            checkable=True,
        )
        group.addAction(action)
        actions[mode] = action
    return actions


def _column_actions(window: "MainWindow") -> dict[int, QAction]:
    """One exclusive, checkable action per grid column count (1 to 8)."""
    group = QActionGroup(window)
    group.setExclusive(True)
    actions: dict[int, QAction] = {}
    for count in range(1, 9):
        action = _create_action(
            window,
            f"{count} Across",
            slot=lambda _checked=False, selected=count: window._views.set_column_count(selected),
            checkable=True,
        )
        group.addAction(action)
        actions[count] = action
    return actions


# The single-action groups, in the order their QActions are created. QAction
# creation order is parent-child order on the window, so it is part of the
# observable result: keep these in the order the actions have always been built.
_SINGLE_ACTION_GROUPS = (
    _file_actions,
    _edit_and_batch_actions,
    _selection_review_actions,
    _view_toggle_actions,
    _layout_actions,
    _ai_setup_actions,
    _ai_workflow_actions,
    _ai_review_tool_actions,
    _collection_actions,
    _catalog_actions,
    _delivery_actions,
    _workspace_and_tool_actions,
    _filter_actions,
    _help_actions,
)


def _create_single_actions(window: "MainWindow") -> dict[str, QAction]:
    """Every plain (non-dict) ``MainWindowActions`` field, from the groups above.

    The groups must be disjoint: a name supplied twice would silently keep only
    the later action (both would still be created and parented to the window)."""
    singles: dict[str, QAction] = {}
    for create_group in _SINGLE_ACTION_GROUPS:
        group = create_group(window)
        duplicated = sorted(singles.keys() & group.keys())
        if duplicated:
            raise ValueError(f"{create_group.__name__} redefines actions already created: {duplicated}")
        singles.update(group)
    return singles


def build_main_window_actions(window: "MainWindow") -> MainWindowActions:
    # Creation order is observable (see _SINGLE_ACTION_GROUPS): every plain
    # action first, then each exclusive group's QActionGroup followed by its
    # actions, in the order of the dict fields below. ``ai_state_actions`` stays
    # empty here; the records view controller fills it in later.
    singles = _create_single_actions(window)
    return MainWindowActions(
        **singles,
        appearance_actions=_appearance_actions(window),
        toolbar_placement_actions=_toolbar_placement_actions(window),
        sort_actions=_sort_actions(window),
        filter_actions=_quick_filter_actions(window),
        column_actions=_column_actions(window),
    )
