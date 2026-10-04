"""Characterization test for ``build_main_window_actions`` (audit finding A2, stage 2).

The builder used to be one ~440-line ``MainWindowActions(...)`` call. It is now
a handful of group functions (``_file_actions``, ``_view_actions``, ...) plus one
helper per exclusive QActionGroup. The restructuring must not change anything
observable, so this module pins what the builder produces *today*, as a
SHA-256 digest of a full text dump (``tests/builder_snapshot_support.py``):

* every ``MainWindowActions`` field in declaration order (dict fields in key
  order) with each QAction's text, shortcuts, checkable/checked/enabled/visible
  state, tool tip, status tip, menu role, auto-repeat, priority, action-group
  membership, and which window handler its ``triggered`` / ``toggled`` signal
  reaches (observed with a recording stand-in for MainWindow, so no real handler
  runs); and
* the QAction / QActionGroup *creation order* on the window.

It says nothing about whether those values are right, only that they have not
moved. The readable tests below pin the same facts in a form that tells you
roughly WHAT changed when the digest test fails.

If a change to the actions is intentional, regenerate the digest table:

    # PowerShell
    $env:UPDATE_BUILDER_SNAPSHOTS = "1"
    pythonw3.13.exe scripts/run313.py regen.log pytest -q -s tests/test_actions_snapshot.py
    Remove-Item Env:UPDATE_BUILDER_SNAPSHOTS

then paste the table the run prints (it is also written to
``<temp dir>/image_triage_test_actions_snapshot.txt``) over ``_GOLDEN`` below,
update the readable expectations that the change touched, and review the diff.
The digests are pinned for Windows key-sequence text (``Ctrl+O`` ...).
"""
from __future__ import annotations

import dataclasses
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import QStyle

from image_triage.ui import actions as actions_module
from image_triage.ui.actions import MainWindowActions, build_main_window_actions
from tests import builder_snapshot_support as support

def _controllers_by_window_attribute() -> dict[str, type]:
    from image_triage.ai_run_controller import AiRunController
    from image_triage.appearance_controller import AppearanceController
    from image_triage.ai_setup_controller import AiSetupController
    from image_triage.aiculler_controller import AiCullerController
    from image_triage.batch_rename_controller import BatchRenameApplyController
    from image_triage.catalog_controller import CatalogController
    from image_triage.command_palette_controller import CommandPaletteController
    from image_triage.folder_ops_controller import FolderOpsController
    from image_triage.record_ops_controller import RecordOpsController
    from image_triage.records_view_controller import RecordsViewController
    from image_triage.recycle_bin_controller import RecycleBinController
    from image_triage.tool_mode_controller import ToolModeController
    from image_triage.toolbar_controller import ToolbarController
    from image_triage.zen_controller import ZenController
    from image_triage.ui.toolbar_menus import ToolbarMenuController

    return {
        "_ai_run": AiRunController,
        "_appearance": AppearanceController,
        "_ai_setup": AiSetupController,
        "_aiculler": AiCullerController,
        "_batch_rename": BatchRenameApplyController,
        "_catalog": CatalogController,
        "_command_palette": CommandPaletteController,
        "_folder_ops": FolderOpsController,
        "_record_ops": RecordOpsController,
        "_records_view": RecordsViewController,
        "_recycle_bin": RecycleBinController,
        "_tool_mode": ToolModeController,
        "_toolbar": ToolbarController,
        "_zen": ZenController,
        "_toolbar_menus": ToolbarMenuController,
    }


_CONTROLLERS_BY_WINDOW_ATTRIBUTE = _controllers_by_window_attribute()

# case id -> (sha256 of the text, its length in characters)
_GOLDEN: dict[str, tuple[str, int]] = {
    "fields+slots": ("ff1e667c1d2981bf6c0845ff42329e8fbe1e6610cdf03149603a09cd51cb99f2", 89265),
    "creation-order": ("45c802fdc14fbd2d56cb3ea52f592067d91dada10bcb4835a30e0408ab42e97e", 4939),
}

