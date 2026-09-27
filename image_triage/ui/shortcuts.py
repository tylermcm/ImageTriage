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
    # AI
    ("open_ai_workflow_center", "AI", "Ctrl+Shift+W", "AI Workflow Center..."),
    ("quick_rerank_ai_culling", "AI", "Ctrl+Shift+Y", "Quick Rerank"),
    ("next_ai_pick", "AI", "Ctrl+Alt+N", "Next AI Top Pick"),
    ("compare_ai_group", "AI", "Ctrl+Alt+G", "Compare Current AI Group"),
    # Workflow / export
    ("handoff_builder", "Workflow", "Ctrl+Alt+H", "Deliver / Handoff Builder..."),
    ("send_to_editor_pipeline", "Workflow", "Ctrl+Alt+E", "Send To Editor..."),
    ("best_of_set_auto_assembly", "Workflow", "Ctrl+Alt+B", "Best-of-Set Auto Assembly..."),
    # Workspace
    ("save_workspace_preset", "Workspace", "Ctrl+Alt+S", "Save Current Workspace Preset..."),
    # Review (unified from the window's own Keyboard Shortcuts dialog and
    # grid/preview/details view's hardcoded review keys, WI-3.2)
    ("open_preview", "Review", "", "Open Preview"),
    ("accept_selection", "Review", "W", "Mark Winner"),
    ("reject_selection", "Review", "X", "Reject Selection"),
    ("keep_selection", "Review", "", "Move Selection To _keep"),
    ("move_selection", "Review", "", "Move Selection..."),
    ("delete_selection", "Review", "", "Delete Selection"),
    ("cycle_burst_previous", "Review", "[", "Cycle Burst: Previous"),
    ("cycle_burst_next", "Review", "]", "Cycle Burst: Next"),
    ("keep_at_cursor", "Review", "K", "Keep Photo At Cursor"),
    ("move_at_cursor", "Review", "M", "Move Photo At Cursor"),
    ("tag_at_cursor", "Review", "T", "Tag Photo At Cursor"),
    ("adapter_label_hero", "Review", "1", "Adapter Label: Hero"),
    ("adapter_label_strong", "Review", "2", "Adapter Label: Strong"),
    ("adapter_label_maybe", "Review", "3", "Adapter Label: Maybe"),
    ("adapter_label_weak", "Review", "4", "Adapter Label: Weak"),
    ("adapter_label_reject", "Review", "5", "Adapter Label: Reject"),
    # Workflow
    ("share_to_phone", "Workflow", "Ctrl+Alt+P", "Send to PocketDrop"),
    # Workspace
    ("open_command_palette", "Workspace", "Ctrl+K", "Command Palette..."),
)


_SHORTCUT_SETTINGS_PREFIX = "shortcuts"


def _shortcut_settings_key(attr_name: str) -> str:
    return f"{_SHORTCUT_SETTINGS_PREFIX}/{attr_name}"


def _resolve_settings(settings: QSettings | None) -> QSettings:
    if settings is not None:
        return settings
    # Deferred so this module stays loadable standalone (see module docstring).
    from ..app_identity import user_settings

    return user_settings()


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


def effective_shortcuts(
    names: Iterable[str],
    overrides: Mapping[str, str] | None = None,
    *,
    settings: QSettings | None = None,
) -> dict[str, str]:
    """Effective (override-or-default) shortcut text for registry entries
    that have no `MainWindowActions` attribute — the grid/preview/details
    view review keys `apply_shortcut_overrides` silently skips."""

    if overrides is None:
        overrides = load_shortcut_overrides(settings)
    defaults = {attr_name: default for attr_name, _category, default, _display in SHORTCUT_REGISTRY}
    return {name: overrides.get(name, defaults.get(name, "")) for name in names}


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
    "effective_shortcuts",
    "load_shortcut_overrides",
    "save_shortcut_overrides",
)
