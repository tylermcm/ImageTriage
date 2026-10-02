"""Shared machinery for the A2 stage-2 characterization tests.

Audit finding A2 split four long *declarative* builders into section helpers:

* ``build_main_window_actions``   (``image_triage/ui/actions.py``)
* ``build_main_menu_bar``         (``image_triage/ui/menus.py``)
* ``CommandPaletteController.build_commands``  (``image_triage/command_palette_controller.py``)
* ``build_parser``                (``image_triage/photo_terminal/cli.py``)

Each of them is user-visible *in order* (menu order, palette order, QAction
creation order and the argparse tree), so the refactor had to change nothing
observable. This module turns each builder's output into plain text lines that
the four ``test_*_snapshot.py`` modules pin by SHA-256. It is deliberately a
description of what the builders produce today, not a statement that any of it
is right.

What a "dump" contains
----------------------
* **actions** - every ``MainWindowActions`` field in declaration order (dict
  fields in key order), every QAction's text/shortcuts/checkable/... and which
  slot each action's ``triggered`` / ``toggled`` signal reaches, the
  action-group membership, and the QAction/QActionGroup *creation order*.
* **menus** - the complete menu tree, with the ``MainWindowActions`` field each
  menu entry stands for (inline ``addAction(text, slot)`` entries are probed
  instead), for several combinations of the optional menus / dock actions.
* **palette** - every command's id/title/subtitle/section/shortcut/keywords and
  what its callback does, for a grid of window states.
* **cli** - the ``photoedit`` argparse tree structure plus ``parse_args``
  results for a representative argv list per subcommand.

Slots are observed by running the builders against :class:`StubWindow`, a
``QMainWindow`` whose ``_private`` attributes are recording callables. A slot
that is wired to the wrong handler therefore shows up as a different recorded
call, with no real handler ever running.
"""
from __future__ import annotations

import argparse
import contextlib
import dataclasses
import enum
import hashlib
import json
import os
import tempfile
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import QApplication, QMainWindow, QMenu, QMenuBar

UPDATE_ENV = "UPDATE_BUILDER_SNAPSHOTS"

Digest = tuple[str, int]


# --- Digests and the shared "regenerate deliberately" convention --------------
def digest_lines(lines: list[str]) -> Digest:
    text = "\n".join(lines) + "\n"
    return hashlib.sha256(text.encode("utf-8")).hexdigest(), len(text)


def format_table(name: str, actual: Mapping[str, Digest]) -> str:
    rows = [f'    "{case}": ("{digest}", {length}),' for case, (digest, length) in actual.items()]
    return f"{name}: dict[str, tuple[str, int]] = {{\n" + "\n".join(rows) + "\n}\n"


def assert_matches_golden(
    *,
    module: str,
    table_name: str,
    golden: Mapping[str, Digest],
    actual: Mapping[str, Digest],
    what: str,
    dumps: Mapping[str, list[str]] | None = None,
) -> None:
    """Compare ``actual`` with the pinned table, or - with UPDATE_BUILDER_SNAPSHOTS=1 -
    print the new table (and write it to the temp dir) and skip. When ``dumps``
    (case -> text lines) is given, a failure also writes each mismatching case's
    full text to ``<temp>/image_triage_<module>_actual/`` so it can be diffed
    against a run of the code you are comparing with."""
    import pytest

    if os.environ.get(UPDATE_ENV) == "1":
        table = format_table(table_name, actual)
        target = Path(tempfile.gettempdir()) / f"image_triage_{module}.txt"
        target.write_text(table, encoding="utf-8")
        print("\n" + table)
        pytest.skip(f"{UPDATE_ENV}=1: new digest table printed and written to {target}; paste it over {table_name}")

    problems: list[str] = []
    written: list[str] = []
    for case in sorted(set(golden) | set(actual)):
        want, got = golden.get(case), actual.get(case)
        if want is None:
            problems.append(f"  {case}: no pinned digest (new case); got {got}")
        elif got is None:
            problems.append(f"  {case}: pinned but no longer generated")
        elif want != got:
            problems.append(f"  {case}: expected {want[0][:16]}.../{want[1]} chars, got {got[0][:16]}.../{got[1]} chars")
        if dumps is not None and case in dumps and want != got:
            target_dir = Path(tempfile.gettempdir()) / f"image_triage_{module}_actual"
            target_dir.mkdir(exist_ok=True)
            safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in case)
            (target_dir / f"{safe}.txt").write_text("\n".join(dumps[case]) + "\n", encoding="utf-8")
            written.append(str(target_dir / f"{safe}.txt"))
    if written:
        problems.append("Full text of the mismatching cases written to:\n" + "\n".join(f"    {path}" for path in written))
    assert not problems, (
        f"{what} changed. This is a CHARACTERIZATION test: it pins the exact observable output of a builder\n"
        "that was split into section helpers (audit finding A2), so restructuring it cannot reorder or alter it.\n"
        "If the change is intentional, regenerate the table deliberately:\n"
        f"  set {UPDATE_ENV}=1 and run: pythonw3.13.exe scripts/run313.py regen.log pytest -q -s tests/{module}.py\n"
        f"then paste the printed table over {table_name} in tests/{module}.py and review the diff.\n"
        "Differences:\n" + "\n".join(problems)
    )


