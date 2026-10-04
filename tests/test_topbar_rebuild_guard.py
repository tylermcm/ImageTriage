"""WI-8.1: ``MainWindow._rebuild_topbar_action_stack`` skips redundant rebuilds,
keeps checkable top-bar buttons in step with their actions, and no longer leaks
a popup menu and an ``action.changed`` connection per button per rebuild."""
from __future__ import annotations

import gc
import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, QSignalBlocker, QTimer
from PySide6.QtWidgets import QApplication, QMenu, QToolButton

from image_triage.ui.display_metrics import STANDARD_DISPLAY
from image_triage.ui.theme import apply_gamma
from image_triage.toolbar_controller import ToolbarController
from image_triage.window import MainWindow

CHECKABLE_ITEMS = {
    # item id -> MainWindow attribute that _update_action_states pushes into the action
    "compare": "_compare_enabled",
    "auto_advance": "_auto_advance_enabled",
    "burst_groups": "_burst_groups_enabled",
    "burst_stacks": "_burst_stacks_enabled",
    "show_hidden_folders": "_show_hidden_folders",
    "zen_mode": "_zen_mode_enabled",
}
ACTION_NAMES = {
    "compare": "compare_mode",
    "auto_advance": "auto_advance",
    "burst_groups": "burst_groups",
    "burst_stacks": "burst_stacks",
    "show_hidden_folders": "show_hidden_folders",
    "zen_mode": "zen_mode",
}


