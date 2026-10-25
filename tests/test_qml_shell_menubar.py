"""The top bar's File, Review, View and AI menus: they open, switch as the pointer moves along the bar, and each row runs its command or says it
is not connected yet."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, QPoint, QPointF, Qt
from PySide6.QtTest import QTest

from test_qml_shell import _load_main, pump, qapp, rows  # noqa: F401  (the same helpers and application fixture)


@pytest.fixture
def shell(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path)
    window.setWidth(1440)
    window.setHeight(900)
    context = engine.rootContext()
    context.contextProperty("photos").setRows([dict(r, decision="", rating=0) for r in rows(6)], "/p")      # nothing decided or rated
    pump(qapp, 0.6)
    yield window, context, warnings
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)


def find(window, name):
    """An item by its objectName, looking through the visual tree (a Repeater's delegates are not QObject children of anything findChild reaches)."""
    stack = [window.contentItem()]
    while stack:
        item = stack.pop()
        if item.objectName() == name:
            return item
        stack.extend(item.childItems())
    return None


def centre(window, name):
    item = find(window, name)
    assert item is not None, name
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    return QPoint(int(point.x()), int(point.y()))


def click(qapp, window, name):
    point = centre(window, name)
    QTest.mouseMove(window, point)
    pump(qapp, 0.15)
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    pump(qapp, 0.35)


def hover(qapp, window, name):
    QTest.mouseMove(window, centre(window, name))
    pump(qapp, 0.3)


def open_menu(window):
    return find(window, "topMenus").property("which")


def test_a_title_opens_its_menu_and_a_second_click_closes_it(qapp, shell):
    window, context, warnings = shell
    assert open_menu(window) == ""
    click(qapp, window, "menuTitle-File")
    assert open_menu(window) == "file"
    click(qapp, window, "menuTitle-File")
    assert open_menu(window) == ""
    assert not [w for w in warnings if "ReferenceError" in w or "TypeError" in w or "TopMenus" in w], warnings


def test_moving_along_the_bar_switches_menus(qapp, shell):
    window, context, warnings = shell
    click(qapp, window, "menuTitle-File")
    hover(qapp, window, "menuTitle-View")
    assert open_menu(window) == "view"
    hover(qapp, window, "menuTitle-AI")
    assert open_menu(window) == "ai"
    hover(qapp, window, "menuTitle-Help")                                # Help has its own menu, and the others close
    assert open_menu(window) == "" and context.contextProperty("reference").property("menuOpen")
    hover(qapp, window, "menuTitle-Review")
    assert open_menu(window) == "review" and not context.contextProperty("reference").property("menuOpen")


def test_hovering_a_title_with_no_menu_open_does_nothing(qapp, shell):
    window, context, warnings = shell
    hover(qapp, window, "menuTitle-View")
    assert open_menu(window) == ""


def test_escape_and_a_click_below_the_bar_put_the_menu_away(qapp, shell):
    window, context, warnings = shell
    click(qapp, window, "menuTitle-Review")
    QTest.keyClick(window, Qt.Key.Key_Escape)
    pump(qapp, 0.3)
    assert open_menu(window) == ""
    click(qapp, window, "menuTitle-Review")
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(1000, 700))
    pump(qapp, 0.3)
    assert open_menu(window) == ""


def test_a_row_runs_its_command_and_the_menu_closes(qapp, shell):
    window, context, warnings = shell
    photos = context.contextProperty("photos")
    assert photos.row_at(0)["decision"] == ""
    click(qapp, window, "menuTitle-Review")
    click(qapp, window, "menuRow-review.reject_selection")
    assert photos.row_at(0)["decision"] == "rej"
    assert open_menu(window) == ""
    click(qapp, window, "menuTitle-Review")
    click(qapp, window, "menuRow-review.clear_decision")
    assert photos.row_at(0)["decision"] == ""


def test_a_fly_out_shows_beside_its_row_and_its_rows_run(qapp, shell):
    window, context, warnings = shell
    photos = context.contextProperty("photos")
    click(qapp, window, "menuTitle-View")
    flyout = find(window, "topMenuFlyout")
    assert not flyout.property("visible")
    hover(qapp, window, "menuSub-Sort By")
    assert flyout.property("visible")
    click(qapp, window, "menuRow-sort.rating")
    assert photos._sort == "rating" and open_menu(window) == ""
    click(qapp, window, "menuTitle-Review")
    click(qapp, window, "menuSub-Rating")
    click(qapp, window, "menuRow-review.rate.4")
    assert photos.row_at(0)["rating"] == 4


def test_a_row_that_is_not_connected_says_so_and_changes_nothing(qapp, shell):
    window, context, warnings = shell
    toasts = context.contextProperty("toasts")
    photos = context.contextProperty("photos")
    before = [photos.row_at(i)["decision"] for i in range(6)]
    click(qapp, window, "menuTitle-File")
    click(qapp, window, "menuOff-New Folder…")
    assert toasts.items[-1]["kind"] == "warn" and toasts.items[-1]["title"] == "New Folder isn’t connected yet"
    assert open_menu(window) == "" and [photos.row_at(i)["decision"] for i in range(6)] == before


def test_view_filters_the_grid_through_its_rows(qapp, shell):
    window, context, warnings = shell
    photos = context.contextProperty("photos")
    total = photos.shown
    click(qapp, window, "menuTitle-View")
    hover(qapp, window, "menuSub-Show Photos")
    click(qapp, window, "menuRow-view.fmt.raw")
    assert photos.shown <= total
    click(qapp, window, "menuTitle-View")
    hover(qapp, window, "menuSub-Show Photos")
    click(qapp, window, "menuRow-search.clear_filters")
    assert photos.shown == total


def test_every_menu_builds_without_a_qml_complaint(qapp, shell):
    window, context, warnings = shell
    for title in ("File", "Review", "View", "AI"):
        click(qapp, window, f"menuTitle-{title}")
        assert open_menu(window) == title.lower()
        click(qapp, window, f"menuTitle-{title}")
    assert not [w for w in warnings if "ReferenceError" in w or "TypeError" in w or "TopMenus" in w or "Required property" in w], warnings


def glass_of(item):
    """The Glass items (the ones with a `frosted` property) under an item."""
    found, stack = [], [item]
    while stack:
        node = stack.pop()
        if node.property("frosted") is not None:
            found.append(node)
        stack.extend(node.childItems())
    return found


def test_the_menus_are_solid_not_frosted(qapp, shell):
    """What is behind a menu never shows through it: the cards are plain opaque surfaces, in both themes, from the first frame."""
    window, context, warnings = shell
    theme = context.contextProperty("theme")
    for light in (False, True):
        theme.light = light
        click(qapp, window, "menuTitle-Review")
        hover(qapp, window, "menuSub-Rating")
        for name in ("topMenuCard", "topMenuFlyout"):
            glass = glass_of(find(window, name))
            assert glass and all(g.property("frosted") is False for g in glass), (name, light)
        card = find(window, "topMenuCard")
        spot = card.mapToScene(QPointF(card.width() - 3, card.height() / 2))             # the card's own margin, over the grid underneath
        pixels = {window.grabWindow().pixelColor(int(spot.x()), int(spot.y() + dy)).name() for dy in (-60, -20, 20, 60)}
        assert len(pixels) == 1, pixels
        click(qapp, window, "menuTitle-Review")
    assert not [w for w in warnings if "ReferenceError" in w or "TypeError" in w or "Required property" in w], warnings