def ensure_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def event_loop_turn(ms: int = 30) -> None:
    """A real event-loop turn: ``processEvents`` alone never runs ``deleteLater``."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _enum_name(value: object) -> str:
    return getattr(value, "name", None) or str(value)


def _key_label(key: object) -> str:
    if isinstance(key, enum.Enum):
        return f"{type(key).__name__}.{key.name}"
    return repr(key)


# --- A recording stand-in for MainWindow --------------------------------------
class _Recorder:
    """Callable that logs ``(name, args, kwargs)`` and does nothing else."""

    def __init__(self, log: list, name: str) -> None:
        self._log = log
        self._name = name

    def __call__(self, *args, **kwargs):
        self._log.append((self._name, args, kwargs))
        return None


class StubWindow(QMainWindow):
    """A real ``QMainWindow`` (so QActions/menus can be parented to it) whose
    private attributes are recorders.

    ``window._choose_folder`` is a recorder named ``_choose_folder``; calling it
    appends ``("_choose_folder", args, kwargs)`` to ``window.calls``. Public
    names resolve normally, so ``close``/``style``/``menuBar`` are the real
    thing (``close`` is recorded instead of executed)."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list = []
        self.workspace_docks = None

    def __getattr__(self, name: str):
        if name.startswith("_") and not name.startswith("__"):
            return _Recorder(self.__dict__.get("calls", []), name)
        raise AttributeError(name)

    def close(self) -> bool:  # type: ignore[override]
        self.calls.append(("close", (), {}))
        return True


@contextlib.contextmanager
def stub_window() -> Iterator[StubWindow]:
    ensure_app()
    window = StubWindow()
    try:
        yield window
    finally:
        window.deleteLater()
        event_loop_turn()


# --- Actions -------------------------------------------------------------------
def action_fields(actions) -> Iterator[tuple[str, QAction]]:
    """``(label, QAction)`` for every field, in declaration order; dict-valued
    fields yield their entries in the dict's own key order."""
    from image_triage.ui.actions import MainWindowActions

    for field in dataclasses.fields(MainWindowActions):
        value = getattr(actions, field.name)
        if isinstance(value, dict):
            for key, action in value.items():
                yield f"{field.name}[{_key_label(key)}]", action
        else:
            yield field.name, value


def action_label_map(actions) -> dict[int, str]:
    return {id(action): label for label, action in action_fields(actions)}


def _pixmap_digest(action: QAction) -> str | None:
    icon = action.icon()
    if icon.isNull():
        return None
    image = icon.pixmap(16, 16).toImage()
    return hashlib.sha256(bytes(image.constBits())).hexdigest()[:16]


def describe_action(action: QAction, window, groups: list[QActionGroup], *, pixel_icons: bool = False) -> dict:
    group = action.actionGroup()
    record = {
        "text": action.text(),
        "base_text_property": action.property("imageTriageBaseText"),
        "shortcuts": [seq.toString(QKeySequence.SequenceFormat.PortableText) for seq in action.shortcuts()],
        "shortcut_native": action.shortcut().toString(QKeySequence.SequenceFormat.NativeText),
        "shortcut_context": _enum_name(action.shortcutContext()),
        "checkable": action.isCheckable(),
        "checked": action.isChecked(),
        "enabled": action.isEnabled(),
        "visible": action.isVisible(),
        "tooltip": action.toolTip(),
        "status_tip": action.statusTip(),
        "whats_this": action.whatsThis(),
        "object_name": action.objectName(),
        "menu_role": _enum_name(action.menuRole()),
        "has_icon": not action.icon().isNull(),
        "auto_repeat": action.autoRepeat(),
        "priority": _enum_name(action.priority()),
        "icon_visible_in_menu": action.isIconVisibleInMenu(),
        "shortcut_visible_in_context_menu": action.isShortcutVisibleInContextMenu(),
        "data": repr(action.data()),
        "group": None if group is None else {"index": groups.index(group), "exclusive": group.isExclusive()},
        "parent_is_window": action.parent() is window,
    }
    if pixel_icons:
        record["icon_pixels"] = _pixmap_digest(action)
    return record


def probe_slots(window: StubWindow, action: QAction) -> dict[str, list]:
    """Emit the action's signals (without changing its state) and report what
    the stub window recorded. Non-checkable actions wire ``triggered``;
    checkable ones wire ``toggled`` - both are probed so a mix-up shows."""
    result: dict[str, list] = {}
    window.calls.clear()
    action.triggered.emit()
    result["triggered"] = list(window.calls)
    window.calls.clear()
    if action.isCheckable():
        action.toggled.emit(True)
        result["toggled"] = list(window.calls)
        window.calls.clear()
    return result


def actions_snapshot_lines(window, actions, *, probe: bool, new_children: list | None = None, pixel_icons: bool = False) -> list[str]:
    """Plain-text description of a ``MainWindowActions``.

    ``new_children`` is the list of the window's children that the builder
    created (in creation order); when given, the creation order is dumped too."""
    groups = [child for child in (new_children if new_children is not None else window.children()) if isinstance(child, QActionGroup)]
    labels = action_label_map(actions)
    lines = [f"actions: {sum(1 for _ in action_fields(actions))} entries"]
    for label, action in action_fields(actions):
        record = describe_action(action, window, groups, pixel_icons=pixel_icons)
        line = f"{label} {json.dumps(record)}"
        if probe:
            line += " slots=" + repr(probe_slots(window, action))
        lines.append(line)
    from image_triage.ui.actions import MainWindowActions

    for field in dataclasses.fields(MainWindowActions):
        value = getattr(actions, field.name)
        if isinstance(value, dict):
            lines.append(f"dict {field.name}: keys={[_key_label(key) for key in value]}")
    if new_children is not None:
        lines.append("creation order:")
        group_index = {id(group): index for index, group in enumerate(groups)}
        for position, child in enumerate(new_children):
            if isinstance(child, QAction):
                lines.append(f"  {position} QAction {labels.get(id(child), '?? unlabelled: ' + child.text())}")
            elif isinstance(child, QActionGroup):
                lines.append(f"  {position} QActionGroup #{group_index[id(child)]} exclusive={child.isExclusive()}")
            else:
                lines.append(f"  {position} {type(child).__name__}")
    return lines


