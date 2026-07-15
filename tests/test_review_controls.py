from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QPushButton, QToolButton

from image_triage.ui.review_controls import ReviewControlsContext, ReviewControlsPanel


def _ensure_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _actions() -> SimpleNamespace:
    owner = _ensure_app()

    def action(text: str) -> QAction:
        return QAction(text, owner)

    return SimpleNamespace(
        accept_selection=action("Mark Winner"),
        reject_selection=action("Reject Selection"),
        next_ai_pick=action("Next AI Top Pick"),
        review_ai_disagreements=action("Review AI Disagreements"),
        compare_ai_group=action("Compare Current AI Group"),
        winner_ladder_mode=action("Winner Ladder"),
    )


def test_review_controls_render_context_and_emit_commands() -> None:
    _ensure_app()
    actions = _actions()
    panel = ReviewControlsPanel(actions)
    accept_triggers: list[bool] = []
    reject_triggers: list[bool] = []
    next_requests: list[bool] = []
    disagreement_requests: list[bool] = []
    filter_action_triggers: list[bool] = []
    actions.accept_selection.triggered.connect(lambda _checked=False: accept_triggers.append(True))
    actions.reject_selection.triggered.connect(lambda _checked=False: reject_triggers.append(True))
    panel.next_unreviewed_requested.connect(lambda: next_requests.append(True))
    panel.next_disagreement_requested.connect(lambda: disagreement_requests.append(True))
    panel._actions.review_ai_disagreements.triggered.connect(
        lambda _checked=False: filter_action_triggers.append(True)
    )

    panel.set_context(
        ReviewControlsContext(
            selection_count=2,
            records_available=True,
            decision_label="Mixed decisions",
            decision_state="mixed",
            decision_meta="AI Review / AI Miss",
            group_summary="Burst 2/5",
        )
    )

    assert panel.selection_count_label.text() == "2 selected"
    assert panel.decision_label.text() == "Mixed decisions"
    assert panel.decision_meta_label.text() == "AI Review / AI Miss"
    assert not panel.findChildren(QToolButton, "reviewStageButton")
    assert not panel.findChildren(QToolButton, "leftRatingStar")
    assert not panel.group_section.isHidden()

    next(
        button
        for button in panel.findChildren(QPushButton)
        if button.text() == "Winner"
    ).click()
    next(
        button
        for button in panel.findChildren(QPushButton)
        if button.text() == "Reject"
    ).click()
    panel.next_unreviewed_button.click()
    panel.next_disagreement_button.click()
    assert accept_triggers == [True]
    assert reject_triggers == [True]
    assert next_requests == [True]
    assert disagreement_requests == [True]
    assert filter_action_triggers == []


def test_review_controls_follow_action_enabled_state() -> None:
    _ensure_app()
    actions = _actions()
    panel = ReviewControlsPanel(actions)
    ai_pick_button = next(
        button
        for button in panel.findChildren(QPushButton)
        if button.text() == "AI Pick"
    )

    actions.next_ai_pick.setEnabled(False)
    QApplication.processEvents()

    assert not ai_pick_button.isEnabled()


def test_disagreement_navigation_does_not_change_filters_when_none_exist() -> None:
    from image_triage.window import MainWindow

    record = SimpleNamespace(name="plain.jpg", is_folder=False)

    class GridStub:
        selected_index: int | None = None

        @staticmethod
        def current_index() -> int:
            return 0

        def set_current_index(self, index: int) -> None:
            self.selected_index = index

    class StatusStub:
        message = ""

        def showMessage(self, message: str) -> None:
            self.message = message

    grid = GridStub()
    status = StatusStub()
    filter_sentinel = object()
    window = SimpleNamespace(
        _ai_bundle=object(),
        _records=[record],
        _filter_query=filter_sentinel,
        grid=grid,
        _record_at=lambda index: record if index == 0 else None,
        _workflow_insight_for_record=lambda _record: None,
        _is_record_disputed=lambda _record: False,
        statusBar=lambda: status,
    )

    MainWindow._jump_to_next_ai_disagreement(window)

    assert grid.selected_index is None
    assert window._filter_query is filter_sentinel
    assert status.message == "No AI disagreement found in the current view"


def test_topbar_slot_count_is_derived_from_available_width() -> None:
    from image_triage.window import MainWindow

    assert MainWindow._topbar_visible_slot_count_for_width(0) == MainWindow.TOPBAR_INITIAL_VISIBLE_SLOTS
    assert MainWindow._topbar_visible_slot_count_for_width(36) == 1
    assert MainWindow._topbar_visible_slot_count_for_width(76) == 2
    assert MainWindow._topbar_visible_slot_count_for_width(276) == 7
    assert MainWindow._topbar_visible_slot_count_for_width(9999) == MainWindow.TOPBAR_SLOT_COUNT


def test_topbar_overflow_flattens_popup_menu_entries() -> None:
    _ensure_app()
    from image_triage.window import MainWindow

    def popup_factory() -> QMenu:
        popup = QMenu("AI Results")
        submenu = QMenu("Pick / Review / Reject", popup)
        submenu.addAction(QAction("Winner", submenu))
        popup.addMenu(submenu)
        popup.addAction(QAction("Open AI Report", popup))
        return popup

    overflow = QMenu()
    target = SimpleNamespace(
        _keep_topbar_overflow_menu_source=MainWindow._keep_topbar_overflow_menu_source,
    )

    MainWindow._add_topbar_overflow_popup_entries(target, overflow, "AI Results", popup_factory)

    actions = overflow.actions()
    assert [action.text() for action in actions] == [
        "AI Results",
        "Pick / Review / Reject",
        "Open AI Report",
    ]
    assert actions[0].menu() is None
    assert actions[1].menu() is not None
    assert actions[2].menu() is None
