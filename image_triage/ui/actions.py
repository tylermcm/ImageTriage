from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QStyle

from ..models import FilterMode, SortMode
from .theme import AppearanceMode

if TYPE_CHECKING:
    from ..window import MainWindow


@dataclass(slots=True)
class MainWindowActions:
    open_folder: QAction
    refresh_folder: QAction
    empty_recycle_bin: QAction
    new_folder: QAction
    workflow_settings: QAction
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
    manual_mode: QAction
    ai_mode: QAction
    run_ai_culling: QAction
    load_saved_ai: QAction
    load_ai_results: QAction
    clear_ai_results: QAction
    open_ai_report: QAction
    prepare_ai_training_data: QAction
    open_ai_data_selection: QAction
    run_full_ai_training_pipeline: QAction
    train_ai_ranker: QAction
    manage_ai_rankers: QAction
    evaluate_ai_ranker: QAction
    score_ai_with_trained_ranker: QAction
    build_ai_reference_bank: QAction
    clear_ai_trained_model: QAction
    next_ai_pick: QAction
    next_unreviewed_ai_pick: QAction
    compare_ai_group: QAction
    review_ai_disagreements: QAction
    taste_calibration_wizard: QAction
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
    handoff_builder: QAction
    send_to_editor_pipeline: QAction
    best_of_set_auto_assembly: QAction
    keyboard_shortcuts: QAction
    save_workspace_preset: QAction
    open_command_palette: QAction
    advanced_filters: QAction
    save_filter_preset: QAction
    delete_filter_preset: QAction
    clear_filters: QAction
    keyboard_help: QAction
    ai_guide: QAction
    advanced_help: QAction
    about: QAction
    appearance_actions: dict[AppearanceMode, QAction] = field(default_factory=dict)
    sort_actions: dict[SortMode, QAction] = field(default_factory=dict)
    filter_actions: dict[FilterMode, QAction] = field(default_factory=dict)
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
    resolved_icon = window.style().standardIcon(icon) if icon is not None else None
    action = QAction(resolved_icon, text, window) if resolved_icon is not None else QAction(text, window)
    action.setToolTip(text)
    action.setStatusTip(text)
    action.setAutoRepeat(auto_repeat)
    if shortcut is not None:
        action.setShortcut(shortcut)
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
            "Workflow Settings...",
            slot=window._show_settings,
            shortcut="Ctrl+,",
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
        manual_mode=_create_action(window, "Manual Review", slot=lambda _checked=False: window._set_ui_mode("manual"), checkable=True),
        ai_mode=_create_action(window, "AI Review", slot=lambda _checked=False: window._set_ui_mode("ai"), checkable=True),
        run_ai_culling=_create_action(
            window,
            "Run AI Culling",
            slot=window._run_ai_pipeline,
            icon=QStyle.StandardPixmap.SP_MediaPlay,
        ),
        load_saved_ai=_create_action(window, "Load Saved AI For Folder", slot=window._load_hidden_ai_results_for_current_folder),
        load_ai_results=_create_action(window, "Load AI Results...", slot=window._choose_ai_results),
        clear_ai_results=_create_action(window, "Clear AI Results", slot=window._clear_ai_results),
        open_ai_report=_create_action(window, "Open AI Report", slot=window._open_ai_report),
        prepare_ai_training_data=_create_action(window, "Prepare Training Data", slot=window._prepare_ai_training_data),
        open_ai_data_selection=_create_action(
            window,
            "Collect Training Labels...",
            slot=window._open_ai_data_selection,
            shortcut="Ctrl+Shift+L",
        ),
        run_full_ai_training_pipeline=_create_action(
            window,
            "Run Full Training Pipeline...",
            slot=window._run_full_ai_training_pipeline,
            shortcut="Ctrl+Shift+Y",
        ),
        train_ai_ranker=_create_action(
            window,
            "Train Ranker...",
            slot=window._train_ai_ranker,
            shortcut="Ctrl+Shift+T",
        ),
        manage_ai_rankers=_create_action(window, "Ranker Center...", slot=window._manage_ai_rankers),
        evaluate_ai_ranker=_create_action(window, "Evaluate Trained Ranker", slot=window._evaluate_ai_ranker),
        score_ai_with_trained_ranker=_create_action(
            window,
            "Score Current Folder With Active Ranker",
            slot=window._score_current_folder_with_trained_ranker,
        ),
        build_ai_reference_bank=_create_action(window, "Build Reference Bank...", slot=window._build_ai_reference_bank),
        clear_ai_trained_model=_create_action(window, "Clear Trained Model...", slot=window._clear_ai_trained_model),
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
        review_ai_disagreements=_create_action(
            window,
            "Review AI Disagreements",
            slot=window._review_ai_disagreements,
            shortcut="Ctrl+Alt+D",
        ),
        taste_calibration_wizard=_create_action(
            window,
            "Taste Calibration Wizard...",
            slot=window._open_taste_calibration_wizard,
            shortcut="Ctrl+Alt+K",
        ),
        winner_ladder_mode=_create_action(
            window,
            "Winner Ladder",
            slot=window._open_winner_ladder,
            shortcut="Ctrl+Alt+W",
        ),
        assign_review_round_first_pass=_create_action(
            window,
            "Mark First Pass Rejects",
            slot=lambda _checked=False: window._assign_review_round_to_selection("first_pass_rejects"),
            shortcut="Alt+1",
        ),
        assign_review_round_second_pass=_create_action(
            window,
            "Mark Second Pass Keepers",
            slot=lambda _checked=False: window._assign_review_round_to_selection("second_pass_keepers"),
            shortcut="Alt+2",
        ),
        assign_review_round_third_pass=_create_action(
            window,
            "Mark Third Pass Finalists",
            slot=lambda _checked=False: window._assign_review_round_to_selection("third_pass_finalists"),
            shortcut="Alt+3",
        ),
        assign_review_round_hero=_create_action(
            window,
            "Mark Final Hero Selects",
            slot=lambda _checked=False: window._assign_review_round_to_selection("final_hero_selects"),
            shortcut="Alt+4",
        ),
        clear_review_round=_create_action(
            window,
            "Clear Review Round",
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
        open_command_palette=_create_action(
            window,
            "Command Palette...",
            slot=window._open_command_palette,
            auto_repeat=False,
        ),
        advanced_filters=_create_action(window, "Advanced Filters...", slot=window._open_advanced_filters_dialog),
        save_filter_preset=_create_action(window, "Save Current Search...", slot=window._save_current_filter_preset),
        delete_filter_preset=_create_action(window, "Delete Saved Search", slot=window._delete_current_filter_preset),
        clear_filters=_create_action(window, "Clear Filters", slot=window._clear_record_filters, shortcut="Ctrl+Shift+X"),
        keyboard_help=_create_action(window, "Quick Help", slot=window._show_help),
        ai_guide=_create_action(window, "AI Guide", slot=window._show_ai_guide),
        advanced_help=_create_action(window, "Advanced Help", slot=window._show_advanced_help),
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

    return actions
