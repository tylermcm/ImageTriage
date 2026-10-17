"""Characterization test for ``build_main_menu_bar`` (audit finding A2, stage 2).

The menu bar builder used to be one ~200-line function; it is now one helper per
top-level menu (``_build_file_menu``, ``_build_view_menu``, ...). The View menu
is created third but only finished after the Tools menu has been created, and
that interleaving, the order of every entry and the optional-menu branches are
all user-visible, so this module pins the produced *tree*.

For each of 13 scenarios (every optional menu present / absent, dock actions
absent / empty / partial / full, a window without dock controls) the real
builder runs against a recording stand-in window and the full tree is dumped:
menu titles in order, each entry's text / shortcuts / checkable / checked /
enabled, separators, nested submenus, the ``MainWindowActions`` field each entry
is (``INLINE`` entries created with ``addAction(text, slot)`` are probed to see
which call they make instead). The dumps are pinned by SHA-256. It is a
statement that the menus are *unchanged*, not that they are right.

If a menu change is intentional, regenerate the digest table:

    # PowerShell
    $env:UPDATE_BUILDER_SNAPSHOTS = "1"
    pythonw3.13.exe scripts/run313.py regen.log pytest -q -s tests/test_menu_tree_snapshot.py
    Remove-Item Env:UPDATE_BUILDER_SNAPSHOTS

then paste the printed table (also written to
``<temp dir>/image_triage_test_menu_tree_snapshot.txt``) over ``_GOLDEN`` below,
update the readable expectations that the change touched, and review the diff.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QMenu

from tests import builder_snapshot_support as support

# scenario -> (sha256 of the dump, its length in characters)
_GOLDEN: dict[str, tuple[str, int]] = {
    "all-present": ("5c039cbf90546644af72fd8aa630f32f3d8b677d7782d8a49262e6538be5f65e", 24693),
    "no-optional-menus": ("879644ddef23fdf18adde407228c4c53d7f13203886f79c63b5e688e38940a97", 23748),
    "no-recipe-menu": ("49914c4ef9ef2dd7ef922e7122448d4460fbca737dda28da570926aa57e1f40c", 24509),
    "no-preset-menu": ("43f7dc6b745205c68afe774a76464ca57afcaf9343b5a2b980e8865cd986a70d", 24372),
    "no-collections-menu": ("924744ac98b8db90ce64889212691d63a4a47e042073d4d43930a4e4531437dd", 24475),
    "no-catalog-menu": ("1cc86dd9553929e879e84d4c02ec0c0caae2c853081fb995168066e4db70321b", 24483),
    "dock-actions-none": ("9bd0acb4873b125cfd16160acb8020598055090a128d04f4b5dd1b22e9932d7e", 21601),
    "dock-actions-empty": ("fcebc0502d572f6a61adafca966a62de48e3b58973edf9403fc1422591d5e9a2", 21603),
    "dock-actions-library-only": ("ad41e36817d0dd4499cfdc0e7d18414f3c0dd971d03ba1a06aabc2c33fb82954", 24589),
    "dock-actions-inspector-only": ("db8e4d3037e5997f297c5363b3c7a0368eba0e224d493d58e237d23428da14c5", 24597),
    "dock-actions-unknown-key-only": ("e3c1b2e0244d89f6bebebae72438f11a423147e13976b54180aa8d48378b1b4f", 24476),
    "no-workspace-docks-object": ("d9adb3314a68b225ee06dd20b8dc17a99cb255d42f8c1571eb5e20e04372150b", 22470),
    "no-docks-no-presets": ("b8d9901caa177ae1e811ffec534ee3e90233b152ccc3e494bb03c97521080592", 21513),
}

_TOP_LEVEL_TITLES = ["&File", "&Edit", "&View", "&Review", "&Library", "&Workflow", "&AI", "&Tools", "&Settings", "&Help"]


@pytest.fixture(scope="module")
def dumps() -> dict[str, list[str]]:
    return support.stub_menu_snapshots()


def test_menu_trees_are_unchanged(dumps) -> None:
    support.assert_matches_golden(
        module="test_menu_tree_snapshot",
        table_name="_GOLDEN",
        golden=_GOLDEN,
        actual={name: support.digest_lines(lines) for name, lines in dumps.items()},
        what="The main menu bar tree produced by build_main_menu_bar",
        dumps=dumps,
    )


# --- Readable facts (independent of the digest) --------------------------------
def _top_level(lines: list[str]) -> list[str]:
    return [line.split("title=")[1].split(" object=")[0].strip("'") for line in lines if line.startswith("> menu")]


def _entries_of(lines: list[str], title: str) -> list[str]:
    """The direct children of the top-level menu ``title`` as ``menu:<title>`` /
    ``---`` / ``<field>`` tokens."""
    start = lines.index(next(line for line in lines if line.startswith(f"> menu title={title!r} ")))
    entries: list[str] = []
    for line in lines[start + 1 :]:
        if not line.startswith("  "):
            break
        if line.startswith("   "):
            continue  # deeper than a direct child
        body = line.strip()
        if body.startswith("> menu"):
            entries.append("menu:" + body.split("title=")[1].split(" object=")[0].strip("'"))
        elif body.startswith("---"):
            entries.append("---")
        else:
            entries.append(body.split(" ")[1])
    return entries


def test_top_level_menus_and_their_order(dumps) -> None:
    for name, lines in dumps.items():
        assert _top_level(lines) == _TOP_LEVEL_TITLES, name


def test_view_menu_is_finished_after_the_tools_menu(dumps) -> None:
    """The View menu's window/dock controls are appended after Tools exists;
    with dock controls the tail is Panels + Panel Layout, without it is not."""
    lines = dumps["all-present"]
    assert _entries_of(lines, "&View") == [
        "menu:Appearance",
        "menu:Layout",
        "menu:Sort",
        "menu:Filters",
        "menu:Review View",
        "---",
        "show_workspace_toolbar",
        "menu:Toolbar Position",
        "menu:Panels",
        "menu:Panel Layout",
        "reset_layout",
    ]
    assert _entries_of(dumps["dock-actions-none"], "&View") == [
        "menu:Appearance",
        "menu:Layout",
        "menu:Sort",
        "menu:Filters",
        "menu:Review View",
        "---",
        "show_workspace_toolbar",
        "menu:Toolbar Position",
        "reset_layout",
    ]


def test_each_top_level_menu_in_the_full_scenario(dumps) -> None:
    lines = dumps["all-present"]
    assert _entries_of(lines, "&File") == ["open_folder", "refresh_folder", "new_folder", "empty_recycle_bin", "---", "exit_app"]
    assert _entries_of(lines, "&Edit") == [
        "undo", "---", "rename_selection", "accept_selection", "reject_selection", "keep_selection", "move_selection",
        "move_selection_to_new_folder", "delete_selection", "restore_selection",
    ]
    assert _entries_of(lines, "&Review") == [
        "open_preview", "winner_ladder_mode", "---", "rename_selection", "accept_selection", "reject_selection",
        "keep_selection", "move_selection", "move_selection_to_new_folder", "delete_selection", "restore_selection",
        "---", "reveal_in_explorer", "open_in_photoshop",
    ]
    assert _entries_of(lines, "&Library") == ["menu:Collections", "menu:Catalog"]
    assert _entries_of(lines, "&Workflow") == [
        "share_to_phone", "---", "handoff_builder", "send_to_editor_pipeline", "best_of_set_auto_assembly", "menu:Recipes",
    ]
    assert _entries_of(lines, "&AI") == [
        "guided_ai_cull_preferences", "open_ai_workflow_center", "---", "menu:Run And Apply", "menu:Results And Filters",
        "menu:Review Tools", "---", "menu:AI Setup And Cache",
    ]
    assert _entries_of(lines, "&Tools") == [
        "open_command_palette", "---", "batch_rename_selection", "batch_resize_selection", "batch_convert_selection",
        "---", "extract_archive", "---", "menu:Diagnostics",
    ]
    assert _entries_of(lines, "&Settings") == ["workflow_settings", "file_associations", "---", "reset_layout"]
    assert _entries_of(lines, "&Help") == [
        "documentation", "---", "keyboard_help", "ai_guide", "ai_review_tag_legend", "advanced_help", "---",
        "check_for_updates", "---", "about",
    ]


def test_optional_menus_appear_only_when_supplied(dumps) -> None:
    def titles(scenario: str) -> set[str]:
        return {line.split("title=")[1].split(" object=")[0].strip("'") for line in dumps[scenario] if line.lstrip().startswith("> menu")}

    full = titles("all-present")
    assert {"Recipes", "Workspaces", "Collections List", "Catalog List"} <= full
    for scenario, gone in (
        ("no-recipe-menu", "Recipes"),
        ("no-preset-menu", "Workspaces"),
        ("no-collections-menu", "Collections List"),
        ("no-catalog-menu", "Catalog List"),
    ):
        assert gone not in titles(scenario), scenario
        assert titles(scenario) == full - {gone}, scenario
    assert not (titles("no-optional-menus") & {"Recipes", "Workspaces", "Collections List", "Catalog List"})
    # Panels / Panel Layout need dock actions; a window without docks still gets the toolbar position menu.
    for scenario in ("dock-actions-none", "dock-actions-empty"):
        assert {"Panels", "Panel Layout"}.isdisjoint(titles(scenario)), scenario
    assert {"Panels", "Panel Layout", "Toolbar Position"} <= titles("dock-actions-unknown-key-only")


def test_the_save_workspace_action_rides_with_the_preset_menu(dumps) -> None:
    assert any("save_workspace_preset" in line for line in dumps["all-present"])
    assert not any("save_workspace_preset" in line and not line.startswith("fields not") for line in dumps["no-preset-menu"])


def test_actions_with_no_menu_entry_are_the_known_set(dumps) -> None:
    """Palette/toolbar-only actions. If one of these gains a menu entry (or one is
    dropped from a menu) this list changes - on purpose."""
    line = next(line for line in dumps["all-present"] if line.startswith("fields not in any menu: "))
    assert line == (
        "fields not in any menu: ['download_ai_model', 'run_ai_culling', 'load_saved_ai', 'load_ai_results', "
        "'clear_ai_results', 'review_ai_adapter_labels', 'review_ai_disagreements', 'keyboard_shortcuts', "
        "'ai_state_actions[AIStateFilter.ALL]', 'ai_state_actions[AIStateFilter.GROUPED]', "
        "'ai_state_actions[AIStateFilter.PENDING]', 'ai_state_actions[AIStateFilter.OBVIOUS_WINNERS]', "
        "'ai_state_actions[AIStateFilter.LIKELY_KEEPERS]', 'ai_state_actions[AIStateFilter.DISAGREEMENTS]']"
    )


def test_inline_entries_call_the_window(dumps) -> None:
    """The entries made with ``addAction(text, slot)`` rather than from a
    ``MainWindowActions`` field, and the call each one makes."""
    inline = []
    for line in dumps["all-present"]:
        if "INLINE" in line and not line.split("text=")[1].split(" shortcuts=")[0].endswith(" entry'"):
            text = line.split("text=")[1].split(" shortcuts=")[0].strip("'")
            inline.append((text, line.split("slots=")[1]))
    panel_calls = lambda key: [  # noqa: E731
        ("Show Expanded", f"{{'triggered': [('docks.expand_panel', ('{key}',), {{}})]}}"),
        ("Collapse To Tab", f"{{'triggered': [('docks.collapse_panel', ('{key}',), {{}})]}}"),
        ("Hide", f"{{'triggered': [('docks.hide_panel', ('{key}',), {{}})]}}"),
        ("Dock Left", f"{{'triggered': [('docks.dock_to_side', ('{key}', 'left'), {{'show_after': True}})]}}"),
        ("Dock Right", f"{{'triggered': [('docks.dock_to_side', ('{key}', 'right'), {{'show_after': True}})]}}"),
        ("Pop Out", f"{{'triggered': [('docks.pop_out_panel', ('{key}',), {{}})]}}"),
    ]
    assert inline == [
        ("Top", "{'triggered': [('_toolbar.set_workspace_bar_position', ('top',), {})]}"),
        ("Bottom", "{'triggered': [('_toolbar.set_workspace_bar_position', ('bottom',), {})]}"),
        *panel_calls("library"),
        *panel_calls("inspector"),
        ("Swap Left And Right Panels", "{'triggered': [('docks.swap_sides', (), {})]}"),
    ]


def test_real_main_window_menu_bar_has_the_same_top_level_menus(main_window) -> None:
    from PySide6.QtWidgets import QMenuBar

    menu_bar = main_window.menuBar()
    assert isinstance(menu_bar, QMenuBar)
    titles = [action.menu().title() for action in menu_bar.actions() if action.menu() is not None]
    assert titles == _TOP_LEVEL_TITLES
    assert all(isinstance(action.menu(), QMenu) for action in menu_bar.actions())
