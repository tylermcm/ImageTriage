from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QMenu, QToolBar, QToolButton

from .actions import MainWindowActions


def _add_toolbar_action(toolbar: QToolBar, action, *, toolbar_text: str, min_width: int = 98) -> None:
    action.setIconText(toolbar_text)
    toolbar.addAction(action)
    button = toolbar.widgetForAction(action)
    if isinstance(button, QToolButton):
        button.setObjectName("primaryToolbarButton")
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
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
    toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)

    _add_toolbar_action(toolbar, actions.open_folder, toolbar_text="Open", min_width=96)
    _add_toolbar_action(toolbar, actions.refresh_folder, toolbar_text="Refresh", min_width=108)
    _add_toolbar_action(toolbar, actions.undo, toolbar_text="Undo", min_width=92)
    toolbar.addSeparator()
    _add_toolbar_action(toolbar, actions.run_ai_culling, toolbar_text="Run AI", min_width=98)
    ai_results_menu = QMenu("AI Results", toolbar)
    ai_results_menu.addAction(actions.load_saved_ai)
    ai_results_menu.addAction(actions.load_ai_results)
    ai_results_menu.addAction(actions.clear_ai_results)
    ai_results_menu.addSeparator()
    ai_results_menu.addAction(actions.open_ai_report)
    _add_toolbar_menu(toolbar, text="AI Results", menu=ai_results_menu, min_width=104)
    if toolbar.layout() is not None:
        toolbar.layout().setContentsMargins(2, 2, 2, 2)
        toolbar.layout().setSpacing(2)
    return toolbar
