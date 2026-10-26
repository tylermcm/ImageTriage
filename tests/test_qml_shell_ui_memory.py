"""The interface as the person left it: the open tab, the inspector, thumbnail size, sort, names and badges, and the rail's pins come back next run."""
from __future__ import annotations

from PySide6.QtCore import Q_ARG, QMetaObject

from image_triage.app_identity import user_settings
from image_triage.qml_shell.ui_memory import PREFIX, UiMemory
from test_qml_shell import _load_main, pump, qapp  # noqa: F401  (the same helpers and application fixture)


def test_a_value_comes_back_as_it_went_in_and_survives_a_new_session(qapp):
    memory = UiMemory(parent=qapp)
    assert memory.get("tab", "lib") == "lib"                                  # nothing saved yet
    for name, value in (("tab", "faces"), ("inspector", False), ("pins", {"sort": True, "undo": False})):
        memory.set(name, value)
    again = UiMemory(parent=qapp)
    assert again.get("tab", "lib") == "faces" and again.get("inspector", True) is False and again.get("pins", None) == {"sort": True, "undo": False}
    user_settings().remove(PREFIX + "tab")
    assert UiMemory(parent=qapp).get("tab", "lib") == "lib"
    for name in ("inspector", "pins"):
        user_settings().remove(PREFIX + name)


def test_a_damaged_value_is_the_default(qapp):
    user_settings().setValue(PREFIX + "tab", "{not json")
    assert UiMemory(parent=qapp).get("tab", "lib") == "lib"
    user_settings().remove(PREFIX + "tab")


def test_the_sample_page_and_headless_checks_do_not_touch_the_settings(qapp):
    memory = UiMemory(memory=True, parent=qapp)
    memory.set("tab", "drop")
    assert memory.get("tab", "lib") == "drop"                                 # it remembers within the run
    assert user_settings().value(PREFIX + "tab", None) is None                 # and writes nothing
    assert UiMemory(memory=True, parent=qapp).get("tab", "lib") == "lib"


def interface(window):
    """The interface's state (a QML object comes back as a QJSValue)."""
    return window.property("interfaceNow").toVariant()


def window_with(qapp, tmp_path, width=1440):
    engine, window, warnings = _load_main(qapp, tmp_path)
    window.setWidth(width)
    window.setHeight(900)
    pump(qapp, 0.4)
    return engine, window, warnings, engine.rootContext().contextProperty("uiMemory")


def run(window, command):
    QMetaObject.invokeMethod(window, "runCommand", Q_ARG("QVariant", command))
    pump(qapp_of(window), 0.1)


def qapp_of(_window):
    from PySide6.QtWidgets import QApplication

    return QApplication.instance()


def test_the_interface_is_put_back_as_it_was_left(qapp, tmp_path):
    engine, window, warnings, memory = window_with(qapp, tmp_path)
    assert interface(window)["tab"] == "lib" and interface(window)["density"] == "m"
    for name, value in (("tab", "faces"), ("inspector", False), ("density", "l"), ("sort", "rating"), ("names", False), ("badges", False),
                        ("pins", {"sort": True, "undo": False, "somethingNew": True})):
        memory.set(name, value)
    QMetaObject.invokeMethod(window, "restoreInterface")
    pump(qapp, 0.2)
    now = interface(window)
    assert (now["tab"], now["inspector"], now["density"], now["sort"], now["names"], now["badges"]) == ("faces", False, "l", "rating", False, False)
    assert now["pins"]["sort"] is True and now["pins"]["undo"] is False and now["pins"]["open"] is True       # the others keep theirs
    assert "somethingNew" not in now["pins"]                                                                  # a pin this version does not have is ignored
    assert not [w for w in warnings if "ReferenceError" in w or "TypeError" in w or "Required property" in w], warnings
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)


def test_values_that_make_no_sense_leave_the_defaults(qapp, tmp_path):
    engine, window, warnings, memory = window_with(qapp, tmp_path)
    for name, value in (("tab", "nowhere"), ("density", "xl"), ("sort", "colour"), ("inspector", "yes"), ("pins", "all")):
        memory.set(name, value)
    QMetaObject.invokeMethod(window, "restoreInterface")
    pump(qapp, 0.2)
    now = interface(window)
    assert (now["tab"], now["inspector"], now["density"], now["sort"]) == ("lib", True, "m", "name")
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)


def test_a_change_is_remembered_when_it_is_made(qapp, tmp_path):
    engine, window, warnings, memory = window_with(qapp, tmp_path)
    run(window, "sort.ai")
    run(window, "view.inspector")
    assert memory.get("sort", "") == "ai" and memory.get("inspector", True) is False
    run(window, "view.inspector")
    assert memory.get("inspector", False) is True
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)


def test_a_narrow_window_does_not_overwrite_what_was_left(qapp, tmp_path):
    engine, window, warnings, memory = window_with(qapp, tmp_path, width=800)               # its sheets start closed whatever was left open
    memory.set("inspector", True)
    run(window, "view.inspector")
    assert memory.get("inspector", None) is True
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)