_DICT_FIELDS = (
    "appearance_actions",
    "toolbar_placement_actions",
    "sort_actions",
    "filter_actions",
    "ai_state_actions",
    "column_actions",
)

_EXPECTED_SHORTCUTS = {
    "open_folder": "Ctrl+O",
    "refresh_folder": "F5",
    "new_folder": "Ctrl+Shift+N",
    "workflow_settings": "Ctrl+,",
    "undo": "Ctrl+Z",
    "rename_selection": "F2",
    "batch_rename_selection": "Ctrl+Shift+R",
    "batch_resize_selection": "Ctrl+Shift+E",
    "batch_convert_selection": "Ctrl+Shift+C",
    "compare_mode": "C",
    "grid_view": "Ctrl+1",
    "details_view": "Ctrl+2",
    "zen_mode": "F11",
    "open_ai_workflow_center": "Ctrl+Shift+W",
    "quick_rerank_ai_culling": "Ctrl+Shift+Y",
    "next_ai_pick": "Ctrl+Alt+N",
    "compare_ai_group": "Ctrl+Alt+G",
    "winner_ladder_mode": "Ctrl+Alt+W",
    "share_to_phone": "Ctrl+Alt+P",
    "handoff_builder": "Ctrl+Alt+H",
    "send_to_editor_pipeline": "Ctrl+Alt+E",
    "best_of_set_auto_assembly": "Ctrl+Alt+B",
    "save_workspace_preset": "Ctrl+Alt+S",
    "clear_filters": "Ctrl+Shift+X",
    "documentation": "F1",
}

_EXPECTED_ICONS = {
    "open_folder": QStyle.StandardPixmap.SP_DialogOpenButton,
    "refresh_folder": QStyle.StandardPixmap.SP_BrowserReload,
    "delete_selection": QStyle.StandardPixmap.SP_TrashIcon,
    "guided_ai_cull_preferences": QStyle.StandardPixmap.SP_MediaPlay,
    "run_ai_culling": QStyle.StandardPixmap.SP_MediaPlay,
}

_EXPECTED_CHECKABLE = {
    "compare_mode",
    "auto_advance",
    "burst_groups",
    "burst_stacks",
    "show_hidden_folders",
    "grid_view",
    "details_view",
    "details_density_compact",
    "details_density_comfortable",
    "zen_mode",
    "show_workspace_toolbar",
    "performance_logging",
}

# Position of each exclusive QActionGroup in the creation order: it is created
# just before the actions it holds, after the 93 plain actions.
_EXPECTED_GROUP_POSITIONS = [93, 104, 107, 114, 127]


@pytest.fixture(scope="module")
def built():
    """``(window, actions, created_children, dump_lines)`` from the real builder
    run against a recording stand-in window."""
    with support.stub_window() as window:
        actions, created = support.build_stub_actions(window)
        lines = support.actions_snapshot_lines(window, actions, probe=True, new_children=created)
        yield window, actions, created, lines


def _plain_field_names() -> list[str]:
    return [f.name for f in dataclasses.fields(MainWindowActions) if f.name not in _DICT_FIELDS]


def test_actions_text_is_unchanged(built) -> None:
    *_head, lines = built
    split = lines.index("creation order:")
    actual = {
        "fields+slots": support.digest_lines(lines[:split]),
        "creation-order": support.digest_lines(lines[split:]),
    }
    support.assert_matches_golden(
        module="test_actions_snapshot",
        table_name="_GOLDEN",
        golden=_GOLDEN,
        actual=actual,
        what="The QActions produced by build_main_window_actions",
        dumps={"fields+slots": lines[:split], "creation-order": lines[split:]},
    )


