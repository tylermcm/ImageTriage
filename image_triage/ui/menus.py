from __future__ import annotations

from .actions import MainWindowActions
from .theme import AppearanceMode


def build_main_menu_bar(window, actions: MainWindowActions) -> None:
    menu_bar = window.menuBar()
    menu_bar.clear()

    file_menu = menu_bar.addMenu("&File")
    file_menu.addAction(actions.open_folder)
    file_menu.addAction(actions.refresh_folder)
    file_menu.addAction(actions.empty_recycle_bin)
    file_menu.addSeparator()
    file_menu.addAction(actions.workflow_settings)
    file_menu.addSeparator()
    file_menu.addAction(actions.exit_app)

    edit_menu = menu_bar.addMenu("&Edit")
    edit_menu.addAction(actions.undo)
    edit_menu.addSeparator()
    edit_menu.addAction(actions.accept_selection)
    edit_menu.addAction(actions.reject_selection)
    edit_menu.addAction(actions.keep_selection)
    edit_menu.addAction(actions.move_selection)
    edit_menu.addAction(actions.delete_selection)
    edit_menu.addAction(actions.restore_selection)

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

    columns_menu = view_menu.addMenu("Columns")
    for count in (2, 3, 4):
        columns_menu.addAction(actions.column_actions[count])

    view_menu.addSeparator()
    view_menu.addAction(actions.compare_mode)
    view_menu.addAction(actions.auto_advance)
    view_menu.addSeparator()
    view_menu.addAction(actions.manual_mode)
    view_menu.addAction(actions.ai_mode)

    review_menu = menu_bar.addMenu("&Review")
    review_menu.addAction(actions.open_preview)
    review_menu.addSeparator()
    review_menu.addAction(actions.accept_selection)
    review_menu.addAction(actions.reject_selection)
    review_menu.addAction(actions.keep_selection)
    review_menu.addAction(actions.delete_selection)
    review_menu.addAction(actions.restore_selection)
    review_menu.addSeparator()
    review_menu.addAction(actions.reveal_in_explorer)
    review_menu.addAction(actions.open_in_photoshop)

    ai_menu = menu_bar.addMenu("&AI")
    ai_menu.addAction(actions.run_ai_culling)
    ai_menu.addAction(actions.load_saved_ai)
    ai_menu.addAction(actions.load_ai_results)
    ai_menu.addAction(actions.clear_ai_results)
    ai_menu.addAction(actions.open_ai_report)
    ai_menu.addSeparator()
    ai_menu.addAction(actions.next_ai_pick)
    ai_menu.addAction(actions.next_unreviewed_ai_pick)
    ai_menu.addAction(actions.compare_ai_group)

    window_menu = menu_bar.addMenu("&Window")
    window_menu.addAction(actions.manual_mode)
    window_menu.addAction(actions.ai_mode)
    window_menu.addSeparator()
    window_menu.addAction(actions.reset_layout)

    help_menu = menu_bar.addMenu("&Help")
    help_menu.addAction(actions.keyboard_help)
    help_menu.addSeparator()
    help_menu.addAction(actions.about)
