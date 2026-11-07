"""Characterization of per-folder view state, restore-position, and the
unified shortcut registry (WI-0.5, unified in WI-3.2)."""
from __future__ import annotations

import dataclasses
import json

from PySide6.QtCore import QSettings
from PySide6.QtGui import QKeySequence

from image_triage.shortcuts import (
    SHORTCUT_REGISTRY,
    load_shortcut_overrides,
    save_shortcut_overrides,
)


def _set_raw(window, key, value) -> None:
    window._settings.setValue(key, value)


# ---- folder view state --------------------------------------------------------





















def test_no_two_registry_defaults_collide() -> None:
    defaults: dict[str, list[str]] = {}
    for attr, _category, default, _display in SHORTCUT_REGISTRY:
        if default:
            defaults.setdefault(default, []).append(attr)

    assert {k: v for k, v in defaults.items() if len(v) > 1} == {}




def _registry_attr_with_default():
    return next((attr, default) for attr, _c, default, _d in SHORTCUT_REGISTRY if default)


def test_registry_store_is_sparse_and_round_trips(tmp_path) -> None:
    settings = QSettings(str(tmp_path / "keys.ini"), QSettings.Format.IniFormat)
    attr, default = _registry_attr_with_default()

    assert load_shortcut_overrides(settings) == {}
    save_shortcut_overrides({attr: "Ctrl+Alt+F12"}, settings=settings)
    assert load_shortcut_overrides(settings) == {attr: "Ctrl+Alt+F12"}
    save_shortcut_overrides({attr: default}, settings=settings)
    assert load_shortcut_overrides(settings) == {}, "a value equal to the default is not an override"


def test_registry_store_blank_value_resets_to_default(tmp_path) -> None:
    settings = QSettings(str(tmp_path / "keys.ini"), QSettings.Format.IniFormat)
    attr, _default = _registry_attr_with_default()
    save_shortcut_overrides({attr: "Ctrl+Alt+F11"}, settings=settings)
    save_shortcut_overrides({attr: ""}, settings=settings)

    assert load_shortcut_overrides(settings) == {}