def build_stub_actions(window: StubWindow):
    """Run the real builder against the stub window; returns ``(actions, created_children)``."""
    from image_triage.ui.actions import build_main_window_actions

    before = len(window.children())
    actions = build_main_window_actions(window)
    return actions, window.children()[before:]


def stub_actions_snapshot() -> list[str]:
    with stub_window() as window:
        actions, created = build_stub_actions(window)
        return actions_snapshot_lines(window, actions, probe=True, new_children=created)


def add_ai_state_actions(window: QMainWindow, actions) -> None:
    """What ``RecordsViewController.build_record_filter_actions`` adds after the
    builder ran; the AI menu reads these."""
    from image_triage.filtering import AIStateFilter

    group = QActionGroup(window)
    group.setExclusive(True)
    for mode in AIStateFilter:
        action = QAction(mode.value, window)
        action.setCheckable(True)
        group.addAction(action)
        actions.ai_state_actions[mode] = action


# --- Menus ---------------------------------------------------------------------
def menu_snapshot_lines(menu_or_bar, label_map: dict[int, str], window, *, probe: bool, depth: int = 0) -> list[str]:
    lines: list[str] = []
    pad = "  " * depth
    for action in menu_or_bar.actions():
        submenu = action.menu()
        if action.isSeparator():
            lines.append(f"{pad}---" + (f" section {action.text()!r}" if action.text() else ""))
        elif submenu is not None:
            lines.append(
                f"{pad}> menu title={submenu.title()!r} object={submenu.objectName()!r} items={len(submenu.actions())}"
                f" owner={'bar' if isinstance(menu_or_bar, QMenuBar) else 'menu'}"
            )
            lines.extend(menu_snapshot_lines(submenu, label_map, window, probe=probe, depth=depth + 1))
        else:
            label = label_map.get(id(action))
            shortcuts = [seq.toString(QKeySequence.SequenceFormat.PortableText) for seq in action.shortcuts()]
            line = (
                f"{pad}* {label or 'INLINE'} text={action.text()!r} shortcuts={shortcuts}"
                f" checkable={action.isCheckable()} checked={action.isChecked()}"
                f" enabled={action.isEnabled()} visible={action.isVisible()}"
            )
            if label is None and probe:
                line += " slots=" + repr(probe_slots(window, action))
            lines.append(line)
    return lines


class _FakeDocks:
    """Records what the menu's inline dock entries call."""

    def __init__(self, log: list) -> None:
        for name in ("expand_panel", "collapse_panel", "hide_panel", "dock_to_side", "pop_out_panel", "swap_sides"):
            setattr(self, name, _Recorder(log, f"docks.{name}"))


def _optional_menu(window, title: str) -> QMenu:
    menu = QMenu(title, window)
    menu.setObjectName(title.replace(" ", "_").lower())
    menu.addAction(f"{title} entry")
    return menu


def menu_scenarios() -> dict[str, dict]:
    """Name -> description of how to call ``build_main_menu_bar``. Covers every
    branch: each optional menu present/absent, dock actions absent / empty /
    partial / full, and a window without dock controls."""
    return {
        "all-present": dict(dock="both", recipe=True, preset=True, collections=True, catalog=True, docks=True),
        "no-optional-menus": dict(dock="both", recipe=False, preset=False, collections=False, catalog=False, docks=True),
        "no-recipe-menu": dict(dock="both", recipe=False, preset=True, collections=True, catalog=True, docks=True),
        "no-preset-menu": dict(dock="both", recipe=True, preset=False, collections=True, catalog=True, docks=True),
        "no-collections-menu": dict(dock="both", recipe=True, preset=True, collections=False, catalog=True, docks=True),
        "no-catalog-menu": dict(dock="both", recipe=True, preset=True, collections=True, catalog=False, docks=True),
        "dock-actions-none": dict(dock="none", recipe=True, preset=True, collections=True, catalog=True, docks=True),
        "dock-actions-empty": dict(dock="empty", recipe=True, preset=True, collections=True, catalog=True, docks=True),
        "dock-actions-library-only": dict(dock="library", recipe=True, preset=True, collections=True, catalog=True, docks=True),
        "dock-actions-inspector-only": dict(dock="inspector", recipe=True, preset=True, collections=True, catalog=True, docks=True),
        "dock-actions-unknown-key-only": dict(dock="unknown", recipe=True, preset=True, collections=True, catalog=True, docks=True),
        "no-workspace-docks-object": dict(dock="both", recipe=True, preset=True, collections=True, catalog=True, docks=False),
        "no-docks-no-presets": dict(dock="both", recipe=False, preset=False, collections=False, catalog=False, docks=False),
    }


