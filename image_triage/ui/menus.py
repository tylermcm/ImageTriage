from __future__ import annotations

from collections.abc import Mapping

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu

from .actions import MainWindowActions
from .theme import AppearanceMode


def _add_ai_training_actions(menu: QMenu, actions: MainWindowActions) -> None:
    menu.addAction(actions.review_ai_adapter_labels)
    menu.addAction(actions.open_ai_data_selection)
    menu.addAction(actions.train_ai_ranker)
    menu.addAction(actions.train_ai_ranker_from_global)
    menu.addAction(actions.evaluate_ai_ranker)
    menu.addAction(actions.score_ai_with_trained_ranker)


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


def _add_panel_layout_menu(menu: QMenu, window, panel_key: str, title: str) -> None:
    docks = getattr(window, "workspace_docks", None)
    if docks is None:
        return
    panel_menu = menu.addMenu(title)
    panel_menu.addAction("Show Expanded", lambda _checked=False, key=panel_key: docks.expand_panel(key))
    panel_menu.addAction("Collapse To Tab", lambda _checked=False, key=panel_key: docks.collapse_panel(key))
    panel_menu.addAction("Hide", lambda _checked=False, key=panel_key: docks.hide_panel(key))
    panel_menu.addSeparator()
    panel_menu.addAction("Dock Left", lambda _checked=False, key=panel_key: docks.dock_to_side(key, "left", show_after=True))
    panel_menu.addAction("Dock Right", lambda _checked=False, key=panel_key: docks.dock_to_side(key, "right", show_after=True))
    panel_menu.addAction("Pop Out", lambda _checked=False, key=panel_key: docks.pop_out_panel(key))


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

    layout_menu = view_menu.addMenu("Layout")
    columns_menu = layout_menu.addMenu("Columns")
    for count in range(1, 9):
        columns_menu.addAction(actions.column_actions[count])
    layout_menu.addAction(actions.compact_cards)
    layout_menu.addAction(actions.show_hidden_folders)
    layout_menu.addSeparator()
    layout_menu.addAction(actions.grid_view)
    layout_menu.addAction(actions.details_view)
    layout_menu.addAction(actions.details_preview_pane)
    layout_menu.addAction(actions.details_preview_on_hover)
    details_density_menu = layout_menu.addMenu("Details Row Density")
    details_density_menu.addAction(actions.details_density_comfortable)
    details_density_menu.addAction(actions.details_density_compact)
    details_navigation_menu = layout_menu.addMenu("Details Navigation")
    details_navigation_menu.addAction(actions.details_next_unreviewed)
    details_navigation_menu.addAction(actions.details_next_kept)
    details_navigation_menu.addAction(actions.details_next_rejected)
    layout_menu.addSeparator()
    layout_menu.addAction(actions.zen_mode)
    layout_menu.addSeparator()
    layout_menu.addAction(actions.show_workspace_toolbar)

    sort_menu = view_menu.addMenu("Sort")
    for action in actions.sort_actions.values():
        sort_menu.addAction(action)

    filter_menu = view_menu.addMenu("Filters")
    for action in actions.filter_actions.values():
        filter_menu.addAction(action)
    filter_menu.addSeparator()
    filter_menu.addAction(actions.advanced_filters)
    filter_menu.addAction(actions.save_filter_preset)
    filter_menu.addAction(actions.delete_filter_preset)
    filter_menu.addAction(actions.clear_filters)

    review_view_menu = view_menu.addMenu("Review View")
    review_view_menu.addAction(actions.burst_groups)
    review_view_menu.addAction(actions.burst_stacks)
    review_view_menu.addAction(actions.compare_mode)
    review_view_menu.addAction(actions.auto_advance)

    mode_menu = view_menu.addMenu("Mode")
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
    ai_menu.addAction(actions.open_ai_workflow_center)
    ai_menu.addSeparator()
    ai_menu.addAction(actions.run_ai_culling)
    ai_menu.addAction(actions.quick_rerank_ai_culling)
    ai_menu.addAction(actions.apply_ai_culling)
    ai_menu.addAction(actions.sort_ai_semantic_folders)
    ai_menu.addSeparator()

    training_menu = ai_menu.addMenu("Adapter")
    _add_ai_training_actions(training_menu, actions)

    results_menu = ai_menu.addMenu("Results")
    results_menu.addAction(actions.load_saved_ai)
    results_menu.addAction(actions.load_ai_results)
    results_menu.addAction(actions.clear_ai_results)
    results_menu.addSeparator()
    results_menu.addAction(actions.open_ai_report)
    results_menu.addAction(actions.show_ai_review_summary)
    results_menu.addAction(actions.ai_review_tag_legend)

    review_tools_menu = ai_menu.addMenu("Review Tools")
    review_tools_menu.addAction(actions.next_ai_pick)
    review_tools_menu.addAction(actions.next_unreviewed_ai_pick)
    review_tools_menu.addAction(actions.compare_ai_group)
    review_tools_menu.addAction(actions.review_ai_disagreements)

    ai_menu.addSeparator()
    setup_menu = ai_menu.addMenu("Runtime")
    setup_menu.addAction(actions.install_ai_runtime)
    setup_menu.addAction(actions.download_ai_model)
    setup_menu.addSeparator()
    setup_menu.addAction(actions.reset_ai_review_cache)

    tools_menu = menu_bar.addMenu("&Tools")
    tools_menu.addAction(actions.open_command_palette)
    tools_menu.addSeparator()
    tools_menu.addAction(actions.batch_rename_selection)
    tools_menu.addAction(actions.batch_resize_selection)
    tools_menu.addAction(actions.batch_convert_selection)
    tools_menu.addSeparator()
    tools_menu.addAction(actions.extract_archive)
    tools_menu.addSeparator()
    diagnostics_menu = tools_menu.addMenu("Diagnostics")
    diagnostics_menu.addAction(actions.performance_logging)
    diagnostics_menu.addAction(actions.open_performance_log_folder)

    # View menu absorbs the old "Window" menu so layout/dock/toolbar controls
    # all live in one place.
    view_menu.addSeparator()
    view_menu.addAction(actions.show_workspace_toolbar)
    toolbar_position_menu = view_menu.addMenu("Toolbar Position")
    toolbar_position_menu.addAction("Top", lambda _checked=False: window._set_workspace_bar_position("top"))
    toolbar_position_menu.addAction("Bottom", lambda _checked=False: window._set_workspace_bar_position("bottom"))
    if dock_actions:
        panels_menu = view_menu.addMenu("Panels")
        for key in ("library", "inspector"):
            action = dock_actions.get(key)
            if action is not None:
                panels_menu.addAction(action)
        layout_submenu = view_menu.addMenu("Panel Layout")
        _add_panel_layout_menu(layout_submenu, window, "library", "Library")
        _add_panel_layout_menu(layout_submenu, window, "inspector", "Inspector")
        layout_submenu.addSeparator()
        layout_submenu.addAction(
            "Swap Left And Right Panels",
            lambda _checked=False: window.workspace_docks.swap_sides(),
        )
        layout_submenu.addSeparator()
        _add_workspace_presets_menu(layout_submenu, actions, workspace_preset_menu)
    view_menu.addAction(actions.reset_layout)

    # Settings now has real entries instead of a single-item submenu so the
    # click flow is: Settings menu → Open Settings (or jump straight to a
    # related dialog). Ctrl+, still opens the main dialog directly.
    settings_menu = menu_bar.addMenu("&Settings")
    settings_menu.addAction(actions.workflow_settings)
    settings_menu.addAction(actions.keyboard_help)
    settings_menu.addAction(actions.file_associations)
    settings_menu.addSeparator()
    settings_menu.addAction(actions.reset_layout)

    help_menu = menu_bar.addMenu("&Help")
    help_menu.addAction(actions.keyboard_help)
    help_menu.addAction(actions.ai_guide)
    help_menu.addAction(actions.ai_review_tag_legend)
    help_menu.addAction(actions.advanced_help)
    help_menu.addSeparator()
    help_menu.addAction(actions.about)