# --- Readable facts (independent of the digest) --------------------------------
def test_field_inventory(built) -> None:
    _window, actions, _created, _lines = built
    plain = _plain_field_names()
    assert len(plain) == 93
    assert [label for label, _ in support.action_fields(actions)][: len(plain)] == plain
    assert sum(1 for _ in support.action_fields(actions)) == 131
    # The dict fields, in key order.
    assert [mode.name for mode in actions.appearance_actions] == [
        "SLATE", "INDIGO", "DARK", "MIDNIGHT", "GRAPHITE", "FOREST", "HIGH_CONTRAST", "WARM_NEUTRAL", "LIGHT", "AUTO"
    ]
    assert list(actions.toolbar_placement_actions) == ["floating", "docked"]
    assert [mode.name for mode in actions.sort_actions] == ["NAME", "DATE", "SIZE", "TYPE", "AI_RANK", "AI_WOW"]
    assert [mode.name for mode in actions.filter_actions] == [
        "ALL", "WINNERS", "REJECTS", "UNREVIEWED", "EDITED", "SMART_GROUPS", "DUPLICATES", "AI_TOP_PICKS",
        "AI_GROUPED", "AI_DISAGREEMENTS", "AI_INGESTED", "AI_PREFILTER_DUMPED",
    ]
    assert list(actions.column_actions) == [1, 2, 3, 4, 5, 6, 7, 8]
    # The builder leaves this one empty; the records view controller fills it in.
    assert actions.ai_state_actions == {}


def test_creation_order_is_declaration_order_then_each_group(built) -> None:
    _window, actions, created, _lines = built
    labels = support.action_label_map(actions)
    created_actions = [labels[id(child)] for child in created if id(child) in labels]
    assert created_actions[:93] == _plain_field_names(), "plain actions must be created in declaration order"
    assert [position for position, child in enumerate(created) if isinstance(child, QActionGroup)] == _EXPECTED_GROUP_POSITIONS
    assert created_actions[93:] == [label for label, _ in support.action_fields(actions)][93:]
    assert len(created) == 131 + len(_EXPECTED_GROUP_POSITIONS)


def test_shortcuts(built) -> None:
    _window, actions, _created, _lines = built
    actual = {
        label: [seq.toString() for seq in action.shortcuts()][0]
        for label, action in support.action_fields(actions)
        if action.shortcuts()
    }
    assert actual == _EXPECTED_SHORTCUTS


def test_icons_are_the_standard_pixmaps_of_the_window_style(built) -> None:
    window, actions, _created, _lines = built
    with_icon = {label for label, action in support.action_fields(actions) if not action.icon().isNull()}
    assert with_icon == set(_EXPECTED_ICONS)
    by_label = dict(support.action_fields(actions))
    for label, pixmap in _EXPECTED_ICONS.items():
        want = window.style().standardIcon(pixmap).pixmap(16, 16).toImage()
        got = by_label[label].icon().pixmap(16, 16).toImage()
        assert got == want, f"{label} no longer uses {pixmap}"


def test_checkable_actions_and_auto_repeat(built) -> None:
    _window, actions, _created, _lines = built
    checkable_plain = {
        label for label, action in support.action_fields(actions) if "[" not in label and action.isCheckable()
    }
    assert checkable_plain == _EXPECTED_CHECKABLE
    for field in _DICT_FIELDS:
        assert all(action.isCheckable() for action in getattr(actions, field).values()), field
    assert [label for label, action in support.action_fields(actions) if not action.autoRepeat()] == ["open_command_palette"]


def test_group_membership_is_exclusive_per_dict_field(built) -> None:
    _window, actions, _created, _lines = built
    groups = []
    for field in ("appearance_actions", "toolbar_placement_actions", "sort_actions", "filter_actions", "column_actions"):
        owners = {action.actionGroup() for action in getattr(actions, field).values()}
        assert len(owners) == 1 and None not in owners, field
        (group,) = owners
        assert group.isExclusive()
        groups.append(group)
    assert len({id(group) for group in groups}) == 5, "each dict field has its own QActionGroup"
    assert all(action.actionGroup() is None for label, action in support.action_fields(actions) if "[" not in label)


