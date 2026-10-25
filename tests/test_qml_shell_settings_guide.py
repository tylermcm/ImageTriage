"""The Settings window's section list in a short window, and its link to the settings guide in Help."""
from __future__ import annotations

import pytest
from PySide6.QtCore import Q_ARG, QMetaObject, QPoint, QPointF, Qt
from PySide6.QtTest import QTest

from image_triage.qml_shell.reference_model import ReferenceModel
from image_triage.qml_shell.photos import PhotoModel
from image_triage.qml_shell.menu_model import ToastModel
from test_qml_shell import _load_main, pump, qapp  # noqa: F401  (the same helpers and application fixture)


def find(window, name):
    stack = [window.contentItem()]
    while stack:
        item = stack.pop()
        if item.objectName() == name:
            return item
        stack.extend(item.childItems())
    return None


def box(window, name):
    item = find(window, name)
    assert item is not None, name
    top = item.mapToScene(QPointF(0, 0))
    return top.x(), top.y(), item.width(), item.height()


def open_settings(qapp, tmp_path, height):
    engine, window, warnings = _load_main(qapp, tmp_path, "settingsOpen=true")
    window.setWidth(1440)
    window.setHeight(height)
    QMetaObject.invokeMethod(window, "applyStartup", Q_ARG("QVariant", 1440))
    pump(qapp, 0.9)
    return engine, window, warnings


def close(qapp, engine, window):
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)


@pytest.mark.parametrize("height", [560, 900])
def test_the_section_list_never_runs_into_the_help_block(qapp, tmp_path, height):
    engine, window, warnings = open_settings(qapp, tmp_path, height)
    _lx, ly, _lw, lh = box(window, "settingsNavList")
    _hx, hy, _hw, hh = box(window, "settingsHelp")
    assert ly + lh <= hy, (ly + lh, hy)                                    # the list ends above the help block
    link = box(window, "settingsGuideLink")
    assert hy <= link[1] and link[1] + link[3] <= hy + hh                  # the link is inside the help block, whatever its size
    if height == 560:
        assert find(window, "settingsNavList").property("contentHeight") > lh   # the list scrolls instead of overlapping
    close(qapp, engine, window)


def test_the_settings_link_opens_the_guide_in_help(qapp, tmp_path):
    engine, window, warnings = open_settings(qapp, tmp_path, 900)
    reference = engine.rootContext().contextProperty("reference")
    assert reference.view["window"] != "help"
    x, y, w, h = box(window, "settingsGuideLink")
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(int(x + w / 2), int(y + h / 2)))
    pump(qapp, 0.5)
    view = reference.view
    assert view["window"] == "help" and view["help"]["title"] == "Settings and themes"
    assert any("Profile preset:" in b.get("t", "") for b in view["help"]["blocks"])
    close(qapp, engine, window)


def test_the_guide_lists_every_setting_with_its_words_and_is_searchable(qapp):
    reference = ReferenceModel(PhotoModel(parent=qapp), ToastModel(parent=qapp), memory=True, parent=qapp)
    reference.open("help")
    reference.setTopic("settings")
    text = " ".join(b.get("t", "") for b in reference.view["help"]["blocks"])
    for label in ("Profile preset", "Delete behavior", "UI gamma", "Theme", "Show hidden folders", "Advance after Accept or Reject"):
        assert label + ":" in text, label
    assert [b["t"] for b in reference.view["help"]["blocks"] if b["kind"] == "h"][:3] == ["General", "Appearance", "Browsing & editor"]
    reference.setQuery("gamma")
    topics = {t["id"]: t["n"] for t in reference.view["help"]["topics"] if not t["sep"]}
    assert topics.get("settings") not in ("", None)                        # the search finds the topic through the settings in it
