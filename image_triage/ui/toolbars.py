from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QMenu, QToolBar, QToolButton

from .actions import MainWindowActions
from .icons import build_symbol_icon
from .menus import add_ai_adapter_actions, add_ai_results_actions


def _add_toolbar_action(toolbar: QToolBar, action, *, toolbar_text: str, min_width: int = 98) -> None:
    action.setIconText(toolbar_text)
    if action.icon().isNull():
        fallback = {
            "AI Workflow": "AI",
            "Quick Rerank": "\u21c5",
            "Review Labels": "\u2605",
            "Dispute AI": "!",
        }.get(toolbar_text, toolbar_text[:2])
        action.setIcon(build_symbol_icon(fallback, QColor(190, 198, 208), pixel_size=18, font_size=9 if len(fallback) > 1 else 13))
    toolbar.addAction(action)
    button = toolbar.widgetForAction(action)
    if isinstance(button, QToolButton):
        button.setObjectName("primaryToolbarButton")
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setToolTip(action.toolTip() or toolbar_text)
        button.setFixedSize(32, 30)


def _add_toolbar_menu(toolbar: QToolBar, *, text: str, menu: QMenu, min_width: int = 98) -> None:
    button = QToolButton(toolbar)
    button.setObjectName("primaryToolbarButton")
    button.setText(text)
    button.setIcon(build_symbol_icon("\u25be", QColor(190, 198, 208), pixel_size=18, font_size=12))
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    button.setToolTip(text)
    button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    button.setMenu(menu)
    button.setFixedSize(32, 30)
    toolbar.addWidget(button)


def build_primary_toolbar(window, actions: MainWindowActions) -> QToolBar:
    toolbar = QToolBar("Primary", window)
    toolbar.setObjectName("primaryToolbar")
    toolbar.setMovable(False)
    toolbar.setFloatable(False)
    toolbar.setIconSize(QSize(18, 18))
    toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)

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