def stub_menu_snapshot(name: str, spec: dict) -> list[str]:
    from image_triage.ui.menus import build_main_menu_bar

    with stub_window() as window:
        actions, _created = build_stub_actions(window)
        add_ai_state_actions(window, actions)
        window.workspace_docks = _FakeDocks(window.calls) if spec["docks"] else None
        dock_keys = {
            "both": ("library", "inspector"),
            "library": ("library",),
            "inspector": ("inspector",),
            "unknown": ("elsewhere",),
            "empty": (),
            "none": None,
        }[spec["dock"]]
        dock_actions = None
        if dock_keys is not None:
            dock_actions = {}
            for key in dock_keys:
                action = QAction(f"Show {key.title()}", window)
                action.setCheckable(True)
                action.setChecked(True)
                dock_actions[key] = action
        extras = {
            "workflow_recipe_menu": _optional_menu(window, "Recipes") if spec["recipe"] else None,
            "workspace_preset_menu": _optional_menu(window, "Workspaces") if spec["preset"] else None,
            "collections_menu": _optional_menu(window, "Collections List") if spec["collections"] else None,
            "catalog_menu": _optional_menu(window, "Catalog List") if spec["catalog"] else None,
        }
        build_main_menu_bar(window, actions, dock_actions, **extras)
        # Inline entries look the docks up when triggered; give the probe something to call.
        window.workspace_docks = _FakeDocks(window.calls)
        label_map = action_label_map(actions)
        for key, action in (dock_actions or {}).items():
            label_map[id(action)] = f"dock_actions[{key!r}]"
        lines = [f"menu scenario {name}: {spec}"]
        lines += menu_snapshot_lines(window.menuBar(), label_map, window, probe=True)
        # The reverse mapping: which fields no menu entry shows at all.
        shown = {id(action) for menu in window.menuBar().findChildren(QMenu) for action in menu.actions()}
        missing = [label for label, action in action_fields(actions) if id(action) not in shown]
        lines.append(f"fields not in any menu: {missing}")
        return lines


def stub_menu_snapshots() -> dict[str, list[str]]:
    return {name: stub_menu_snapshot(name, spec) for name, spec in menu_scenarios().items()}


# --- Command palette -----------------------------------------------------------
# Window methods that palette callbacks reach; replaced by recorders while a
# palette is described, so invoking a callback never runs the real handler.
_PALETTE_CALLBACK_TARGETS = (
    "_apply_filter_preset",
    "_run_workflow_recipe",
    "_apply_workspace_preset",
    "_open_virtual_collection",
    "_browse_catalog",
    "_move_selected_records_to_destination",
    "_handle_preview_rename_requested",
    "_handle_preview_winner_requested",
    "_handle_preview_reject_requested",
    "_handle_preview_keep_requested",
    "_handle_preview_move_requested",
    "_handle_preview_delete_requested",
    "_handle_preview_tag_requested",
    "_open_preview_image_in_photoshop",
)


class StubPreview:
    """The slice of ``FullScreenPreview`` that ``build_commands`` touches."""

    def __init__(self, *, visible: bool, focused: str, photoshop: str, compare: bool, focus_on: bool,
                 color_index: int, strength_index: int, dim: bool) -> None:
        from image_triage.review_tools import FOCUS_ASSIST_COLORS, FOCUS_ASSIST_STRENGTHS

        self.calls: list = []
        self._visible, self._focused, self._photoshop = visible, focused, photoshop
        self._compare, self._focus_on, self._dim = compare, focus_on, dim
        self._color = FOCUS_ASSIST_COLORS[color_index]
        self._strength = FOCUS_ASSIST_STRENGTHS[strength_index]

    def isVisible(self) -> bool:
        return self._visible

    def focused_path(self) -> str:
        return self._focused

    def focused_photoshop_path(self) -> str:
        return self._photoshop

    def compare_mode_enabled(self) -> bool:
        return self._compare

    def focus_assist_enabled(self) -> bool:
        return self._focus_on

    def focus_assist_color(self):
        return self._color

    def focus_assist_strength(self):
        return self._strength

    def focus_assist_dim_background(self) -> bool:
        return self._dim

    def __getattr__(self, name: str):
        if name.startswith("__") or name.startswith("_"):
            raise AttributeError(name)
        return _Recorder(self.__dict__.setdefault("calls", []), f"preview.{name}")


@dataclasses.dataclass(frozen=True)
class PaletteState:
    label: str
    context: str = "main"
    enabled: str = "all"  # "all" | "none" | "alternate" | "natural" (leave the window's own enablement)
    flags_on: bool = True
    saved_data: bool = True  # saved presets/recipes/workspaces, collections, catalog roots, recent folders
    docks: str = "fake"  # "fake" | "fake-unchecked" | "none"
    actions_present: bool = True
    preview: str = "none"  # "none" | "full" | "bare" | "hidden"
    photoshop: bool = True


PALETTE_STATES: tuple[PaletteState, ...] = (
    PaletteState("main|all-enabled|flags-on|data|docks", enabled="all", flags_on=True),
    PaletteState("main|all-enabled|flags-off|data|docks-unchecked", enabled="all", flags_on=False, docks="fake-unchecked"),
    PaletteState("main|all-disabled|no-data|no-docks", enabled="none", flags_on=False, saved_data=False, docks="none"),
    PaletteState("main|alternate-enabled|flags-on|data|docks", enabled="alternate", flags_on=True),
    PaletteState("main|alternate-enabled|flags-off|no-data|docks", enabled="alternate", flags_on=False, saved_data=False),
    PaletteState("main|no-actions|data|no-docks", actions_present=False, docks="none"),
    PaletteState("main|visible-preview-ignored", enabled="all", preview="full"),
    PaletteState("preview|hidden-preview", context="preview", enabled="all", preview="hidden"),
    PaletteState("preview|no-preview-built", context="preview", enabled="all", preview="none"),
    PaletteState("preview|visible|full", context="preview", enabled="all", preview="full"),
    PaletteState("preview|visible|focused-photoshop-missing-exe", context="preview", enabled="all", preview="full", photoshop=False),
    PaletteState("preview|visible|bare", context="preview", enabled="none", preview="bare", flags_on=False),
)


