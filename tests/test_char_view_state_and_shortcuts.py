"""Characterization of per-folder view state, restore-position, and both shortcut stores (WI-0.5)."""
from __future__ import annotations

import json

from PySide6.QtCore import QSettings

from image_triage.keyboard_mapping import shortcut_conflicts
from image_triage.ui.shortcuts import (
    SHORTCUT_REGISTRY,
    load_shortcut_overrides,
    save_shortcut_overrides,
)
from image_triage.window import MainWindow, _memory_path_key
from tests.harness import make_jpegs, open_folder


def _set_raw(window, key, value) -> None:
    window._settings.setValue(key, value)


# ---- folder view state --------------------------------------------------------

def test_view_state_loader_survives_garbage(main_window) -> None:
    for raw in ("", "not json", "[1, 2]", json.dumps({"a": "not a dict"})):
        _set_raw(main_window, MainWindow.FOLDER_VIEW_STATE_KEY, raw)
        assert main_window._load_folder_view_states() == {}


def test_view_state_loader_sanitises_fields(main_window) -> None:
    payload = {
        "c:/photos": {"sort": "Filename", "scroll": -50},
        "c:/other": {"sort": "no-such-sort", "scroll": "abc"},
    }
    _set_raw(main_window, MainWindow.FOLDER_VIEW_STATE_KEY, json.dumps(payload))

    states = main_window._load_folder_view_states()

    assert states["c:/photos"] == {"sort": "Filename", "scroll": 0}
    assert "c:/other" not in states


def test_remember_saves_sort_scroll_and_current_photo(main_window, tmp_path) -> None:
    (a,) = make_jpegs(tmp_path, ["a.jpg"])
    main_window._folder_view_states = {}
    open_folder(main_window, tmp_path, 1)

    main_window._remember_current_folder_view_state()

    saved = json.loads(main_window._settings.value(MainWindow.FOLDER_VIEW_STATE_KEY, "", str))
    state = saved[_memory_path_key(str(tmp_path))]
    assert set(state) == {"sort", "scroll", "current"}
    assert state["current"] == a


def test_view_state_history_is_capped_at_120_folders(main_window, tmp_path) -> None:
    make_jpegs(tmp_path, ["a.jpg"])
    open_folder(main_window, tmp_path, 1)
    main_window._folder_view_states = {f"folder-{n}": {"sort": "Filename"} for n in range(130)}

    main_window._remember_current_folder_view_state()

    assert len(main_window._folder_view_states) == 120
    assert _memory_path_key(str(tmp_path)) in main_window._folder_view_states


def test_restore_position_on_targets_the_saved_photo(main_window, tmp_path) -> None:
    main_window._restore_folder_position_enabled = True
    main_window._pending_folder_focus_path = ""
    main_window._folder_view_states = {
        _memory_path_key(str(tmp_path)): {"sort": "Filename", "scroll": 300, "current": str(tmp_path / "b.jpg")}
    }

    main_window._apply_folder_view_state(str(tmp_path))

    assert main_window._pending_folder_focus_path.endswith("b.jpg")
    assert main_window._pending_focus_scroll_top is True
    assert main_window._pending_folder_scroll_value is None


def test_restore_position_off_restores_nothing(main_window, tmp_path) -> None:
    main_window._restore_folder_position_enabled = False
    main_window._pending_folder_focus_path = ""
    main_window._folder_view_states = {
        _memory_path_key(str(tmp_path)): {"sort": "Filename", "scroll": 300, "current": str(tmp_path / "b.jpg")}
    }

    main_window._apply_folder_view_state(str(tmp_path))

    assert main_window._pending_folder_focus_path == ""
    assert main_window._pending_folder_scroll_value is None


def test_without_a_saved_photo_the_scroll_offset_is_used(main_window, tmp_path) -> None:
    main_window._restore_folder_position_enabled = True
    main_window._pending_folder_focus_path = ""
    main_window._folder_view_states = {_memory_path_key(str(tmp_path)): {"sort": "Filename", "scroll": 300}}

    main_window._apply_folder_view_state(str(tmp_path))

    assert main_window._pending_folder_scroll_value == 300


def test_column_count_is_program_wide_not_restored_per_folder(main_window, tmp_path) -> None:
    before = main_window.grid._columns
    main_window._restore_folder_position_enabled = True
    main_window._folder_view_states = {_memory_path_key(str(tmp_path)): {"sort": "Filename", "columns": 7}}

    main_window._apply_folder_view_state(str(tmp_path))

    assert main_window.grid._columns == before


def test_restore_position_setting_defaults_on_and_persists(main_window) -> None:
    assert main_window._settings.value(MainWindow.RESTORE_FOLDER_POSITION_KEY, True, bool) is True
    main_window._settings.setValue(MainWindow.RESTORE_FOLDER_POSITION_KEY, False)
    assert main_window._settings.value(MainWindow.RESTORE_FOLDER_POSITION_KEY, True, bool) is False


# ---- shortcut store A: MainWindow (one JSON blob in the main QSettings) --------------

def test_window_overrides_round_trip_and_normalise(main_window) -> None:
    main_window._shortcut_overrides = {"x_binding": "ctrl+shift+k", "blank": ""}
    main_window._save_shortcut_overrides()

    saved = json.loads(main_window._settings.value(MainWindow.SHORTCUT_OVERRIDES_KEY, "", str))
    assert saved == {"x_binding": "Ctrl+Shift+K"}, "blank entries are dropped and text is normalised"
    assert main_window._load_shortcut_overrides() == {"x_binding": "Ctrl+Shift+K"}


def test_window_overrides_loader_survives_garbage(main_window) -> None:
    for raw in ("", "nope", "[]", "5"):
        main_window._settings.setValue(MainWindow.SHORTCUT_OVERRIDES_KEY, raw)
        assert main_window._load_shortcut_overrides() == {}


def test_window_bindings_have_a_known_conflict_on_ctrl_alt_p(main_window) -> None:
    # Audit finding (WI-1.1): two commands share this default key. Pinned so the fix is visible.
    conflicts = shortcut_conflicts(main_window._shortcut_bindings())

    assert "Ctrl+Alt+P" in conflicts and len(conflicts["Ctrl+Alt+P"]) >= 2


# ---- shortcut store B: ui/shortcuts.py (separate org, one key per action) -----------

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


def test_the_two_stores_are_independent(main_window, tmp_path) -> None:
    attr, _default = _registry_attr_with_default()
    save_shortcut_overrides({attr: "Ctrl+Alt+F10"})

    assert main_window._load_shortcut_overrides().get(attr) is None
    assert load_shortcut_overrides().get(attr) == "Ctrl+Alt+F10"
    save_shortcut_overrides({})


def test_the_registry_store_uses_a_different_settings_organisation(main_window) -> None:
    from image_triage.ui import shortcuts

    assert shortcuts._ORG_NAME != main_window._settings.organizationName()