def test_every_slot_is_a_real_main_window_handler(built) -> None:
    """The recording window accepts any ``_name``; make sure each name it recorded
    is something MainWindow really defines, so a typo cannot hide behind the stub."""
    from image_triage.window import MainWindow

    window, actions, _created, _lines = built
    recorded: set[str] = set()
    for _label, action in support.action_fields(actions):
        for calls in support.probe_slots(window, action).values():
            recorded.update(call[0] for call in calls)
    assert recorded, "no slot calls recorded"

    def is_real(name: str) -> bool:
        if "." not in name:
            return callable(getattr(MainWindow, name, None))
        # A handler that lives on one of the window's controllers is recorded as "<owner attribute>.<method>".
        owner, method = name.split(".", 1)
        controller = _CONTROLLERS_BY_WINDOW_ATTRIBUTE.get(owner)
        return controller is not None and callable(getattr(controller, method, None))

    missing = sorted(name for name in recorded if not is_real(name))
    assert not missing, f"slots that MainWindow (or its controllers) does not define: {missing}"


def test_every_plain_action_is_connected_to_exactly_one_signal(built) -> None:
    window, actions, _created, _lines = built
    for label, action in support.action_fields(actions):
        probed = support.probe_slots(window, action)
        wired = "toggled" if action.isCheckable() else "triggered"
        other = "triggered" if action.isCheckable() else "toggled"
        assert len(probed[wired]) == 1, f"{label}: expected one {wired} slot call, got {probed[wired]}"
        assert not probed.get(other), f"{label}: {other} unexpectedly reaches {probed.get(other)}"


# --- Group structure -----------------------------------------------------------
def test_single_action_groups_are_disjoint_and_complete() -> None:
    with support.stub_window() as window:
        seen: dict[str, str] = {}
        for create_group in actions_module._SINGLE_ACTION_GROUPS:
            group = create_group(window)
            assert group, f"{create_group.__name__} is empty"
            for name in group:
                assert name not in seen, f"{name} is built by both {seen[name]} and {create_group.__name__}"
                seen[name] = create_group.__name__
        assert sorted(seen) == sorted(_plain_field_names()), "the groups must supply every plain field exactly once"


def test_building_twice_gives_independent_equal_actions() -> None:
    with support.stub_window() as window:
        first = build_main_window_actions(window)
        second = build_main_window_actions(window)
        assert [label for label, _ in support.action_fields(first)] == [label for label, _ in support.action_fields(second)]
        assert all(a is not b for (_, a), (_, b) in zip(support.action_fields(first), support.action_fields(second)))


def test_a_group_that_redefines_an_action_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(
        actions_module,
        "_SINGLE_ACTION_GROUPS",
        (actions_module._file_actions, actions_module._file_actions),
    )
    with support.stub_window() as window:
        with pytest.raises(ValueError, match="open_folder"):
            actions_module._create_single_actions(window)


def test_a_missing_group_is_caught_by_the_dataclass(monkeypatch) -> None:
    monkeypatch.setattr(actions_module, "_SINGLE_ACTION_GROUPS", actions_module._SINGLE_ACTION_GROUPS[1:])
    with support.stub_window() as window:
        with pytest.raises(TypeError, match="open_folder"):
            build_main_window_actions(window)


def test_main_window_uses_the_builder(main_window) -> None:
    """The live window's actions have exactly the builder's fields (plus the
    AI-state actions its records controller adds afterwards)."""
    live = [label for label, _ in support.action_fields(main_window.actions) if not label.startswith("ai_state_actions[")]
    with support.stub_window() as window:
        built_actions = build_main_window_actions(window)
        assert live == [label for label, _ in support.action_fields(built_actions)]