def _palette_fixtures() -> SimpleNamespace:
    from image_triage.filtering import RecordFilterQuery, SavedFilterPreset
    from image_triage.library_store import CatalogRoot, VirtualCollection
    from image_triage.models import FilterMode
    from image_triage.workflows import WorkflowRecipe, WorkspacePreset

    return SimpleNamespace(
        filters=[
            SavedFilterPreset("Golden Search", RecordFilterQuery(quick_filter=FilterMode.WINNERS, search_text="golden")),
            SavedFilterPreset("Everything", RecordFilterQuery()),
        ],
        recipes=[
            WorkflowRecipe(key="golden_recipe", name="Golden Recipe", description="A saved recipe."),
            WorkflowRecipe(key="bare_recipe", name="Bare Recipe"),
        ],
        workspaces=[
            WorkspacePreset(key="golden_ws", name="Golden Workspace", description="A saved workspace."),
            WorkspacePreset(key="bare_ws", name="Bare Workspace"),
        ],
        collections=[
            VirtualCollection(id="col-1", name="Golden Set", description="", kind="Proofing Set", item_count=3),
            VirtualCollection(id="col-2", name="Described Set", description="Has a description", kind="Custom", item_count=1),
        ],
        roots=[
            CatalogRoot(path="C:/golden/catalog_root", indexed_record_count=12),
            CatalogRoot(path="D:/", indexed_record_count=0),
        ],
        recents=[f"C:/golden/recent_{index}" for index in range(7)] + ["D:/"],
    )


_BUILDER_SHORTCUTS: dict[str, QKeySequence] = {}


def _builder_shortcuts() -> dict[str, QKeySequence]:
    """Each action's shortcut as a freshly built, un-customized window has it: the
    builder's own, with the registry defaults applied on top (no user overrides),
    except ``zen_mode``, whose QAction sequence ``MainWindow.__init__`` clears
    because a separate QShortcut owns F11."""
    if not _BUILDER_SHORTCUTS:
        from image_triage.ui.actions import apply_shortcut_overrides

        with stub_window() as window:
            actions, _created = build_stub_actions(window)
            apply_shortcut_overrides(actions, {})
            _BUILDER_SHORTCUTS.update({label: action.shortcut() for label, action in action_fields(actions)})
        _BUILDER_SHORTCUTS["zen_mode"] = QKeySequence()
    return _BUILDER_SHORTCUTS


@contextlib.contextmanager
def palette_state(window, state: PaletteState) -> Iterator[tuple[list, StubPreview | None]]:
    """Pin everything ``build_commands`` reads on a real ``MainWindow`` and
    restore it afterwards. Yields ``(window_call_log, stub_preview_or_None)``."""
    fixtures = _palette_fixtures()
    window_calls: list = []
    with contextlib.ExitStack() as stack:
        def patch(target, name, value):
            stack.enter_context(mock.patch.object(target, name, value))

        # View/toggle flags the subtitles read.
        on = state.flags_on
        patch(window, "_performance_logging_enabled", on)
        patch(window, "_compare_enabled", on)
        patch(window, "_auto_advance_enabled", on)
        patch(window, "_zen_mode_enabled", on)
        patch(window, "_burst_groups_enabled", on)
        patch(window, "_burst_stacks_enabled", not on)
        patch(window, "_show_hidden_folders", on)
        patch(window, "_browser_view_mode", "grid" if on else "details")
        patch(window, "_details_row_density", "compact" if on else "comfortable")

        # Data-driven sections.
        data = state.saved_data
        patch(window, "_saved_filter_presets", list(fixtures.filters) if data else [])
        patch(window, "_saved_workflow_recipes", list(fixtures.recipes) if data else [])
        patch(window, "_saved_workspace_presets", list(fixtures.workspaces) if data else [])
        patch(window._library_store, "list_collections", lambda: list(fixtures.collections) if data else [])
        patch(window._library_store, "list_catalog_roots", lambda: list(fixtures.roots) if data else [])
        patch(window, "_recent_destination_paths", lambda exclude_current_folder=False: list(fixtures.recents) if data else [])

        # Callback targets become recorders.
        for name in _PALETTE_CALLBACK_TARGETS:
            patch(window, name, _Recorder(window_calls, name))

        # Action enablement and shortcuts (both restored afterwards) and presence. The
        # palette shows each action's *current* shortcut, which other tests may have
        # changed on a shared window (e.g. by re-applying shortcut overrides), so pin it.
        if window.actions is not None and state.enabled != "natural":
            labelled = list(action_fields(window.actions))
            shortcuts = _builder_shortcuts()
            saved = [(action, action.isEnabled(), action.shortcut()) for _label, action in labelled]
            for position, (label, action) in enumerate(labelled):
                action.setEnabled({"all": True, "none": False, "alternate": position % 2 == 0}[state.enabled])
                action.setShortcut(shortcuts.get(label, QKeySequence()))

            def restore(saved=saved):
                for action, was_enabled, was_shortcut in saved:
                    action.setEnabled(was_enabled)
                    action.setShortcut(was_shortcut)

            stack.callback(restore)
        if not state.actions_present:
            patch(window, "actions", None)

        # Dock toggle actions.
        if state.docks == "none":
            patch(window, "workspace_docks", None)
        else:
            checked = state.docks == "fake"
            toggles = {}
            for key in ("library", "inspector", "mixed case"):
                action = QAction(f"dock {key}", window)
                action.setCheckable(True)
                action.setChecked(checked if key != "inspector" else not checked)
                toggles[key] = action
            patch(window, "workspace_docks", SimpleNamespace(toggle_actions=toggles))
            stack.callback(lambda actions=list(toggles.values()): [a.deleteLater() for a in actions])

        # Popout viewer.
        preview = None
        if state.preview != "none":
            preview = StubPreview(
                visible=state.preview != "hidden",
                focused="C:/golden/photos/focused.jpg" if state.preview in ("full", "hidden") else "",
                photoshop="C:/golden/photos/focused.psd" if state.preview in ("full", "hidden") else "",
                compare=on,
                focus_on=on,
                color_index=0 if on else 2,
                strength_index=1 if on else 0,
                dim=on,
            )
            patch(window, "_preview", preview)
        else:
            patch(window, "_preview", None)  # whatever an earlier test left built or visible
        patch(window, "_photoshop_executable", "C:/golden/Photoshop.exe" if state.photoshop else "")
        yield window_calls, preview


