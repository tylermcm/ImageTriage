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
    load_shortcut_overrides,
    save_shortcut_overrides,
)
from .theme import AppearanceMode

if TYPE_CHECKING:
    from ..window import MainWindow


__all__ = (
    "MainWindowActions",
    "SHORTCUT_REGISTRY",
    "apply_shortcut_overrides",
    "build_main_window_actions",
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
    compact_cards: QAction
    show_hidden_folders: QAction
    grid_view: QAction
    details_view: QAction
    details_preview_pane: QAction
    details_preview_on_hover: QAction
    details_density_compact: QAction
    details_density_comfortable: QAction
    details_next_unreviewed: QAction
    details_next_kept: QAction
    details_next_rejected: QAction
    zen_mode: QAction
    manual_mode: QAction
    ai_mode: QAction
    install_ai_runtime: QAction
    download_ai_model: QAction
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
    show_ai_review_summary: QAction
    review_ai_adapter_labels: QAction
    open_ai_data_selection: QAction
    train_ai_ranker: QAction
    train_ai_ranker_from_global: QAction
    evaluate_ai_ranker: QAction
    score_ai_with_trained_ranker: QAction
    next_ai_pick: QAction
    next_unreviewed_ai_pick: QAction
    compare_ai_group: QAction
    dispute_current_ai_result: QAction
    review_ai_disagreements: QAction
    winner_ladder_mode: QAction
    assign_review_round_first_pass: QAction
    assign_review_round_second_pass: QAction
    assign_review_round_third_pass: QAction
    assign_review_round_hero: QAction
    clear_review_round: QAction
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
    handoff_builder: QAction
    send_to_editor_pipeline: QAction
    best_of_set_auto_assembly: QAction
    keyboard_shortcuts: QAction
    save_workspace_preset: QAction
    customize_workspace_toolbar: QAction
    show_workspace_toolbar: QAction
    open_command_palette: QAction
    performance_logging: QAction
    open_performance_log_folder: QAction
    advanced_filters: QAction
    save_filter_preset: QAction
    delete_filter_preset: QAction
    clear_filters: QAction
    keyboard_help: QAction
    ai_guide: QAction
    ai_review_tag_legend: QAction
    advanced_help: QAction
    check_for_updates: QAction
    about: QAction
    appearance_actions: dict[AppearanceMode, QAction] = field(default_factory=dict)
    sort_actions: dict[SortMode, QAction] = field(default_factory=dict)
    filter_actions: dict[FilterMode, QAction] = field(default_factory=dict)
    ai_state_actions: dict[AIStateFilter, QAction] = field(default_factory=dict)
    column_actions: dict[int, QAction] = field(default_factory=dict)
    mode_actions: dict[str, QAction] = field(default_factory=dict)


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
    if shortcut is not None:
        action.setShortcut(shortcut)
    shortcut_text = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
    hinted_text = f"{text} ({shortcut_text})" if shortcut_text else text
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


def build_main_window_actions(window: "MainWindow") -> MainWindowActions:
    actions = MainWindowActions(
        open_folder=_create_action(
            window,
            "Open Folder...",
            slot=window._choose_folder,
            icon=QStyle.StandardPixmap.SP_DialogOpenButton,
            shortcut=QKeySequence.StandardKey.Open,
        ),
        refresh_folder=_create_action(
            window,
            "Refresh Folder",
            slot=window._refresh_folder,
            icon=QStyle.StandardPixmap.SP_BrowserReload,
            shortcut=QKeySequence.StandardKey.Refresh,
        ),
        empty_recycle_bin=_create_action(window, "Empty Recycle Bin", slot=window._empty_recycle_bin),
        new_folder=_create_action(window, "New Folder...", slot=window._create_folder_in_current_folder, shortcut="Ctrl+Shift+N"),
        workflow_settings=_create_action(
            window,
            "Settings...",
            slot=window._show_settings,
            shortcut="Ctrl+,",
        ),
        file_associations=_create_action(
            window,
            "File Associations...",
            slot=window._open_file_associations_dialog,
        ),
        reset_layout=_create_action(window, "Reset Window Layout", slot=window._reset_window_layout),
        exit_app=_create_action(window, "Exit", slot=window.close),
        undo=_create_action(
            window,
            "Undo",
            slot=window._undo_last_action,
            shortcut=QKeySequence.StandardKey.Undo,
        ),
        rename_selection=_create_action(window, "Rename Image...", slot=window._rename_selected_record, shortcut="F2"),
        batch_rename_selection=_create_action(
            window,
            "Batch Rename...",
            slot=window._start_batch_rename_tool_mode,
            shortcut="Ctrl+Shift+R",
        ),
        batch_resize_selection=_create_action(
            window,
            "Batch Resize...",
            slot=window._start_batch_resize_tool_mode,
            shortcut="Ctrl+Shift+E",
        ),
        batch_convert_selection=_create_action(
            window,
            "Batch Convert...",
            slot=window._start_batch_convert_tool_mode,
            shortcut="Ctrl+Shift+C",
        ),
        extract_archive=_create_action(window, "Extract Archive...", slot=window._extract_archive_prompt),
        accept_selection=_create_action(window, "Accept Selection", slot=window._accept_selected_records),
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
        open_preview=_create_action(window, "Open Preview", slot=window._open_current_preview),
        reveal_in_explorer=_create_action(window, "Reveal In File Explorer", slot=window._reveal_current_selection),
        open_in_photoshop=_create_action(window, "Open In Photoshop", slot=window._open_selected_in_photoshop),
        compare_mode=_create_action(
            window,
            "Compare",
            slot=window._handle_compare_toggled,
            checkable=True,
            shortcut="C",
        ),
        auto_advance=_create_action(
            window,
            "Auto-Advance",
            slot=window._handle_auto_advance_toggled,
            checkable=True,
        ),
        burst_groups=_create_action(
            window,
            "Smart Groups",
            slot=window._handle_burst_groups_toggled,
            checkable=True,
        ),
        burst_stacks=_create_action(
            window,
            "Smart Stacks",
            slot=window._handle_burst_stacks_toggled,
            checkable=True,
        ),
        compact_cards=_create_action(
            window,
            "Compact Cards",
            slot=window._handle_compact_cards_toggled,
            checkable=True,
        ),
        show_hidden_folders=_create_action(
            window,
            "Show Hidden Folders",
            slot=window._handle_show_hidden_folders_toggled,
            checkable=True,
        ),
        grid_view=_create_action(
            window,
            "Grid View",
            slot=lambda _checked=False: window._set_browser_view_mode("grid"),
            checkable=True,
            shortcut="Ctrl+1",
        ),
        details_view=_create_action(
            window,
            "Details View",
            slot=lambda _checked=False: window._set_browser_view_mode("details"),
            checkable=True,
            shortcut="Ctrl+2",
        ),
        details_preview_pane=_create_action(
            window,
            "Details Preview Pane",
            slot=window._handle_details_preview_toggled,
            checkable=True,
        ),
        details_preview_on_hover=_create_action(
            window,
            "Details Preview On Hover",
            slot=window._handle_details_preview_on_hover_toggled,
            checkable=True,
        ),
        details_density_compact=_create_action(
            window,
            "Compact Details Rows",
            slot=lambda _checked=False: window._set_details_row_density("compact"),
            checkable=True,
        ),
        details_density_comfortable=_create_action(
            window,
            "Comfortable Details Rows",
            slot=lambda _checked=False: window._set_details_row_density("comfortable"),
            checkable=True,
        ),
        details_next_unreviewed=_create_action(
            window,
            "Next Unreviewed In Details",
            slot=lambda _checked=False: window._jump_details_to_review_state("unreviewed"),
        ),
        details_next_kept=_create_action(
            window,
            "Next Kept In Details",
            slot=lambda _checked=False: window._jump_details_to_review_state("kept"),
        ),
        details_next_rejected=_create_action(
            window,
            "Next Rejected In Details",
            slot=lambda _checked=False: window._jump_details_to_review_state("rejected"),
        ),
        zen_mode=_create_action(
            window,
            "Zen Mode",
            slot=window._handle_zen_mode_toggled,
            checkable=True,
            shortcut="F11",
        ),
        manual_mode=_create_action(window, "Manual Review", slot=lambda _checked=False: window._set_ui_mode("manual"), checkable=True),
        ai_mode=_create_action(window, "AI Review", slot=lambda _checked=False: window._set_ui_mode("ai"), checkable=True),
        install_ai_runtime=_create_action(
            window,
            "Install AI Runtime...",
            slot=window._install_ai_runtime,
        ),
        download_ai_model=_create_action(
            window,
            "Download AI Models...",
            slot=window._download_ai_model,
        ),
        open_ai_workflow_center=_create_action(
            window,
            "AI Workflow Center...",
            slot=window._open_ai_workflow_center,
            shortcut="Ctrl+Shift+W",
        ),
        run_ai_culling=_create_action(
            window,
            "Open AI Workflow Center",
            slot=window._open_ai_workflow_center,
            icon=QStyle.StandardPixmap.SP_MediaPlay,
        ),
        quick_rerank_ai_culling=_create_action(
            window,
            "Quick Rerank",
            slot=window._rerank_ai_pipeline,
            shortcut="Ctrl+Shift+Y",
        ),
        apply_ai_culling=_create_action(
            window,
            "Apply AI Decisions",
            slot=window._apply_ai_culling,
        ),
        sort_ai_semantic_folders=_create_action(
            window,
            "Move To AI Category Folders...",
            slot=window._sort_images_into_semantic_folders,
        ),
        reset_ai_review_cache=_create_action(
            window,
            "Reset AI Review Cache...",
            slot=window._reset_ai_review_cache,
        ),
        load_saved_ai=_create_action(window, "Load Saved AI For Folder", slot=window._load_hidden_ai_results_for_current_folder),
        load_ai_results=_create_action(window, "Load AI Results...", slot=window._choose_ai_results),
        clear_ai_results=_create_action(window, "Clear AI Results", slot=window._clear_ai_results),
        open_ai_report=_create_action(window, "Open AI Report", slot=window._open_ai_report),
        show_ai_review_summary=_create_action(window, "Show AI Review Summary", slot=window._show_last_ai_review_summary),
        review_ai_adapter_labels=_create_action(
            window,
            "Review Adapter Labels...",
            slot=window._review_aiculler_adapter_labels,
        ),
        open_ai_data_selection=_create_action(
            window,
            "Prepare Rating CSV",
            slot=window._export_aiculler_ratings,
            shortcut="Ctrl+Shift+L",
        ),
        train_ai_ranker=_create_action(
            window,
            "Train Adapter...",
            slot=window._train_aiculler_adapter,
            shortcut="Ctrl+Shift+T",
        ),
        train_ai_ranker_from_global=_create_action(
            window,
            "Train Global Adapter...",
            slot=window._train_aiculler_adapter_from_global_labels,
        ),
        evaluate_ai_ranker=_create_action(window, "Evaluate Adapter", slot=window._evaluate_aiculler_adapter),
        score_ai_with_trained_ranker=_create_action(
            window,
            "Rank Folder With Local Adapter",
            slot=window._rank_aiculler_adapter,
        ),
        next_ai_pick=_create_action(window, "Next AI Top Pick", slot=window._jump_to_next_ai_top_pick, shortcut="Ctrl+Alt+P"),
        next_unreviewed_ai_pick=_create_action(
            window,
            "Next Unreviewed AI Top Pick",
            slot=lambda _checked=False: window._jump_to_next_ai_top_pick(unreviewed_only=True),
        ),
        compare_ai_group=_create_action(
            window,
            "Compare Current AI Group",
            slot=window._open_current_ai_group_compare,
            shortcut="Ctrl+Alt+G",
        ),
        dispute_current_ai_result=_create_action(
            window,
            "Dispute Current AI Decision...",
            slot=window._dispute_current_ai_result,
        ),
        review_ai_disagreements=_create_action(
            window,
            "Review AI Disagreements",
            slot=window._review_ai_disagreements,
            shortcut="Ctrl+Alt+D",
        ),
        winner_ladder_mode=_create_action(
            window,
            "Winner Ladder",
            slot=window._open_winner_ladder,
            shortcut="Ctrl+Alt+W",
        ),
        assign_review_round_first_pass=_create_action(
            window,
            "Mark As First-Pass Reject",
            slot=lambda _checked=False: window._assign_review_round_to_selection("first_pass_rejects"),
            shortcut="Alt+1",
        ),
        assign_review_round_second_pass=_create_action(
            window,
            "Mark As Keeper Candidate",
            slot=lambda _checked=False: window._assign_review_round_to_selection("second_pass_keepers"),
            shortcut="Alt+2",
        ),
        assign_review_round_third_pass=_create_action(
            window,
            "Mark As Finalist",
            slot=lambda _checked=False: window._assign_review_round_to_selection("third_pass_finalists"),
            shortcut="Alt+3",
        ),
        assign_review_round_hero=_create_action(
            window,
            "Mark As Hero Select",
            slot=lambda _checked=False: window._assign_review_round_to_selection("final_hero_selects"),
            shortcut="Alt+4",
        ),
        clear_review_round=_create_action(
            window,
            "Clear Review Stage",
            slot=lambda _checked=False: window._assign_review_round_to_selection(""),
            shortcut="Alt+0",
        ),
        create_virtual_collection=_create_action(
            window,
            "Create Collection From Selection...",
            slot=window._create_virtual_collection_from_selection,
        ),
        add_selection_to_collection=_create_action(
            window,
            "Add Selection To Collection...",
            slot=window._add_selection_to_virtual_collection,
        ),
        remove_selection_from_collection=_create_action(
            window,
            "Remove Selection From Collection...",
            slot=window._remove_selection_from_virtual_collection,
        ),
        delete_virtual_collection=_create_action(
            window,
            "Delete Collection...",
            slot=window._delete_virtual_collection,
        ),
        browse_catalog=_create_action(
            window,
            "Browse Global Catalog...",
            slot=window._browse_catalog,
        ),
        add_current_folder_to_catalog=_create_action(
            window,
            "Add Current Folder To Catalog",
            slot=window._add_current_folder_to_catalog,
        ),
        add_folder_to_catalog=_create_action(
            window,
            "Add Folder To Catalog...",
            slot=window._add_folder_to_catalog_prompt,
        ),
        remove_catalog_folder=_create_action(
            window,
            "Remove Catalog Folder...",
            slot=window._remove_catalog_root_prompt,
        ),
        refresh_catalog=_create_action(
            window,
            "Refresh Catalog Index",
            slot=window._refresh_catalog_index,
        ),
        rebuild_folder_catalog_cache=_create_action(
            window,
            "Rebuild Open Folder Cache",
            slot=window._rebuild_current_folder_catalog_cache,
        ),
        handoff_builder=_create_action(
            window,
            "Deliver / Handoff Builder...",
            slot=window._open_handoff_builder,
            shortcut="Ctrl+Alt+H",
        ),
        send_to_editor_pipeline=_create_action(
            window,
            "Send To Editor...",
            slot=window._open_send_to_editor_pipeline,
            shortcut="Ctrl+Alt+E",
        ),
        best_of_set_auto_assembly=_create_action(
            window,
            "Best-of-Set Auto Assembly...",
            slot=window._open_best_of_set_builder,
            shortcut="Ctrl+Alt+B",
        ),
        keyboard_shortcuts=_create_action(
            window,
            "Keyboard Shortcuts...",
            slot=window._open_keyboard_shortcuts_dialog,
        ),
        save_workspace_preset=_create_action(
            window,
            "Save Current Workspace Preset...",
            slot=window._save_current_workspace_preset,
            shortcut="Ctrl+Alt+S",
        ),
        customize_workspace_toolbar=_create_action(
            window,
            "Customize Toolbars...",
            slot=window._enter_toolbar_edit_mode,
        ),
        show_workspace_toolbar=_create_action(
            window,
            "Show Workspace Toolbar",
            slot=window._handle_workspace_toolbar_visibility_action,
            checkable=True,
        ),
        open_command_palette=_create_action(
            window,
            "Command Palette...",
            slot=window._open_command_palette,
            auto_repeat=False,
        ),
        performance_logging=_create_action(
            window,
            "Performance Logging",
            slot=window._handle_performance_logging_toggled,
            checkable=True,
        ),
        open_performance_log_folder=_create_action(
            window,
            "Open Performance Log Folder",
            slot=window._open_performance_log_folder,
        ),
        advanced_filters=_create_action(window, "Advanced Filters...", slot=window._open_advanced_filters_dialog),
        save_filter_preset=_create_action(window, "Save Current Search...", slot=window._save_current_filter_preset),
        delete_filter_preset=_create_action(window, "Delete Saved Search", slot=window._delete_current_filter_preset),
        clear_filters=_create_action(window, "Clear Filters", slot=window._clear_record_filters, shortcut="Ctrl+Shift+X"),
        keyboard_help=_create_action(window, "Quick Help", slot=window._show_help),
        ai_guide=_create_action(window, "AI Guide", slot=window._show_ai_guide),
        ai_review_tag_legend=_create_action(window, "AI Review Tag Legend", slot=window._show_ai_review_tag_legend),
        advanced_help=_create_action(window, "Advanced Help", slot=window._show_advanced_help),
        check_for_updates=_create_action(window, "Check For Updates...", slot=window._check_for_updates),
        about=_create_action(window, "About Image Triage", slot=window._show_about_dialog),
    )

    appearance_group = QActionGroup(window)
    appearance_group.setExclusive(True)
    for mode, label in (
        (AppearanceMode.DARK, "Dark"),
        (AppearanceMode.MIDNIGHT, "Midnight"),
        (AppearanceMode.LIGHT, "Light"),
        (AppearanceMode.AUTO, "Auto"),
    ):
        action = _create_action(
            window,
            label,
            slot=lambda _checked=False, selected=mode: window._set_appearance_mode(selected),
            checkable=True,
        )
        appearance_group.addAction(action)
        actions.appearance_actions[mode] = action

    mode_group = QActionGroup(window)
    mode_group.setExclusive(True)
    mode_group.addAction(actions.manual_mode)
    mode_group.addAction(actions.ai_mode)
    actions.mode_actions = {"manual": actions.manual_mode, "ai": actions.ai_mode}

    sort_group = QActionGroup(window)
    sort_group.setExclusive(True)
    for mode in SortMode:
        action = _create_action(
            window,
            mode.value,
            slot=lambda _checked=False, selected=mode: window._set_sort_mode(selected),
            checkable=True,
        )
        sort_group.addAction(action)
        actions.sort_actions[mode] = action

    filter_group = QActionGroup(window)
    filter_group.setExclusive(True)
    for mode in FilterMode:
        action = _create_action(
            window,
            mode.value,
            slot=lambda _checked=False, selected=mode: window._set_filter_mode(selected),
            checkable=True,
        )
        filter_group.addAction(action)
        actions.filter_actions[mode] = action

    column_group = QActionGroup(window)
    column_group.setExclusive(True)
    for count in range(1, 9):
        action = _create_action(
            window,
            f"{count} Across",
            slot=lambda _checked=False, selected=count: window._set_column_count(selected),
            checkable=True,
        )
        column_group.addAction(action)
        actions.column_actions[count] = action

    actions.dispute_current_ai_result.setToolTip(
        "Dispute the selected AI result and choose the correct 1-5 label. "
        "Keyboard path in AI Review: press D, then 1-5."
    )
    actions.dispute_current_ai_result.setStatusTip(actions.dispute_current_ai_result.toolTip())

    return actions
