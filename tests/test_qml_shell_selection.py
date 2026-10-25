"""Picking several photos in the QML shell: Ctrl and Shift clicks, Ctrl+A, Shift+arrows, decisions and ratings on the pick (one undo step),
and the pick as the scope of a handoff or a batch."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, Qt
from PySide6.QtTest import QTest

from image_triage.qml_shell.batch_model import BatchModel
from image_triage.qml_shell.handoff_model import HandoffModel
from image_triage.qml_shell.photos import PhotoModel
from test_qml_shell import _load_main, pump, qapp, rows  # noqa: F401  (the same helpers and application fixture)


@pytest.fixture
def photos(qapp):
    model = PhotoModel(parent=qapp)
    model.setRows(rows(12), "/p")
    return model


def names(model, picked):
    return [model.row_at(i)["name"] for i in picked]


def test_nothing_is_picked_until_you_pick_and_an_action_applies_to_the_current_photo(photos):
    assert photos.picked == 0 and photos.selectionRows(4) == [4]
    assert len(photos.scope_rows()) == 12                              # no pick: every photo shown


def test_ctrl_click_adds_and_removes_and_starts_from_the_photo_the_grid_was_on(photos):
    photos.toggleSelected(5, 2)
    assert photos.selectionRows(5) == [2, 5] and photos.picked == 2
    photos.toggleSelected(7, 5)
    assert photos.selectionRows(7) == [2, 5, 7]
    photos.toggleSelected(5, 7)
    assert photos.selectionRows(7) == [2, 7]
    photos.clearSelection()
    assert photos.picked == 0 and photos.selectionRows(3) == [3]


def test_shift_picks_a_range_either_way_and_ctrl_a_picks_everything(photos):
    photos.selectRange(3, 6)
    assert photos.selectionRows(6) == [3, 4, 5, 6]
    photos.selectRange(8, 5)
    assert photos.selectionRows(5) == [5, 6, 7, 8]
    photos.selectAll()
    assert photos.picked == 12


def test_the_pick_survives_a_sort_and_goes_with_the_folder(photos):
    photos.selectRange(0, 2)
    picked = {photos.row_at(i)["path"] for i in photos.selectionRows(0)}
    photos.setSort("rating")
    assert {photos.row_at(i)["path"] for i in photos.selectionRows(0)} == picked
    photos.setRows(rows(5), "/other")
    assert photos.picked == 0


def test_a_decision_on_the_pick_is_one_undo_step_and_clears_when_all_have_it(photos):
    photos.selectRange(1, 3)
    before = [photos.row_at(i)["decision"] for i in (1, 2, 3)]
    photos.setDecisionMany(photos.selectionRows(1), "keep")
    assert [photos.row_at(i)["decision"] for i in (1, 2, 3)] == ["keep"] * 3
    photos.setDecisionMany(photos.selectionRows(1), "keep")              # all have it: it clears, as the single toggle does
    assert [photos.row_at(i)["decision"] for i in (1, 2, 3)] == [""] * 3
    assert photos.undo() and [photos.row_at(i)["decision"] for i in (1, 2, 3)] == ["keep"] * 3
    assert photos.undo() and [photos.row_at(i)["decision"] for i in (1, 2, 3)] == before


def test_a_rating_on_the_pick_and_a_single_row_is_the_models_own_toggle(photos):
    photos.selectRange(4, 5)
    photos.setRatingMany(photos.selectionRows(4), 3)
    assert [photos.row_at(i)["rating"] for i in (4, 5)] == [3, 3]
    photos.clearSelection()
    photos.setRatingMany([4], 3)
    assert photos.row_at(4)["rating"] == 0                             # one photo: the same key again clears it


def test_annotations_are_saved_for_every_photo_changed(qapp):
    model = PhotoModel(parent=qapp)
    items = rows(4)
    for item in items:
        item["record"] = object()
        item["decision"] = ""
    model.setRows(items, "/p")
    saved = []
    model.annotationChanged.connect(saved.append)
    model.setDecisionMany([0, 1, 2], "win")
    assert len(saved) == 3


def test_a_handoff_and_a_batch_take_the_pick_else_every_photo(qapp, photos):
    handoff = HandoffModel(photos, memory=True, parent=qapp)
    assert handoff.count == 12
    photos.selectRange(2, 4)
    assert handoff.count == 3 and "3 image bundles" == handoff.countText
    batch = BatchModel(photos, memory=True, parent=qapp)
    assert len(batch._batch()) == 3
    photos.clearSelection()
    assert handoff.count == 12 and len(batch._batch()) == 12


def test_the_grid_keys_pick_with_shift_and_ctrl_a_and_act_on_the_pick(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path)
    model = engine.rootContext().contextProperty("photos")
    model.setRows(rows(12), "/p")
    window.setWidth(1440)
    window.setHeight(900)
    window.show()
    pump(qapp, 0.5)
    grid = window.findChild(QObject, "photoGrid")
    grid.forceActiveFocus()
    grid.setProperty("currentIndex", 1)
    pump(qapp, 0.1)
    QTest.keyClick(window, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    QTest.keyClick(window, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    assert model.selectionRows(3) == [1, 2, 3]
    QTest.keyClick(window, Qt.Key.Key_K)
    assert [model.row_at(i)["decision"] for i in (1, 2, 3)] == ["keep"] * 3
    QTest.keyClick(window, Qt.Key.Key_Escape)
    assert model.picked == 0
    QTest.keyClick(window, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert model.picked == 12
    window.close()
    pump(qapp, 0.1)
