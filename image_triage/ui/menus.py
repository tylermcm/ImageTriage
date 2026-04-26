from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

from .actions import MainWindowActions
from .theme import AppearanceMode


def _add_ai_training_actions(menu: QMenu, actions: MainWindowActions) -> None:
    menu.addAction(actions.open_ai_data_selection)
    menu.addAction(actions.prepare_ai_training_data)
    menu.addAction(actions.build_ai_reference_bank)
    menu.addAction(actions.train_ai_ranker)
    menu.addAction(actions.evaluate_ai_ranker)
    menu.addAction(actions.score_ai_with_trained_ranker)
    menu.addSeparator()
    menu.addAction(actions.clear_ai_trained_model)


def _add_ai_training_management_actions(menu: QMenu, actions: MainWindowActions) -> None:
    menu.addAction(actions.manage_ai_rankers)
    menu.addAction(actions.run_full_ai_training_pipeline)


def _add_selection_actions(menu: QMenu, actions: MainWindowActions) -> None:
    menu.addAction(actions.rename_selection)
    menu.addAction(actions.accept_selection)
    menu.addAction(actions.reject_selection)
    menu.addAction(actions.keep_selection)
    menu.addAction(actions.move_selection)
    menu.addAction(actions.move_selection_to_new_folder)
    menu.addAction(actions.delete_selection)
    menu.addAction(actions.restore_selection)


def _add_workspace_presets_menu(
    menu: QMenu,
    actions: MainWindowActions,
    workspace_preset_menu: QMenu | None,
) -> None:
    if workspace_preset_menu is None:
        return
    menu.addMenu(workspace_preset_menu)
    menu.addAction(actions.save_workspace_preset)


