"""Characterization of per-folder view state, restore-position, and the
unified shortcut registry (WI-0.5, unified in WI-3.2)."""
from __future__ import annotations

import dataclasses
import json

from PySide6.QtCore import QSettings
from PySide6.QtGui import QKeySequence

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


def test_no_two_actions_share_a_default_shortcut(main_window) -> None:
    from PySide6.QtGui import QAction

    owners: dict[str, list[str]] = {}
    for field in dataclasses.fields(main_window.actions):
        name, action = field.name, getattr(main_window.actions, field.name)
        if not isinstance(action, QAction) or action.shortcut().isEmpty():
            continue
        owners.setdefault(action.shortcut().toString(), []).append(name)
    duplicates = {key: names for key, names in owners.items() if len(names) > 1}

    assert duplicates == {}


def test_no_two_registry_defaults_collide() -> None:
    defaults: dict[str, list[str]] = {}
    for attr, _category, default, _display in SHORTCUT_REGISTRY:
        if default:
            defaults.setdefault(default, []).append(attr)

    assert {k: v for k, v in defaults.items() if len(v) > 1} == {}


def test_pocketdrop_owns_ctrl_alt_p_and_next_ai_pick_moved(main_window) -> None:
    assert main_window.actions.share_to_phone.shortcut().toString() == "Ctrl+Alt+P"
    assert main_window.actions.next_ai_pick.shortcut().toString() == "Ctrl+Alt+N"
    assert dict((a, d) for a, _c, d, _n in SHORTCUT_REGISTRY)["next_ai_pick"] == "Ctrl+Alt+N"


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


def test_there_is_now_one_store_for_every_surface(main_window) -> None:
    """WI-3.2: the window's own JSON-blob shortcut store is gone. A rebind
    saved to the single registry store propagates to the QAction *and* to
    the grid/details/preview review-key surfaces that used to read a
    hardcoded literal."""
    try:
        save_shortcut_overrides({"keep_at_cursor": "Ctrl+Alt+F10"})
        main_window._apply_shortcut_overrides()

        assert load_shortcut_overrides().get("keep_at_cursor") == "Ctrl+Alt+F10"
        expected = QKeySequence("Ctrl+Alt+F10")
        assert main_window.grid._review_key_shortcuts["keep_at_cursor"] == expected
        assert main_window.details_view.table._review_key_shortcuts["keep_at_cursor"] == expected
        assert main_window.preview._review_key_shortcuts["keep_at_cursor"] == expected
    finally:
        save_shortcut_overrides({})
        main_window._apply_shortcut_overrides()


def test_the_registry_store_now_shares_the_main_settings_identity(main_window) -> None:
    from image_triage.app_identity import user_settings

    settings = user_settings()
    assert settings.organizationName() == main_window._settings.organizationName()
    assert settings.applicationName() == main_window._settings.applicationName()