def describe_callback(
    command, label_map: dict[int, str], window_calls: list, preview: StubPreview | None, *, invoke: bool = True
) -> str:
    """What the callback does. ``action.trigger`` callbacks are named by the
    action they belong to (never run). With ``invoke`` the others are called -
    only valid once the window's handlers are recorders (``palette_state``);
    without it they are described by their code (used for natural states of a
    live window, where calling them would run real handlers)."""
    callback = command.callback
    owner = getattr(callback, "__self__", None)
    if isinstance(owner, QAction):
        return f"action:{label_map.get(id(owner), 'dock/' + owner.text())}.{getattr(callback, '__name__', '?')}"
    if not invoke:
        if owner is not None:
            return f"bound:{type(owner).__name__}.{getattr(callback, '__name__', '?')}"
        code = callback.__code__
        return f"fn names={code.co_names} consts={code.co_consts!r} defaults={callback.__defaults__!r} free={code.co_freevars}"
    window_calls.clear()
    if preview is not None:
        preview.calls.clear()
    callback()
    calls = list(window_calls) + (list(preview.calls) if preview is not None else [])
    window_calls.clear()
    if preview is not None:
        preview.calls.clear()
    return "calls=" + repr(calls)


def palette_snapshot_lines(
    window, commands, *, label: str, context: str, window_calls: list, preview: StubPreview | None, invoke: bool = True
) -> list[str]:
    label_map = action_label_map(window.actions) if window.actions is not None else {}
    docks = getattr(window, "workspace_docks", None)
    if docks is not None:
        label_map.update({id(action): f"dock[{key}]" for key, action in docks.toggle_actions.items()})
    lines = [f"== palette state {label} (context={context}): {len(commands)} commands"]
    for command in commands:
        lines.append(
            f"{command.id} | title={command.title!r} | subtitle={command.subtitle!r} | section={command.section!r}"
            f" | shortcut={command.shortcut!r} | keywords={command.keywords!r}"
            f" | cb={describe_callback(command, label_map, window_calls, preview, invoke=invoke)}"
        )
    return lines


def synthetic_palette_snapshots(window) -> dict[str, tuple[list[str], list[str]]]:
    """State label -> (dump lines, ordered command ids) for the ``PALETTE_STATES`` grid."""
    results: dict[str, tuple[list[str], list[str]]] = {}
    for state in PALETTE_STATES:
        with palette_state(window, state) as (window_calls, preview):
            commands = window._command_palette.build_commands(state.context)
            lines = palette_snapshot_lines(
                window, commands, label=state.label, context=state.context, window_calls=window_calls, preview=preview
            )
            results[state.label] = (lines, [command.id for command in commands])
    return results


# --- photoedit CLI --------------------------------------------------------------
def _describe_argparse_action(action: argparse.Action) -> dict:
    record: dict = {
        "class": type(action).__name__,
        "options": list(action.option_strings),
        "dest": action.dest,
        "nargs": action.nargs,
        "const": repr(action.const),
        "default": repr(action.default),
        "type": getattr(action.type, "__name__", repr(action.type)) if action.type is not None else None,
        "required": action.required,
        "help": action.help,
        "metavar": repr(action.metavar),
    }
    if not isinstance(action, argparse._SubParsersAction):
        record["choices"] = None if action.choices is None else list(action.choices)
    return record


def parser_structure_lines(parser: argparse.ArgumentParser, depth: int = 0) -> list[str]:
    pad = "  " * depth
    defaults = {
        key: (value.__name__ if callable(value) else repr(value)) for key, value in sorted(parser._defaults.items())
    }
    lines = [
        f"{pad}parser prog={parser.prog!r} description={parser.description!r} add_help={parser.add_help}"
        f" allow_abbrev={parser.allow_abbrev} defaults={defaults}"
    ]
    for action in parser._actions:
        lines.append(f"{pad}  arg {json.dumps(_describe_argparse_action(action))}")
        if isinstance(action, argparse._SubParsersAction):
            helps = {choice.dest: choice.help for choice in action._choices_actions}
            for name, sub in action.choices.items():
                lines.append(f"{pad}  subcommand {name!r} help={helps.get(name)!r}")
                lines.extend(parser_structure_lines(sub, depth + 2))
    return lines


