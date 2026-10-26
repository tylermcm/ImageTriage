"""The rail's pinned buttons: when more are pinned than fit, the ones last in the order shown wait in a "..." menu instead of being cut off or covering "+"."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QObject

from test_qml_shell import _load_main, pump, qapp, rows  # noqa: F401  (the same helpers and application fixture)


@pytest.fixture
def shell(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path)
    engine.rootContext().contextProperty("photos").setRows(rows(12), "/p")
    window.show()
    yield engine, window, warnings
    window.close()
    pump(qapp, 0.1)


def js(value):
    return value.toVariant() if hasattr(value, "toVariant") else value


def resize(qapp, window, height):
    window.setWidth(1200)
    window.setHeight(height)
    pump(qapp, 0.5)


def test_everything_fits_in_a_tall_window_and_there_is_no_more_button(shell, qapp):
    engine, window, warnings = shell
    resize(qapp, window, 900)
    rail = window.findChild(QObject, "rail")
    assert rail.property("overflowing") is False and list(js(rail.property("overflowKeys"))) == []
    assert window.findChild(QObject, "railMore").property("visible") is False


def test_a_short_window_moves_the_last_buttons_into_the_more_menu_and_keeps_plus_in_view(shell, qapp):
    engine, window, warnings = shell
    resize(qapp, window, 560)
    rail = window.findChild(QObject, "rail")
    keys = list(js(rail.property("overflowKeys")))
    assert rail.property("overflowing") is True and keys, "something must have moved into the menu"
    assert window.findChild(QObject, "railMore").property("visible") is True
    shown = [m["k"] for m in js(rail.property("shownOrder"))]
    assert shown[-len(keys):] == keys                          # the last ones in the order shown, in that order
    assert abs(rail.property("pinFit") - 30) < 0.01            # the buttons are at their smallest first
    assert rail.property("maxScroll") == 0                     # nothing scrolls under "+" any more
    assert rail.property("cap") == len(shown) - len(keys) + 1  # the "..." takes the slot of the last button that fits


def test_the_buttons_in_the_menu_come_back_when_the_window_grows(shell, qapp):
    engine, window, warnings = shell
    resize(qapp, window, 560)
    rail = window.findChild(QObject, "rail")
    assert list(js(rail.property("overflowKeys")))
    resize(qapp, window, 900)
    assert list(js(rail.property("overflowKeys"))) == []


def test_a_menu_button_in_the_more_menu_opens_its_own_menu_beside_the_more_button(shell, qapp):
    from PySide6.QtCore import Q_ARG, QMetaObject

    engine, window, warnings = shell
    resize(qapp, window, 560)
    rail = window.findChild(QObject, "rail")
    keys = list(js(rail.property("overflowKeys")))
    menu_key = next(k for k in keys if k in ("view", "filters", "sort", "columns"))
    ui = engine.rootContext()
    QMetaObject.invokeMethod(rail, "runPin", Q_ARG("QVariant", menu_key))
    pump(qapp, 0.4)
    popups = [o for o in window.findChildren(QObject) if o.property("name") == menu_key and o.property("opened") is True]
    assert popups, "its menu opened"
    assert popups[0].property("parent").objectName() == "railMore"


def test_pinning_more_buttons_in_a_tall_window_never_hides_any_when_they_fit(shell, qapp):
    from PySide6.QtCore import Q_ARG, QMetaObject

    engine, window, warnings = shell
    resize(qapp, window, 900)
    rail = window.findChild(QObject, "rail")
    count = rail.property("pinCount")
    assert count == len(js(rail.property("shownOrder")))
    assert rail.property("cap") >= count                       # 10 pinned buttons fit at 900 px
