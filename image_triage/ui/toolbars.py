from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QMenu, QToolBar, QToolButton

from .actions import MainWindowActions
from .menus import add_ai_adapter_actions, add_ai_results_actions


def _add_toolbar_action(toolbar: QToolBar, action, *, toolbar_text: str, min_width: int = 98) -> None:
    action.setIconText(toolbar_text)
    toolbar.addAction(action)
    button = toolbar.widgetForAction(action)
    if isinstance(button, QToolButton):
        button.setObjectName("primaryToolbarButton")
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        button.setMinimumWidth(min_width)


def _add_toolbar_menu(toolbar: QToolBar, *, text: str, menu: QMenu, min_width: int = 98) -> None:
    button = QToolButton(toolbar)
    button.setObjectName("primaryToolbarButton")
    button.setText(text)
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    button.setMenu(menu)
    button.setMinimumWidth(min_width)
    toolbar.addWidget(button)


def build_primary_toolbar(window, actions: MainWindowActions) -> QToolBar:
    toolbar = QToolBar("Primary", window)
    toolbar.setObjectName("primaryToolbar")
    toolbar.setMovable(False)
    toolbar.setFloatable(False)
    toolbar.setIconSize(QSize(14, 14))
    toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)

    _add_toolbar_action(toolbar, actions.open_folder, toolbar_text="Open", min_width=96)
    _add_toolbar_action(toolbar, actions.refresh_folder, toolbar_text="Refresh", min_width=108)
    _add_toolbar_action(toolbar, actions.undo, toolbar_text="Undo", min_width=92)
    toolbar.addSeparator()
    _add_toolbar_action(toolbar, actions.open_ai_workflow_center, toolbar_text="AI Workflow", min_width=118)
    _add_toolbar_action(toolbar, actions.quick_rerank_ai_culling, toolbar_text="Quick Rerank", min_width=120)
    _add_toolbar_action(toolbar, actions.review_ai_adapter_labels, toolbar_text="Review Labels", min_width=124)
    _add_toolbar_action(toolbar, actions.dispute_current_ai_result, toolbar_text="Dispute AI", min_width=112)
    ai_results_menu = QMenu("AI Results And Filters", toolbar)
    add_ai_results_actions(ai_results_menu, actions)
    _add_toolbar_menu(toolbar, text="AI Results", menu=ai_results_menu, min_width=104)
    ai_training_menu = QMenu("Adapter Training", toolbar)
    add_ai_adapter_actions(ai_training_menu, actions)
    _add_toolbar_menu(toolbar, text="Adapter", menu=ai_training_menu, min_width=104)
    if toolbar.layout() is not None:
        toolbar.layout().setContentsMargins(2, 2, 2, 2)
        toolbar.layout().setSpacing(2)
    return toolbar