def parser_help_lines(parser: argparse.ArgumentParser) -> list[str]:
    with mock.patch.dict(os.environ, {"COLUMNS": "100"}):
        lines = [f"--- help for {parser.prog} ---", *parser.format_help().splitlines()]
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for sub in action.choices.values():
                    lines.extend(parser_help_lines(sub))
    return lines


def subcommand_names(parser: argparse.ArgumentParser) -> list[str]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return list(action.choices)
    return []


# argv per subcommand; "min" = only required arguments (so defaults show), "full" =
# every option given a non-default value (so every dest/type/choice is exercised).
CLI_PARSE_CASES: tuple[tuple[str, list[str]], ...] = (
    ("inspect|min", ["inspect", "a.jpg"]),
    ("preview|min", ["preview", "a.jpg"]),
    ("preview|full", ["preview", "a.jpg", "--width", "120", "--height", "40"]),
    ("recipe|min", ["recipe", "out.json"]),
    (
        "recipe|full",
        ["recipe", "out.json", "--recipe", "base.json", "--exposure", "0.5", "--contrast", "1", "--highlights", "2",
         "--shadows", "3", "--whites", "4", "--blacks", "5", "--temperature", "6", "--tint", "7", "--vibrance", "8",
         "--saturation", "9", "--clarity", "10", "--dehaze", "11", "--sharpen", "12", "--denoise", "13",
         "--vignette", "14", "--vignette-midpoint", "15", "--vignette-roundness", "16", "--vignette-feather", "17",
         "--vignette-highlights", "18", "--rotate", "19", "--crop", "1", "2", "3", "4"],
    ),
    ("render|min", ["render", "in.jpg", "out.jpg"]),
    (
        "render|full",
        ["render", "in.jpg", "out.jpg", "--quality", "80", "--recipe", "base.json", "--exposure", "-1.5", "--contrast", "1",
         "--highlights", "2", "--shadows", "3", "--whites", "4", "--blacks", "5", "--temperature", "6", "--tint", "7",
         "--vibrance", "8", "--saturation", "9", "--clarity", "10", "--dehaze", "11", "--sharpen", "12", "--denoise", "13",
         "--vignette", "14", "--vignette-midpoint", "15", "--vignette-roundness", "16", "--vignette-feather", "17",
         "--vignette-highlights", "18", "--rotate", "19", "--crop", "0", "0", "10", "10"],
    ),
    ("spot|min", ["spot", "in.jpg", "out.jpg", "--x", "1", "--y", "2", "--radius", "3"]),
    ("spot|full", ["spot", "in.jpg", "out.jpg", "--x", "1", "--y", "2", "--radius", "3", "--strength", "0.5", "--quality", "70"]),
    ("session-new|min", ["session-new", "a.jpg"]),
    ("session-new|full", ["session-new", "a.jpg", "--out", "s.edit.json", "--json"]),
    ("session-info|min", ["session-info", "s.edit.json"]),
    ("session-info|full", ["session-info", "s.edit.json", "--json", "--strict"]),
    ("validate|min", ["validate", "s.edit.json"]),
    ("validate|full", ["validate", "s.edit.json", "--strict", "--json"]),
    ("migrate|min", ["migrate", "s.edit.json"]),
    ("migrate|full", ["migrate", "s.edit.json", "--to", "2", "--out", "m.edit.json", "--json"]),
    ("op-add|min", ["op-add", "s.edit.json", "exposure"]),
    (
        "op-add|full",
        ["op-add", "s.edit.json", "exposure", "--id", "op1", "--param", "ev=1", "--param", "x=2", "--mask", "m1",
         "--space", "sp", "--before", "a", "--after", "b", "--first", "--last"],
    ),
    ("op-set|min", ["op-set", "s.edit.json", "op1"]),
    ("op-set|enabled-true", ["op-set", "s.edit.json", "op1", "--param", "a=1", "--enabled", "true", "--mask", "m", "--space", "sp"]),
    ("op-set|enabled-false-global", ["op-set", "s.edit.json", "op1", "--enabled", "false", "--global"]),
    ("op-set|global-then-mask", ["op-set", "s.edit.json", "op1", "--global", "--mask", "m2"]),
    ("op-move|min", ["op-move", "s.edit.json", "op1"]),
    ("op-move|full", ["op-move", "s.edit.json", "op1", "--before", "a", "--after", "b", "--first", "--last"]),
    ("op-delete|min", ["op-delete", "s.edit.json", "op1"]),
    ("wb-kelvin|min", ["wb-kelvin", "s.edit.json", "--kelvin", "5500"]),
    (
        "wb-kelvin|full",
        ["wb-kelvin", "s.edit.json", "--id", "wb", "--kelvin", "5500", "--tint", "3", "--mask", "m", "--before", "a",
         "--after", "b", "--first", "--last"],
    ),
    ("levels|min", ["levels", "s.edit.json", "--black", "1", "--midpoint", "2", "--white", "3"]),
    (
        "levels|full",
        ["levels", "s.edit.json", "--id", "lv", "--black", "1", "--midpoint", "2", "--white", "3", "--mask", "m",
         "--before", "a", "--after", "b", "--first", "--last"],
    ),
    ("point-curve|min", ["point-curve", "s.edit.json", "--point", "0,0"]),
    (
        "point-curve|full",
        ["point-curve", "s.edit.json", "--id", "pc", "--point", "0,0", "--point", "255,255", "--mask", "m",
         "--before", "a", "--after", "b", "--first", "--last"],
    ),
    ("crop-preset|min", ["crop-preset", "s.edit.json", "--space", "sp", "--preset", "3:2"]),
    (
        "crop-preset|full",
        ["crop-preset", "s.edit.json", "--id", "cp", "--space", "sp", "--preset", "16:9", "--anchor", "left",
         "--before", "a", "--after", "b", "--first", "--last"],
    ),
    ("space-add|min", ["space-add", "s.edit.json", "--id", "sp", "--source-width", "6000", "--source-height", "4000"]),
    (
        "space-add|full",
        ["space-add", "s.edit.json", "--id", "sp", "--source-width", "6000", "--source-height", "4000", "--crop", "1,2,3,4"],
    ),
    (
        "mask-radial|min",
        ["mask-radial", "s.edit.json", "--id", "m", "--space", "sp", "--cx", "1", "--cy", "2", "--rx", "3", "--ry", "4"],
    ),
    (
        "mask-radial|full",
        ["mask-radial", "s.edit.json", "--id", "m", "--space", "sp", "--cx", "1", "--cy", "2", "--rx", "3", "--ry", "4",
         "--angle", "45", "--feather", "10", "--density", "50", "--invert"],
    ),
    (
        "mask-gradient|min",
        ["mask-gradient", "s.edit.json", "--id", "m", "--space", "sp", "--x1", "1", "--y1", "2", "--x2", "3", "--y2", "4"],
    ),
    (
        "mask-gradient|full",
        ["mask-gradient", "s.edit.json", "--id", "m", "--space", "sp", "--x1", "1", "--y1", "2", "--x2", "3", "--y2", "4",
         "--feather", "10", "--density", "50", "--invert"],
    ),
    ("mask-painted-add|min", ["mask-painted-add", "s.edit.json", "--id", "m", "--space", "sp", "--png", "mask.png"]),
    (
        "mask-subject|min",
        ["mask-subject", "s.edit.json", "--id", "m", "--space", "sp", "--model-id", "birefnet", "--model-version", "1",
         "--weights-hash", "abc"],
    ),
    (
        "mask-subject|full",
        ["mask-subject", "s.edit.json", "--id", "m", "--space", "sp", "--model-id", "birefnet", "--model-version", "1",
         "--weights-hash", "abc", "--cache-png", "cache.png"],
    ),
    ("mask-refine-luma|min", ["mask-refine-luma", "s.edit.json", "m1", "--low", "10", "--high", "200"]),
    ("mask-refine-luma|full", ["mask-refine-luma", "s.edit.json", "m1", "--low", "10", "--high", "200", "--feather", "5", "--invert"]),
    ("mask-refine-color|min", ["mask-refine-color", "s.edit.json", "m1", "--space", "sp", "--x", "1", "--y", "2"]),
    (
        "mask-refine-color|full",
        ["mask-refine-color", "s.edit.json", "m1", "--space", "sp", "--x", "1", "--y", "2", "--tolerance", "10",
         "--feather", "5", "--invert"],
    ),
    ("mask-bounds|min", ["mask-bounds", "s.edit.json", "m1", "--pixels", "-4"]),
    ("mask-delete|min", ["mask-delete", "s.edit.json", "m1"]),
    ("mask-delete|force", ["mask-delete", "s.edit.json", "m1", "--force"]),
    ("relink|min", ["relink", "photos"]),
    ("relink|full", ["relink", "photos", "--sessions", "*.json", "--json"]),
    ("export-xmp|min", ["export-xmp", "s.edit.json"]),
    ("export-xmp|full", ["export-xmp", "s.edit.json", "--out", "s.xmp"]),
)