def build_main_menu_bar(
    window,
    actions: MainWindowActions,
    dock_actions: Mapping[str, QAction] | None = None,
    *,
    workflow_recipe_menu: QMenu | None = None,
    workspace_preset_menu: QMenu | None = None,
    collections_menu: QMenu | None = None,
    catalog_menu: QMenu | None = None,
) -> None:
    menu_bar = window.menuBar()
    menu_bar.clear()

    file_menu = menu_bar.addMenu("&File")
    file_menu.addAction(actions.open_folder)
    file_menu.addAction(actions.refresh_folder)
    file_menu.addAction(actions.new_folder)
    file_menu.addAction(actions.empty_recycle_bin)
    file_menu.addSeparator()
    file_menu.addAction(actions.exit_app)

    edit_menu = menu_bar.addMenu("&Edit")
    edit_menu.addAction(actions.undo)
    edit_menu.addSeparator()
    _add_selection_actions(edit_menu, actions)

    view_menu = menu_bar.addMenu("&View")
    appearance_menu = view_menu.addMenu("Appearance")
    for mode in (AppearanceMode.DARK, AppearanceMode.MIDNIGHT, AppearanceMode.LIGHT, AppearanceMode.AUTO):
        appearance_menu.addAction(actions.appearance_actions[mode])

    sort_menu = view_menu.addMenu("Sort")
    for action in actions.sort_actions.values():
        sort_menu.addAction(action)

    filter_menu = view_menu.addMenu("Filter")
    for action in actions.filter_actions.values():
        filter_menu.addAction(action)
    filter_menu.addSeparator()
    filter_menu.addAction(actions.advanced_filters)
    filter_menu.addAction(actions.save_filter_preset)
    filter_menu.addAction(actions.delete_filter_preset)
    filter_menu.addAction(actions.clear_filters)

    columns_menu = view_menu.addMenu("Columns")
    for count in range(1, 9):
        columns_menu.addAction(actions.column_actions[count])

    view_menu.addSeparator()
    view_menu.addAction(actions.burst_groups)
    view_menu.addAction(actions.burst_stacks)
    view_menu.addAction(actions.compact_cards)
    view_menu.addAction(actions.compare_mode)
    view_menu.addAction(actions.auto_advance)
    mode_menu = view_menu.addMenu("Workspace Mode")
    mode_menu.addAction(actions.manual_mode)
    mode_menu.addAction(actions.ai_mode)

    review_menu = menu_bar.addMenu("&Review")
    review_menu.addAction(actions.open_preview)
    review_menu.addAction(actions.winner_ladder_mode)
    review_menu.addSeparator()
    _add_selection_actions(review_menu, actions)
    review_menu.addSeparator()
    rounds_menu = review_menu.addMenu("Review Rounds")
    rounds_menu.addAction(actions.assign_review_round_first_pass)
    rounds_menu.addAction(actions.assign_review_round_second_pass)
    rounds_menu.addAction(actions.assign_review_round_third_pass)
    rounds_menu.addAction(actions.assign_review_round_hero)
    rounds_menu.addSeparator()
    rounds_menu.addAction(actions.clear_review_round)
    review_menu.addSeparator()
    review_menu.addAction(actions.reveal_in_explorer)
    review_menu.addAction(actions.open_in_photoshop)

    library_menu = menu_bar.addMenu("&Library")
    collections_section = library_menu.addMenu("Collections")
    collections_section.addAction(actions.create_virtual_collection)
    collections_section.addAction(actions.add_selection_to_collection)
    collections_section.addAction(actions.remove_selection_from_collection)
    collections_section.addAction(actions.delete_virtual_collection)
    if collections_menu is not None:
        collections_section.addSeparator()
        collections_section.addMenu(collections_menu)

    catalog_section = library_menu.addMenu("Catalog")
    catalog_section.addAction(actions.browse_catalog)
    catalog_section.addAction(actions.add_current_folder_to_catalog)
    catalog_section.addAction(actions.add_folder_to_catalog)
    catalog_section.addAction(actions.remove_catalog_folder)
    catalog_section.addSeparator()
    catalog_section.addAction(actions.refresh_catalog)
    catalog_section.addAction(actions.rebuild_folder_catalog_cache)
    if catalog_menu is not None:
        catalog_section.addSeparator()
        catalog_section.addMenu(catalog_menu)

    workflow_menu = menu_bar.addMenu("&Workflow")
    workflow_menu.addAction(actions.handoff_builder)
    workflow_menu.addAction(actions.send_to_editor_pipeline)
    workflow_menu.addAction(actions.best_of_set_auto_assembly)
    if workflow_recipe_menu is not None:
        workflow_menu.addMenu(workflow_recipe_menu)

    ai_menu = menu_bar.addMenu("&AI")
    ai_menu.addAction(actions.download_ai_model)
    ai_menu.addSeparator()
    ai_menu.addAction(actions.run_ai_culling)
    ai_menu.addAction(actions.load_saved_ai)
    ai_menu.addAction(actions.load_ai_results)
    ai_menu.addAction(actions.clear_ai_results)
    ai_menu.addAction(actions.open_ai_report)
    ai_menu.addSeparator()
    ai_menu.addAction(actions.next_ai_pick)
    ai_menu.addAction(actions.next_unreviewed_ai_pick)
    ai_menu.addAction(actions.compare_ai_group)
    ai_menu.addAction(actions.review_ai_disagreements)
    ai_menu.addAction(actions.taste_calibration_wizard)
    ai_menu.addSeparator()
    training_menu = ai_menu.addMenu("Training")
    _add_ai_training_actions(training_menu, actions)
    _add_ai_training_management_actions(ai_menu, actions)

    tools_menu = menu_bar.addMenu("&Tools")
    tools_menu.addAction(actions.open_command_palette)
    tools_menu.addSeparator()
    tools_menu.addAction(actions.batch_rename_selection)
    tools_menu.addAction(actions.batch_resize_selection)
    tools_menu.addAction(actions.batch_convert_selection)
    tools_menu.addSeparator()
    tools_menu.addAction(actions.extract_archive)

    settings_menu = menu_bar.addMenu("&Settings")
    settings_menu.addAction(actions.workflow_settings)
    settings_menu.addAction(actions.file_associations)
    settings_menu.addAction(actions.keyboard_shortcuts)
    settings_menu.addSeparator()
    settings_menu.addAction(actions.customize_workspace_toolbar)
    _add_workspace_presets_menu(settings_menu, actions, workspace_preset_menu)

    window_menu = menu_bar.addMenu("&Window")
    if dock_actions:
        for key in ("library", "inspector"):
            action = dock_actions.get(key)
            if action is not None:
                window_menu.addAction(action)
        window_menu.addSeparator()
    window_menu.addAction(actions.reset_layout)

    help_menu = menu_bar.addMenu("&Help")
    help_menu.addAction(actions.keyboard_help)
    help_menu.addAction(actions.ai_guide)
    help_menu.addAction(actions.advanced_help)
    help_menu.addSeparator()
    help_menu.addAction(actions.about)
