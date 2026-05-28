"""Customizable keybind registry and override persistence.

Kept dependency-light on purpose: this module deliberately avoids importing
anything from the rest of image_triage so the registry + load/save helpers can
be exercised in unit tests without standing up the full host environment.

`apply_shortcut_overrides` is the one place that touches Qt — it walks the
registry and calls setShortcut on each action.
"""

from __future__ import annotations

from typing import Iterable, Mapping

from PySide6.QtCore import QSettings
from PySide6.QtGui import QKeySequence


# Every host action that ships with a keyboard shortcut is registered here so
# the Settings dialog can list it and the user can rebind it. Format:
#   (attr_name, category, default_shortcut, display_name)
SHORTCUT_REGISTRY: tuple[tuple[str, str, str, str], ...] = (
    # File
    ("open_folder", "File", "Ctrl+O", "Open Folder..."),
    ("refresh_folder", "File", "F5", "Refresh Folder"),
    ("new_folder", "File", "Ctrl+Shift+N", "New Folder..."),
    ("workflow_settings", "File", "Ctrl+,", "Settings..."),
    # Edit
    ("undo", "Edit", "Ctrl+Z", "Undo"),
    ("rename_selection", "Edit", "F2", "Rename Image..."),
    ("batch_rename_selection", "Edit", "Ctrl+Shift+R", "Batch Rename..."),
    ("batch_resize_selection", "Edit", "Ctrl+Shift+E", "Batch Resize..."),
    ("batch_convert_selection", "Edit", "Ctrl+Shift+C", "Batch Convert..."),
    # View
    ("grid_view", "View", "Ctrl+1", "Grid View"),
    ("details_view", "View", "Ctrl+2", "Details View"),
    ("zen_mode", "View", "F11", "Zen Mode"),
    ("clear_filters", "View", "Ctrl+Shift+X", "Clear Filters"),
    # Review
    ("compare_mode", "Review", "C", "Compare"),
    ("winner_ladder_mode", "Review", "Ctrl+Alt+W", "Winner Ladder"),
    ("assign_review_round_first_pass", "Review", "Alt+1", "Mark First Pass Rejects"),
    ("assign_review_round_second_pass", "Review", "Alt+2", "Mark Second Pass Keepers"),
    ("assign_review_round_third_pass", "Review", "Alt+3", "Mark Third Pass Finalists"),
    ("assign_review_round_hero", "Review", "Alt+4", "Mark Final Hero Selects"),
    ("clear_review_round", "Review", "Alt+0", "Clear Review Round"),
    # AI
    ("open_ai_workflow_center", "AI", "Ctrl+Shift+W", "AI Workflow Center..."),
    ("open_ai_data_selection", "AI", "Ctrl+Shift+L", "Prepare Adapter Ratings"),
    ("quick_rerank_ai_culling", "AI", "Ctrl+Shift+Y", "Quick Rerank"),
    ("next_ai_pick", "AI", "Ctrl+Alt+P", "Next AI Top Pick"),
    ("compare_ai_group", "AI", "Ctrl+Alt+G", "Compare Current AI Group"),
    ("review_ai_disagreements", "AI", "Ctrl+Alt+D", "Review AI Disagreements"),
    ("taste_calibration_wizard", "AI", "Ctrl+Alt+K", "Taste Calibration Wizard..."),
    # Workflow / export
    ("handoff_builder", "Workflow", "Ctrl+Alt+H", "Deliver / Handoff Builder..."),
    ("send_to_editor_pipeline", "Workflow", "Ctrl+Alt+E", "Send To Editor..."),
    ("best_of_set_auto_assembly", "Workflow", "Ctrl+Alt+B", "Best-of-Set Auto Assembly..."),
    # Workspace
    ("save_workspace_preset", "Workspace", "Ctrl+Alt+S", "Save Current Workspace Preset..."),
)


_SHORTCUT_SETTINGS_PREFIX = "shortcuts"
_ORG_NAME = "ImageTriage"
_APP_NAME = "ImageTriage"


def _shortcut_settings_key(attr_name: str) -> str:
    return f"{_SHORTCUT_SETTINGS_PREFIX}/{attr_name}"


def _resolve_settings(settings: QSettings | None) -> QSettings:
    return settings if settings is not None else QSettings(_ORG_NAME, _APP_NAME)


def load_shortcut_overrides(settings: QSettings | None = None) -> dict[str, str]:
    """Return user-customized shortcuts keyed by action attribute name.

    Entries equal to their registered default are excluded so the caller can
    treat the result as a sparse override map.
    """

    store = _resolve_settings(settings)
    overrides: dict[str, str] = {}
    for attr_name, _category, default, _display in SHORTCUT_REGISTRY:
        raw = store.value(_shortcut_settings_key(attr_name), default)
        if raw is None:
            continue
        text = str(raw).strip()
        if text and text != default:
            overrides[attr_name] = text
    return overrides


def apply_shortcut_overrides(
    actions: object,
    overrides: Mapping[str, str] | None = None,
    *,
    settings: QSettings | None = None,
) -> None:
    """Apply user keybind overrides to a built MainWindowActions instance.

    Actions named in the registry but absent from MainWindowActions are silently
    skipped so the registry can be edited without breaking the call site.
    """

    if overrides is None:
        overrides = load_shortcut_overrides(settings)
    for attr_name, _category, default, _display in SHORTCUT_REGISTRY:
        action = getattr(actions, attr_name, None)
        if action is None:
            continue
        target = overrides.get(attr_name, default)
        action.setShortcut(QKeySequence(target))


def save_shortcut_overrides(
    overrides: Mapping[str, str],
    *,
    settings: QSettings | None = None,
) -> None:
    """Persist shortcut overrides; pass an empty string to reset to default.

    Keys not present in `overrides` are wiped from settings — the registry owns
    the universe and defaults will be re-applied at load time.
    """

    store = _resolve_settings(settings)
    known_attrs = {name for name, _c, _d, _n in SHORTCUT_REGISTRY}
    for attr_name in known_attrs:
        key = _shortcut_settings_key(attr_name)
        value = overrides.get(attr_name)
        if value is None or not str(value).strip():
            store.remove(key)
        else:
            store.setValue(key, str(value).strip())


__all__ = (
    "SHORTCUT_REGISTRY",
    "apply_shortcut_overrides",
    "load_shortcut_overrides",
    "save_shortcut_overrides",
)