# argv that must be rejected (argparse exits with status 2).
CLI_ERROR_CASES: tuple[tuple[str, list[str], str], ...] = (
    ("no-command", [], "command"),
    ("unknown-command", ["frobnicate"], "frobnicate"),
    ("bad-crop-preset-choice", ["crop-preset", "s.edit.json", "--space", "sp", "--preset", "7:5"], "--preset"),
    ("bad-anchor-choice", ["crop-preset", "s.edit.json", "--space", "sp", "--preset", "1:1", "--anchor", "middle"], "--anchor"),
    ("bad-enabled-choice", ["op-set", "s.edit.json", "op1", "--enabled", "maybe"], "--enabled"),
    ("missing-required-spot-radius", ["spot", "in.jpg", "out.jpg", "--x", "1", "--y", "2"], "--radius"),
    ("non-int-width", ["preview", "a.jpg", "--width", "wide"], "--width"),
    ("wrong-crop-arity", ["render", "in.jpg", "out.jpg", "--crop", "1", "2", "3"], "--crop"),
    ("missing-point", ["point-curve", "s.edit.json"], "--point"),
    ("missing-positional", ["inspect"], "image"),
    ("missing-kelvin", ["wb-kelvin", "s.edit.json"], "--kelvin"),
)


def _format_namespace(namespace: argparse.Namespace) -> str:
    items = []
    for key, value in sorted(vars(namespace).items()):
        if callable(value):
            shown = f"func:{value.__name__}"
        elif isinstance(value, Path):
            shown = f"path:{value.as_posix()}"
        else:
            shown = repr(tuple(value) if isinstance(value, tuple) else value)
        items.append(f"{key}={shown}")
    return " ".join(items)


def cli_parse_lines(parser: argparse.ArgumentParser) -> list[str]:
    lines = []
    for label, argv in CLI_PARSE_CASES:
        lines.append(f"parse {label}: {_format_namespace(parser.parse_args(argv))}")
    return lines
