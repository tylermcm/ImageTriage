"""Dragging buttons into a new order in the real window: the rail's destinations, the rail's pinned tools and the top bar,
each reordered only among themselves, and the new order saved."""
from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from image_triage.ui.nav_rail import NavRail


def _mouse(widget: QWidget, kind: QEvent.Type, global_at: QPoint, *, held: bool) -> None:
    event = QMouseEvent(
        kind,
        QPointF(widget.mapFromGlobal(global_at)),
        QPointF(global_at),
        Qt.MouseButton.LeftButton if kind != QEvent.Type.MouseMove else Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton if held else Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(widget, event)


def _drag(button: QWidget, dx: int, dy: int) -> None:
    origin = button.mapToGlobal(button.rect().center())
    _mouse(button, QEvent.Type.MouseButtonPress, origin, held=True)
    for step in range(1, 7):
        _mouse(button, QEvent.Type.MouseMove, origin + QPoint(dx * step // 6, dy * step // 6), held=True)
    _mouse(button, QEvent.Type.MouseButtonRelease, origin + QPoint(dx, dy), held=False)
    QTest.qWait(350)
    QApplication.instance().processEvents()


def test_rail_destinations_reorder_and_report_the_order() -> None:
    QApplication.instance() or QApplication([])
    rail = NavRail()
    for key, label in (("folders", "Library"), ("faces", "Faces"), ("collections", "Collections"), ("pocketdrop", "PocketDrop")):
        rail.add_destination(key, label, key)
    rail.resize(rail.width(), 400)
    rail.show()
    QApplication.instance().processEvents()
    seen: list[list[str]] = []
    rail.order_changed.connect(seen.append)
    first = rail.button("folders")
    pitch = rail.button("faces").y() - first.y()
    _drag(first, 0, int(pitch * 2.1))
    assert rail.keys() == ("faces", "collections", "folders", "pocketdrop")
    assert seen == [["faces", "collections", "folders", "pocketdrop"]]
    ys = [rail.button(key).y() for key in rail.keys()]
    assert ys == sorted(ys), "the layout follows the new order"
    assert rail.current() == "folders", "dragging does not select the button"
    rail.set_order(["pocketdrop", "bogus"])  # saved orders may name things that no longer exist
    assert rail.keys() == ("pocketdrop", "faces", "collections", "folders")
    rail.close()


def test_pinned_tools_reorder_only_among_themselves_and_are_saved(main_window) -> None:
    window = main_window
    toolbar = window._toolbar
    original = toolbar.pinned_tool_ids()
    window.show()
    try:
        toolbar.set_pinned_tool_ids(["command_palette", "compare", "keyboard_shortcuts"])
        QApplication.instance().processEvents()
        buttons = {item_id: button for button, item_id in toolbar._left_rail_tool_buttons}
        assert list(buttons) == ["command_palette", "compare", "keyboard_shortcuts"]
        first = buttons["command_palette"]
        pitch = buttons["compare"].y() - first.y()
        assert pitch > 0
        _drag(first, 0, int(pitch * 1.1))
        assert toolbar.pinned_tool_ids() == ["compare", "command_palette", "keyboard_shortcuts"]
        assert json.loads(window._settings.value(window.PINNED_TOOLS_KEY)) == ["compare", "command_palette", "keyboard_shortcuts"]
        # The destinations above are a different group: dragging a pinned tool up onto them changes nothing there.
        keys_before = window.left_nav_rail.keys()
        moved = {item_id: button for button, item_id in toolbar._left_rail_tool_buttons}["command_palette"]
        _drag(moved, 0, -600)
        assert window.left_nav_rail.keys() == keys_before
        assert toolbar.pinned_tool_ids()[0] == "command_palette", "dragged to the top of the pinned tools, no further"
    finally:
        toolbar.set_pinned_tool_ids(original)
        window.hide()


def test_top_bar_buttons_trade_places_and_the_layout_is_saved(main_window) -> None:
    window = main_window
    toolbar = window._toolbar
    original = {mode: list(slots) for mode, slots in window._topbar_slots.items()}
    window.show()
    window.resize(1600, 900)
    for _ in range(10):
        QApplication.instance().processEvents()
    window._appearance.apply_layout_ratios()
    for _ in range(10):
        QApplication.instance().processEvents()
    try:
        reorder = toolbar._topbar_reorders["manual"]
        keys = reorder.keys()
        assert len(keys) >= 2
        widgets = dict(toolbar._topbar_action_items["manual"])
        before = list(window._topbar_slots["manual"])
        first, second = keys[0], keys[1]
        pitch = widgets[second].x() - widgets[first].x()
        assert pitch > 0
        _drag(widgets[first], int(pitch * 1.1), 0)
        after = window._topbar_slots["manual"]
        assert after.index(first) == before.index(second) and after.index(second) == before.index(first)
        others = [slot for slot in range(len(before)) if before[slot] not in (first, second)]
        assert [before[slot] for slot in others] == [after[slot] for slot in others], "everything else stays put"
        assert window._topbar_slots["ai"] == after, "the bar is one shared arrangement"
        saved = json.loads(window._settings.value(window.WORKSPACE_TOOLBAR_LAYOUT_KEY, "", str))
        assert saved["slots"]["manual"] == after
        assert toolbar._topbar_reorders["manual"].keys()[:2] == [second, first] or toolbar._topbar_reorders["manual"].keys()[:2] == [first, second]
    finally:
        for mode, slots in original.items():
            window._topbar_slots[mode] = list(slots)
            toolbar.sync_items_from_slots(mode, slots_override=slots)
        toolbar.save_topbar_slots()
        toolbar.rebuild_topbar_action_stack(force=True)
        window.hide()


def test_the_navigation_cluster_reorders_even_past_disabled_buttons_and_is_saved(main_window) -> None:
    window = main_window
    toolbar = window._toolbar
    original = toolbar.saved_nav_cluster_order()
    window.show()
    window.resize(1600, 900)
    for _ in range(10):
        QApplication.instance().processEvents()
    try:
        keys = toolbar._nav_reorder.keys()
        assert keys == ["open", "back", "forward", "up", "refresh", "undo"] or sorted(keys) == sorted(["open", "back", "forward", "up", "refresh", "undo"])
        buttons = {item_id: button for button, item_id in toolbar._topbar_labeled_nav_buttons}
        assert not buttons["back"].isEnabled(), "Back starts disabled: it can still be dragged"
        # Move Back (disabled) two places to the right, over Forward and Up.
        pitch = buttons[keys[1]].geometry().x() - buttons[keys[0]].geometry().x()
        assert pitch > 0
        before = list(keys)
        _drag(buttons[before[1]], int(pitch * 2.1), 0)
        expected = [before[0], before[2], before[3], before[1]] + before[4:]
        assert toolbar._nav_reorder.keys() == expected
        assert json.loads(window._settings.value(window.TOPBAR_NAV_ORDER_KEY)) == expected
        xs = [buttons[item_id].x() for item_id in expected]
        assert xs == sorted(xs), "the cluster is laid out in the new order"
        # A saved order that names buttons that no longer exist (or leaves some out) still gives a whole cluster.
        toolbar.apply_nav_cluster_order(["undo", "ghost"])
        assert toolbar._nav_reorder.keys()[0] == "undo" and len(toolbar._nav_reorder.keys()) == 6
    finally:
        toolbar.apply_nav_cluster_order(original)
        window._settings.setValue(window.TOPBAR_NAV_ORDER_KEY, json.dumps(original))
        window.hide()


def test_nothing_drags_in_collection_mode(main_window) -> None:
    window = main_window
    toolbar = window._toolbar
    window._collection_mode = True
    try:
        assert toolbar.reorder_enabled() is False
    finally:
        window._collection_mode = False
    assert toolbar.reorder_enabled() is True
