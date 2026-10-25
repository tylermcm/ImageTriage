"""The collection dialog's Mark: Icon first and the default, the last icon tile, and the colour picker behind More…"""
from __future__ import annotations

import pytest
from PySide6.QtCore import Q_ARG, QMetaObject, QObject
from PySide6.QtGui import QColor

from image_triage.qml_shell.collections_model import ICONS
from test_qml_shell import _load_main, pump, qapp  # noqa: F401  (the same helpers and application fixture)


def find(window, name):
    stack = [window.contentItem()]
    while stack:
        item = stack.pop()
        if item.objectName() == name:
            return item
        stack.extend(item.childItems())
    return None


def open_dialog(qapp, tmp_path, startup):
    engine, window, warnings = _load_main(qapp, tmp_path, startup)
    window.setWidth(1440)
    window.setHeight(900)
    QMetaObject.invokeMethod(window, "applyStartup", Q_ARG("QVariant", 1440))
    pump(qapp, 0.9)
    return engine, window, warnings, window.findChild(QObject, "collectionDialog")


def close(qapp, engine, window):
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)


def test_a_new_collection_opens_on_icons_with_one_chosen(qapp, tmp_path):
    engine, window, warnings, dialog = open_dialog(qapp, tmp_path, "tab=coll;collDlg=new")
    assert dialog.property("tab") == 0 and dialog.property("icon") == "mountain"      # Icon is the first tab, and the one it opens on
    assert len(dialog.property("icons")) == len(ICONS) == 24                          # the last tile is an icon, not a "+"
    assert dialog.property("icons")[-1]["key"] == "pin"
    close(qapp, engine, window)


def test_the_color_tab_clears_the_icon_and_the_icon_tab_brings_one_back(qapp, tmp_path):
    engine, window, warnings, dialog = open_dialog(qapp, tmp_path, "tab=coll;collDlg=new")
    seg = find(window, "collMarkTabs")
    QMetaObject.invokeMethod(seg, "activated", Q_ARG(int, 1))
    pump(qapp, 0.3)
    assert dialog.property("tab") == 1 and dialog.property("icon") == ""
    QMetaObject.invokeMethod(seg, "activated", Q_ARG(int, 0))
    pump(qapp, 0.3)
    assert dialog.property("tab") == 0 and dialog.property("icon") == "mountain"
    close(qapp, engine, window)


def test_the_hook_and_an_existing_colour_only_collection_open_on_color(qapp, tmp_path):
    engine, window, warnings, dialog = open_dialog(qapp, tmp_path, "tab=coll;collDlgTab=color;collDlg=new")
    assert dialog.property("tab") == 1
    close(qapp, engine, window)
    engine, window, warnings, dialog = open_dialog(qapp, tmp_path, "tab=coll;collDlgId=c1;collDlg=edit")           # c1: a colour, no icon
    assert dialog.property("tab") == 1 and dialog.property("icon") == "" and dialog.property("color") == "#4c8dff"
    close(qapp, engine, window)
    engine, window, warnings, dialog = open_dialog(qapp, tmp_path, "tab=coll;collDlgId=c3;collDlg=edit")           # c3: an icon
    assert dialog.property("tab") == 0 and dialog.property("icon") == "mountain"
    close(qapp, engine, window)


def test_more_opens_the_system_colour_picker_and_its_colour_becomes_the_mark(qapp, tmp_path):
    engine, window, warnings, dialog = open_dialog(qapp, tmp_path, "tab=coll;collDlgTab=color;collDlg=new")
    assert find(window, "collMoreColors") is not None
    picker = window.findChild(QObject, "collColorDialog")
    assert picker is not None and not dialog.property("customColor")
    picker.setProperty("selectedColor", QColor("#12ab34"))
    QMetaObject.invokeMethod(picker, "accepted")                                    # what pressing OK in the picker does
    pump(qapp, 0.3)
    assert dialog.property("color") == "#12ab34" and dialog.property("customColor")
    assert "Custom color" in dialog.property("previewText")
    close(qapp, engine, window)
    assert not [w for w in warnings if "ReferenceError" in w or "TypeError" in w or "Required property" in w], warnings
