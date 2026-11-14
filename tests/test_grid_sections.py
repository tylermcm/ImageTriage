"""Day and hour headers in the grid when it is sorted by a time: the sections, their cells, and the grid's keys and clicks around them."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtTest import QTest

from image_triage import grid_sections
from image_triage.grid_sections import FILLER, Section, layout, sections_for
from image_triage.qml_shell.photos import CELL_FILLER, CELL_FOLDER, CELL_HEADER, CELL_PHOTO, GridModel, KindRole, PhotoModel, PhotoRowRole
from test_qml_shell import _load_main, pump, qapp  # noqa: F401


def at(day: int, hour: int, minute: int = 0) -> float:
    return time.mktime((2026, 11, day, hour, minute, 0, 0, 0, -1))


def timed_rows(times, folder="/p"):
    """Rows as a scan makes them, with a record that carries the time the file was changed."""
    out = []
    for index, seconds in enumerate(times):
        name = f"{index:05d}.jpg"
        record = SimpleNamespace(identity_modified_ns=int(seconds * 1e9), identity_size=1000 + index, path=f"{folder}/{name}", name=name)
        out.append({"path": f"{folder}/{name}", "name": name, "ext": "JPG", "raw": False, "decision": "", "rating": 0, "ai": -1, "record": record})
    return out


# -- the sections -------------------------------------------------------------------------------------------------------------------
def test_a_section_per_day_with_its_date_and_count():
    found = sections_for([at(12, 9), at(12, 15), at(13, 8), at(15, 23), at(15, 23, 30)])
    assert [(s.start, s.count, s.title, s.detail) for s in found] == [
        (0, 2, "Thu 12 Nov 2026", ""), (2, 1, "Fri 13 Nov 2026", ""), (3, 2, "Sun 15 Nov 2026", "")]


def test_a_day_with_many_photos_is_divided_by_the_hour_and_a_small_day_is_not():
    busy = [at(12, 9, m % 60) for m in range(70)] + [at(12, 10, m % 60) for m in range(50)] + [at(12, 14)]
    found = sections_for(busy + [at(13, 9), at(13, 17)])
    assert [(s.start, s.count, s.title, s.detail) for s in found] == [
        (0, 70, "Thu 12 Nov 2026", "09:00 – 10:00"), (70, 50, "Thu 12 Nov 2026", "10:00 – 11:00"), (120, 1, "Thu 12 Nov 2026", "14:00 – 15:00"),
        (121, 2, "Fri 13 Nov 2026", "")]
    assert len(sections_for([at(12, 9)] * 500)) == 1, "many photos within one hour are still one section"


def test_no_sections_without_any_time_and_a_group_for_the_photos_that_have_none():
    assert sections_for([0, 0, 0]) == [] and sections_for([]) == []
    assert [(s.start, s.count, s.title) for s in sections_for([0, 0, at(12, 9)])] == [(0, 2, "No date"), (2, 1, "Thu 12 Nov 2026")]


def test_each_section_starts_a_row_with_its_header_and_its_last_row_is_filled_out():
    cells, index_of_row = layout([Section(0, 5, "a"), Section(5, 2, "b")], columns=4, lead=1)
    #   folder  F  F  F | H0 0 1 2 | 3 4 F F | H1 5 6
    assert cells == [FILLER, FILLER, FILLER, -2, 0, 1, 2, 3, 4, FILLER, FILLER, -3, 5, 6]
    assert index_of_row == [5, 6, 7, 8, 9, 13, 14]
    assert all((1 + cells.index(-2 - n)) % 4 == 0 for n in range(2)), "a header is the first cell of a row"


# -- the model the grid shows ---------------------------------------------------------------------------------------------------------
@pytest.fixture
def grid(qapp):
    photos = PhotoModel(parent=qapp)
    model = GridModel(photos, parent=qapp)
    model.setColumns(4)
    photos.setRows(timed_rows([at(12, 9), at(12, 10), at(12, 11), at(12, 12), at(12, 13), at(13, 9), at(13, 10)]), "/p")
    return photos, model


def kinds(model):
    return [model.data(model.index(i), KindRole) for i in range(model.rowCount())]


def test_headers_only_for_the_time_orders(grid):
    photos, model = grid
    assert not model.sectioned and model.rowCount() == 7 and model.indexOfRow(3) == 3 and model.rowAt(3) == 3
    for key in ("captured", "modified"):
        photos.setSort(key)
        assert model.sectioned and kinds(model).count(CELL_HEADER) == 2, key
    for key in ("name", "size", "ai", "rating", "decision"):
        photos.setSort(key)
        assert not model.sectioned and model.rowCount() == 7, key


def test_cells_and_photo_rows_translate_both_ways(grid):
    photos, model = grid
    photos.setSort("modified")
    H, P, F = CELL_HEADER, CELL_PHOTO, CELL_FILLER
    assert kinds(model) == [H, P, P, P, P, P, F, F, H, P, P]
    assert [model.rowAt(i) for i in range(model.rowCount())] == [-1, 0, 1, 2, 3, 4, -1, -1, -1, 5, 6]
    assert [model.indexOfRow(r) for r in range(7)] == [1, 2, 3, 4, 5, 9, 10]
    assert model.data(model.index(0), Qt.ItemDataRole.UserRole + 2) == "Thu 12 Nov 2026"          # the header's name
    assert model.data(model.index(9), PhotoRowRole) == 5
    model.setColumns(2)                                                    # a narrower grid: the same sections, laid out again
    assert kinds(model) == [H, P, P, P, P, P, H, P, P] and model.indexOfRow(5) == 7


def test_the_cursor_never_lands_on_a_header_or_an_empty_cell(grid):
    photos, model = grid
    photos.setSort("modified")
    # H 0 1 2 | 3 4 F F | H 5 6
    assert model.landing(0, 1) == 1, "Left from the first photo stays"
    assert model.landing(6, 5) == 9, "Right from the last photo of a day goes to the first of the next"
    assert model.landing(8, 9) == 5, "and Left comes back"
    assert model.landing(7, 3) == 5, "Down onto an empty cell takes the last photo of that row"
    assert model.landing(8, 4) == 9, "Down onto a header takes the photo after it"
    assert model.landing(0, 10) == 1 and model.landing(10, 0) == 10, "Home and End"
    assert model.landing(6, 10) == 5, "Up from the next day into the filled-out row"


def test_a_change_to_a_photo_reaches_its_cell(grid):
    photos, model = grid
    photos.setSort("modified")
    seen = []
    model.dataChanged.connect(lambda top, bottom, roles: seen.append((top.row(), bottom.row())))
    photos.setDecision(5, "win")
    assert seen == [(9, 9)]


def test_folder_cards_come_first_and_the_first_section_starts_a_new_row(qapp):
    photos = PhotoModel(parent=qapp)
    model = GridModel(photos, parent=qapp)
    model.setColumns(3)
    photos._sort = "modified"
    photos.setRows(timed_rows([at(12, 9), at(12, 10)]), "/p")
    photos._subfolders = [{"name": "sub", "path": "/p/sub"}]
    photos._rebuild()
    assert kinds(model) == [CELL_FOLDER, CELL_FILLER, CELL_FILLER, CELL_HEADER, CELL_PHOTO, CELL_PHOTO]
    assert model.rowAt(0) == -1 and model.indexOfRow(-1) == 0 and model.indexOfRow(0) == 4


def test_twenty_thousand_photos_are_sectioned_and_laid_out_quickly(qapp):
    photos = PhotoModel(parent=qapp)
    model = GridModel(photos, parent=qapp)
    model.setColumns(6)
    start = at(1, 0)
    photos.setRows(timed_rows([start + n * 97 for n in range(20000)]), "/p")          # about three weeks, 890 photos a day
    began = time.perf_counter()
    photos.setSort("modified")
    sort_and_sections = time.perf_counter() - began
    began = time.perf_counter()
    made = sections_for([r["record"].identity_modified_ns / 1e9 for r in photos._rows])
    sections_only = time.perf_counter() - began
    began = time.perf_counter()
    layout(made, 6)
    layout_only = time.perf_counter() - began
    assert model.sectioned and len(made) > 400
    assert sum(s.count for s in made) == 20000 and model.rowAt(model.indexOfRow(19999)) == 19999
    print(f"20,000 photos: sort+sections+layout {sort_and_sections * 1000:.0f} ms, sections {sections_only * 1000:.0f} ms, layout {layout_only * 1000:.1f} ms, {len(made)} sections")
    assert sections_only < 0.5 and layout_only < 0.2


# -- the real window ----------------------------------------------------------------------------------------------------------------
def cell_point(grid, index):
    cols = grid.parentItem().property("cols")
    width, height = grid.property("cellWidth"), grid.property("cellHeight")
    local = QPointF((index % cols + 0.5) * width, (index // cols + 0.5) * height - (grid.property("contentY") - grid.property("originY")))
    return grid.mapToScene(local).toPoint()


def test_the_window_shows_headers_and_keys_and_clicks_work_around_them(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path)
    context = engine.rootContext()
    photos, model = context.contextProperty("photos"), context.contextProperty("gridModel")
    times = [at(12, 9, m) for m in range(7)] + [at(13, 9, m) for m in range(9)] + [at(14, 9)]
    photos.setRows(timed_rows(times), "/p")
    window.setWidth(1440)
    window.setHeight(900)
    window.show()
    pump(qapp, 0.6)
    grid = window.findChild(QObject, "photoGrid")
    root = grid.parentItem()
    cols = root.property("cols")
    grid.forceActiveFocus()
    grid.setProperty("currentIndex", 3)
    photos.setSort("modified")
    pump(qapp, 0.4)
    assert model.sectioned and grid.property("count") == model.rowCount()
    assert root.property("row") == 3 and grid.property("currentIndex") == model.indexOfRow(3), "the grid stays on its photo when the headers appear"
    headers = [item for item in walk(grid) if item.objectName() == "gridSection" and item.property("visible")]
    assert len(headers) == 3

    last_of_day = model.indexOfRow(6)
    grid.setProperty("currentIndex", last_of_day)
    QTest.keyClick(window, Qt.Key.Key_Right)
    assert root.property("row") == 7 and grid.property("currentIndex") == model.indexOfRow(7), "Right steps over the empty cells and the header"
    QTest.keyClick(window, Qt.Key.Key_Left)
    assert root.property("row") == 6
    QTest.keyClick(window, Qt.Key.Key_Home)
    assert root.property("row") == 0
    QTest.keyClick(window, Qt.Key.Key_End)
    assert root.property("row") == 16
    QTest.keyClick(window, Qt.Key.Key_Home)
    QTest.keyClick(window, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    QTest.keyClick(window, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
    assert photos.selectionRows(0) == [0, 1, 2], "a Shift range is counted in photos, not in cells"
    QTest.keyClick(window, Qt.Key.Key_Escape)
    for _ in range(12):                                                    # Down, row after row, through all three days
        QTest.keyClick(window, Qt.Key.Key_Down)
    assert root.property("row") >= 0 and model.rowAt(grid.property("currentIndex")) == root.property("row")
    QTest.keyClick(window, Qt.Key.Key_Home)                                # back to the top: the header clicked next is in view, clear of the bar
    pump(qapp, 0.3)

    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, cell_point(grid, model.indexOfRow(7) - 1))
    pump(qapp, 0.1)
    assert photos.selectionRows(0) == list(range(7, 16)), "a click on a header picks its section"
    assert root.property("row") == 7
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, cell_point(grid, model.indexOfRow(2)))
    pump(qapp, 0.1)
    assert photos.picked == 0 and root.property("row") == 2
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, cell_point(grid, model.indexOfRow(8)))
    pump(qapp, 0.1)
    assert photos.selectionRows(0) == [2, 3, 4, 5, 6, 7, 8]

    photos.clearSelection()
    photos.setSort("name")
    pump(qapp, 0.3)
    assert not model.sectioned and grid.property("count") == 17
    assert not [w for w in warnings if "PhotoGrid.qml" in w or "Main.qml" in w], warnings
    assert cols >= 2
    window.close(); engine.deleteLater(); pump(qapp, 0.2)


def walk(item):
    yield item
    for child in item.childItems():
        yield from walk(child)


def test_hours_above_is_a_number_of_photos():
    assert grid_sections.HOURS_ABOVE >= 50