def _turn(ms: int = 30) -> None:
    """A real event-loop turn. ``processEvents()`` alone never delivers a
    ``deleteLater`` posted outside an event handler, so old widgets would pile up."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _widgets(window, target: str = "manual") -> list:
    return [widget for _item_id, widget in window._toolbar._topbar_action_items[target]]


def _all_widgets(window) -> list:
    return _widgets(window, "manual") + _widgets(window, "ai")


def _same_objects(first: list, second: list) -> bool:
    return len(first) == len(second) and all(a is b for a, b in zip(first, second))


@pytest.fixture
def window(main_window):
    """The shared real window with its top-bar inputs restored afterwards."""
    saved_slots = {mode: list(slots) for mode, slots in main_window._topbar_slots.items()}
    saved = {
        "_theme": main_window._theme,
        "_display_profile": main_window._display_profile,
        "_toolbar_placement": main_window._toolbar_placement,
    }
    main_window._toolbar.rebuild_topbar_action_stack(force=True)
    _turn()
    try:
        yield main_window
    finally:
        main_window._topbar_slots = saved_slots
        for name, value in saved.items():
            setattr(main_window, name, value)
        main_window._toolbar.rebuild_topbar_action_stack(force=True)
        _turn()


def _put_items_in_the_bar(window, item_ids: list[str]) -> None:
    """Replace the leading slots of both pages with ``item_ids`` and rebuild."""
    slots = list(window._topbar_slots["manual"])
    for index, item_id in enumerate(item_ids):
        slots[index] = item_id
    window._topbar_slots = {"manual": list(slots), "ai": list(slots)}
    window._toolbar.rebuild_topbar_action_stack(force=True)
    _turn()


# ---------------------------------------------------------------- change detection
def test_unchanged_inputs_keep_the_very_same_widgets(window) -> None:
    before = _all_widgets(window)
    slot_widgets = {mode: list(widgets) for mode, widgets in window._toolbar._topbar_slot_widgets.items()}
    assert before, "the default layout should put buttons in the bar"
    window._toolbar.rebuild_topbar_action_stack()
    window._toolbar.rebuild_topbar_action_stack("manual")
    assert _same_objects(before, _all_widgets(window))
    for mode, widgets in slot_widgets.items():
        assert _same_objects(widgets, window._toolbar._topbar_slot_widgets[mode])


def test_force_rebuilds_even_when_nothing_changed(window) -> None:
    before = _all_widgets(window)
    window._toolbar.rebuild_topbar_action_stack(force=True)
    after = _all_widgets(window)
    assert len(before) == len(after)
    assert not any(a is b for a, b in zip(before, after))


def test_a_missing_recorded_key_rebuilds(window) -> None:
    before = _all_widgets(window)
    window._toolbar._topbar_rebuild_key = None
    window._toolbar.rebuild_topbar_action_stack()
    assert not any(a is b for a, b in zip(before, _all_widgets(window)))


def _first_unused_checkable(window) -> str:
    present = {item for slots in window._topbar_slots.values() for item in slots if item}
    return next(item_id for item_id in CHECKABLE_ITEMS if item_id not in present)


def test_a_changed_slot_list_rebuilds(window) -> None:
    before = _all_widgets(window)
    item_id = _first_unused_checkable(window)
    slots = list(window._topbar_slots["manual"])
    first_used = next(index for index, value in enumerate(slots) if value)
    slots[first_used] = item_id
    window._topbar_slots = {"manual": list(slots), "ai": list(slots)}
    window._toolbar.rebuild_topbar_action_stack()
    after = _all_widgets(window)
    assert not any(a is b for a, b in zip(before, after))
    assert item_id in {built_id for built_id, _widget in window._toolbar._topbar_action_items["manual"]}


def test_a_changed_theme_rebuilds(window) -> None:
    before = _all_widgets(window)
    window._theme = apply_gamma(window._theme, 1.25)
    window._toolbar.rebuild_topbar_action_stack()
    assert not any(a is b for a, b in zip(before, _all_widgets(window)))


def test_a_changed_display_profile_rebuilds(window) -> None:
    before = _all_widgets(window)
    profile = window._display_profile or STANDARD_DISPLAY
    window._display_profile = replace(profile, topbar_glyph_size=profile.topbar_glyph_size + 2)
    window._toolbar.rebuild_topbar_action_stack()
    assert not any(a is b for a, b in zip(before, _all_widgets(window)))


def test_a_changed_toolbar_placement_rebuilds(window) -> None:
    before = _all_widgets(window)
    window._toolbar_placement = "docked" if window._toolbar_placement == "floating" else "floating"
    window._toolbar.rebuild_topbar_action_stack()
    assert not any(a is b for a, b in zip(before, _all_widgets(window)))


def test_a_changed_visible_slot_count_rebuilds_synchronously(window, monkeypatch) -> None:
    """The overflow path must stay synchronous (a deferred layout on resize drew
    a visible jump on every launch), so no event-loop turn is allowed here."""
    before = _all_widgets(window)
    visible = window._toolbar.topbar_visible_slot_count()
    monkeypatch.setattr(window._toolbar, "topbar_visible_slot_count", lambda: max(1, visible - 1))
    window._toolbar.update_topbar_overflow("manual")
    after = _all_widgets(window)
    assert not any(a is b for a, b in zip(before, after))
    assert window._toolbar._topbar_rendered_slot_count == max(1, visible - 1)


def test_deleted_widgets_are_detected_and_rebuilt(window) -> None:
    victim = _widgets(window)[0]
    victim.setParent(None)
    victim.deleteLater()
    _turn()
    window._toolbar.rebuild_topbar_action_stack()
    grid = window._toolbar._topbar_action_layouts["manual"]
    items = window._toolbar._topbar_action_items["manual"]
    assert grid.count() == len(items) > 0
    assert all(grid.indexOf(widget) >= 0 for _item_id, widget in items)


def test_layout_ratio_pass_after_the_first_does_not_rebuild(window) -> None:
    window._appearance.apply_layout_ratios()
    settled = _all_widgets(window)
    window._appearance.apply_layout_ratios()
    window._appearance.apply_layout_ratios()
    assert _same_objects(settled, _all_widgets(window))


def test_theme_refresh_always_rebuilds_even_for_an_equal_theme(window) -> None:
    before = _all_widgets(window)
    window._appearance.refresh_themed_chrome_icons()
    assert not any(a is b for a, b in zip(before, _all_widgets(window)))


def test_placement_change_rebuilds_through_its_setter(window) -> None:
    before = _all_widgets(window)
    target = "docked" if window._toolbar_placement == "floating" else "floating"
    window._toolbar.set_toolbar_placement(target)
    assert not any(a is b for a, b in zip(before, _all_widgets(window)))


# ---------------------------------------------------------------- stale checkable buttons
def _button_for(window, item_id: str, target: str = "manual") -> QToolButton:
    return next(widget for built_id, widget in window._toolbar._topbar_action_items[target] if built_id == item_id)


@pytest.fixture
def checkable_bar(window):
    """A bar holding every checkable item, with the window state they mirror
    restored afterwards."""
    _put_items_in_the_bar(window, list(CHECKABLE_ITEMS))
    saved_state = {attr: getattr(window, attr) for attr in CHECKABLE_ITEMS.values()}
    try:
        yield window
    finally:
        for attr, value in saved_state.items():
            setattr(window, attr, value)
        window._update_action_states()


def test_blocked_checked_state_pushed_by_update_action_states_reaches_the_buttons(checkable_bar) -> None:
    """_update_action_states pushes checked state under QSignalBlocker, which
    also swallows action.changed, the signal the buttons listen to. Without an
    explicit re-sync the buttons keep the stale state until an incidental
    rebuild, which the change-detection guard now (rightly) no longer provides."""
    window = checkable_bar
    for target in ("manual", "ai"):
        for item_id, attr in CHECKABLE_ITEMS.items():
            button = _button_for(window, item_id, target)
            action = getattr(window.actions, ACTION_NAMES[item_id])
            assert button.isCheckable() and action.isCheckable()
            assert button.isChecked() == action.isChecked()

    for attr in CHECKABLE_ITEMS.values():
        setattr(window, attr, True)
    window._update_action_states()
    for target in ("manual", "ai"):
        for item_id in CHECKABLE_ITEMS:
            button = _button_for(window, item_id, target)
            action = getattr(window.actions, ACTION_NAMES[item_id])
            assert action.isChecked(), f"{item_id}: _update_action_states did not check the action"
            assert button.isChecked(), f"{item_id} ({target}): button is stale (action checked, button not)"
            glyph = button.findChild(QToolButton, "appTopBarGlyph")
            assert glyph is not None and glyph.isChecked(), f"{item_id} ({target}): glyph state is stale"

    for attr in CHECKABLE_ITEMS.values():
        setattr(window, attr, False)
    window._update_action_states()
    for target in ("manual", "ai"):
        for item_id in CHECKABLE_ITEMS:
            button = _button_for(window, item_id, target)
            assert not button.isChecked(), f"{item_id} ({target}): button stayed checked after the action was cleared"
            assert not button.findChild(QToolButton, "appTopBarGlyph").isChecked()


def test_sync_helper_repairs_a_silently_changed_action(checkable_bar) -> None:
    window = checkable_bar
    action = window.actions.compare_mode
    with QSignalBlocker(action):
        action.setChecked(True)
    assert not _button_for(window, "compare").isChecked(), "precondition: a blocked change leaves the button behind"
    window._toolbar.sync_topbar_action_buttons()
    assert _button_for(window, "compare").isChecked()
    assert _button_for(window, "compare", "ai").isChecked()
    with QSignalBlocker(action):
        action.setChecked(False)
    window._toolbar.sync_topbar_action_buttons()
    assert not _button_for(window, "compare").isChecked()


def test_user_toggle_through_the_action_still_flows_to_the_button(checkable_bar) -> None:
    window = checkable_bar
    window.actions.burst_groups.setEnabled(True)
    window.actions.burst_groups.trigger()
    try:
        assert _button_for(window, "burst_groups").isChecked() == window.actions.burst_groups.isChecked()
    finally:
        window.actions.burst_groups.setChecked(False)


# ---------------------------------------------------------------- leaks
def _menu_count(window) -> int:
    return len(window.findChildren(QMenu))


def test_popup_menus_belong_to_their_buttons(window) -> None:
    popup_buttons = [
        widget for widget in _all_widgets(window)
        if isinstance(widget, QToolButton) and widget.menu() is not None
    ]
    assert popup_buttons, "expected at least the Review/View popup buttons in the default bar"
    for button in popup_buttons:
        menu = button.menu()
        assert menu.parent() is button, "a topbar popup menu must die with its button, not live on MainWindow"
        assert menu.isWindow(), "reparenting must not strip the popup window flags"
    # And it still pops up (offscreen, non-blocking) once reparented.
    menu = popup_buttons[0].menu()
    menu.popup(popup_buttons[0].mapToGlobal(popup_buttons[0].rect().bottomLeft()))
    try:
        assert menu.isVisible()
    finally:
        menu.close()


def test_forced_rebuilds_do_not_accumulate_menus_or_widgets(window) -> None:
    app = QApplication.instance()
    for _ in range(2):
        window._toolbar.rebuild_topbar_action_stack(force=True)
        _turn(40)
    menus = _menu_count(window)
    widgets = len(app.allWidgets())
    assert menus > 0
    for _ in range(8):
        window._toolbar.rebuild_topbar_action_stack(force=True)
        _turn(40)
    assert _menu_count(window) == menus, "QMenu children of the window grew across rebuilds"
    assert len(app.allWidgets()) <= widgets + 3, "widgets grew across rebuilds"


def test_action_changed_connections_do_not_accumulate(checkable_bar, monkeypatch) -> None:
    window = checkable_bar
    calls = []
    real = ToolbarController.sync_topbar_action_button_for

    def counting(self, button, action, item_id):
        calls.append(item_id)
        return real(self, button, action, item_id)

    monkeypatch.setattr(ToolbarController, "sync_topbar_action_button_for", counting)

    def handlers_per_emit() -> int:
        calls.clear()
        window.actions.compare_mode.changed.emit()
        return len(calls)

    baseline = handlers_per_emit()
    assert baseline == 2, "one live handler per page (manual + ai) expected"
    for _ in range(6):
        window._toolbar.rebuild_topbar_action_stack(force=True)
        _turn(40)
    # The handler objects are owned by their buttons, not by Python names, so
    # they must also survive a garbage-collection pass.
    gc.collect()
    assert handlers_per_emit() == baseline, "action.changed handlers leaked across rebuilds"
