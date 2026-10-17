"""Characterization test for ``CommandPaletteController.build_commands`` (audit finding A2, stage 2).

``build_commands`` used to be a single ~470-line method with an inner
``add_action_command`` closure. It is now an orchestrator that calls one private
section method per palette section (``_add_file_and_edit_commands``,
``_add_view_commands``, ``_add_ai_commands``, ..., ``_add_preview_commands``).
The palette's result order, every title / subtitle / section / shortcut /
keyword tuple and what each command *does* are user-visible, so this module pins
them for a grid of window states.

Each state pins everything ``build_commands`` reads on the shared real
``MainWindow`` (view flags, saved presets / recipes / workspaces, library
collections and catalog roots, recent folders, which actions are enabled and
the shortcut each one shows, the dock toggle actions, a stand-in popout viewer)
and then dumps the commands:
id, title, subtitle, section, shortcut, keywords, and the effect of the callback
(``action.trigger`` callbacks are named by their action; every other callback is
*called* with the window's handlers replaced by recorders, so no real handler
runs). The dumps are pinned by SHA-256. This says the palette is *unchanged*, not
that it is right.

If a palette change is intentional, regenerate the digest table:

    # PowerShell
    $env:UPDATE_BUILDER_SNAPSHOTS = "1"
    pythonw3.13.exe scripts/run313.py regen.log pytest -q -s tests/test_palette_commands_snapshot.py
    Remove-Item Env:UPDATE_BUILDER_SNAPSHOTS

then paste the printed table (also written to
``<temp dir>/image_triage_test_palette_commands_snapshot.txt``) over ``_GOLDEN``
below, update the readable expectations the change touched, and review the diff.
The catalog / recent-folder ids embed Windows-normalized paths.

Convention note: the states are computed once per module from the session's
shared window (``_shared_main_window`` in ``conftest.py``, the instance the
``main_window`` fixture hands out) because twelve states per test would be slow;
every attribute they pin is restored afterwards.
"""
from __future__ import annotations

import itertools
import os
import re
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from tests import builder_snapshot_support as support

# state label -> (sha256 of the dump, its length in characters)
_GOLDEN: dict[str, tuple[str, int]] = {
    "main|all-enabled|flags-on|data|docks": ("c2c02236cbb051330b43ac96ea033228482d063fb572b7ee594d093c793f3272", 49000),
    "main|all-enabled|flags-off|data|docks-unchecked": ("6d41d2ddd52667f0ca6971ab7e66586364f0e4b84fb974301d91af537384c7d5", 49016),
    "main|all-disabled|no-data|no-docks": ("ccc2c940bd8f4e2384c01384f4eac2073d9a5dc1a4902f3e80781fbb6ef7f916", 13856),
    "main|alternate-enabled|flags-on|data|docks": ("c3067548a0d4f8e245d2111386dad2e42a01169c7fd91a2fcd3b4e927819bdc2", 36405),
    "main|alternate-enabled|flags-off|no-data|docks": ("703296e4054f766a3014e8a3068bae6f592bb9ace50f45f67599bc80e463eb17", 28113),
    "main|no-actions|data|no-docks": ("bd0990f0680a55502caf607033c776214e51996d19fe1be0184bd9bf5bbd8df9", 22177),
    "main|visible-preview-ignored": ("d1902dd036a4bc682bd395fe8859a74528ce545c9f5bde36e85fb20f01670268", 49280),
    "preview|hidden-preview": ("6403e02b5b07f8f1671042ba1e24bf313caef7a9ecfef31b6613c3584a196ded", 49189),
    "preview|no-preview-built": ("00144bfca87da461dfa37977af63d02ee79e1285a7a2baca7fe4765194e0d730", 48991),
    "preview|visible|full": ("7d1411bbc84af5074ae992f0cb9a11b5aa8ceb6b8ecc1b08484b5a0409df0479", 55444),
    "preview|visible|focused-photoshop-missing-exe": ("79f05b00ea6fcd5923bd849283962ea69913e90c6b21aa123d9b2f0912b2ea34", 55184),
    "preview|visible|bare": ("1a7197dcfa980f538c9d41fd9882744b5b6b8478bbeb9e4a0a9f831605f45550", 27025),
}

_COUNTS = {
    "main|all-enabled|flags-on|data|docks": 176,
    "main|all-enabled|flags-off|data|docks-unchecked": 176,
    "main|all-disabled|no-data|no-docks": 18,
    "main|alternate-enabled|flags-on|data|docks": 112,
    "main|alternate-enabled|flags-off|no-data|docks": 90,
    "main|no-actions|data|no-docks": 40,
    "main|visible-preview-ignored": 176,
    "preview|hidden-preview": 176,
    "preview|no-preview-built": 176,
    "preview|visible|full": 200,
    "preview|visible|focused-photoshop-missing-exe": 199,
    "preview|visible|bare": 59,
}

_FULL = "main|all-enabled|flags-on|data|docks"
_NOTHING_ENABLED = "main|all-disabled|no-data|no-docks"


@pytest.fixture(scope="module")
def states(_shared_main_window) -> dict[str, tuple[list[str], list[str]]]:
    """State label -> (dump lines, ordered command ids)."""
    window = _shared_main_window
    enabled_before = {label: action.isEnabled() for label, action in support.action_fields(window.actions)}
    result = support.synthetic_palette_snapshots(window)
    enabled_after = {label: action.isEnabled() for label, action in support.action_fields(window.actions)}
    changed = sorted(label for label in enabled_before if enabled_before[label] != enabled_after.get(label))
    assert not changed, f"the palette states must put every action's enabled flag back; changed: {changed}"
    return result


def test_palette_commands_are_unchanged(states) -> None:
    support.assert_matches_golden(
        module="test_palette_commands_snapshot",
        table_name="_GOLDEN",
        golden=_GOLDEN,
        actual={label: support.digest_lines(lines) for label, (lines, _ids) in states.items()},
        what="The command palette produced by CommandPaletteController.build_commands",
        dumps={label: lines for label, (lines, _ids) in states.items()},
    )


# --- Readable facts (independent of the digest) --------------------------------
def _sections(lines: list[str]) -> list[tuple[str, int]]:
    names = [re.search(r"section='([^']*)'", line).group(1) for line in lines[1:]]
    return [(name, len(list(group))) for name, group in itertools.groupby(names)]


def test_command_counts_per_state(states) -> None:
    assert {label: len(ids) for label, (_lines, ids) in states.items()} == _COUNTS


def test_ids_are_unique_within_every_state(states) -> None:
    for label, (_lines, ids) in states.items():
        assert len(ids) == len(set(ids)), f"{label}: duplicate command ids {[i for i in ids if ids.count(i) > 1]}"


def test_section_order_with_everything_enabled(states) -> None:
    """The palette's order: the action sections first, then the per-mode loops,
    then docks, filter presets, recipes, workspace presets, collections, catalog
    roots and recent folders."""
    lines, _ids = states[_FULL]
    assert _sections(lines) == [
        ("File", 4),
        ("Edit", 2),
        ("Tools", 6),
        ("Export", 6),
        ("Library", 16),
        ("Review", 12),
        ("View", 11),
        ("Search", 4),
        ("AI", 29),
        ("Workspace", 1),
        ("Help", 6),
        ("Appearance", 10),
        ("View", 26),  # sort 6 + quick filters 12 + columns 8
        ("Workspace", 3),  # dock toggles
        ("Search", 10),  # smart filters 8 + saved searches 2
        ("Export", 7),  # recipes 5 + saved recipes 2
        ("Workspace", 7),  # workspace presets 5 + saved 2
        ("Library", 10),  # collections 2 x (open, add selection, add visible, set active) + catalog roots 2
        ("Review", 6),  # recent folders, capped at 6 of the 8 offered
    ]


def test_only_presets_remain_when_nothing_is_enabled_and_nothing_is_saved(states) -> None:
    _lines, ids = states[_NOTHING_ENABLED]
    assert ids == [
        "smart_filter.unreviewed_raw",
        "smart_filter.ai_top_picks_pending",
        "smart_filter.likely_winners",
        "smart_filter.needs_review",
        "smart_filter.near_duplicates",
        "smart_filter.ai_disagreements",
        "smart_filter.ai_ingested",
        "smart_filter.edited",
        "workflow_recipe.proofing_jpegs",
        "workflow_recipe.client_delivery",
        "workflow_recipe.edit_queue",
        "workflow_recipe.send_to_editor",
        "workflow_recipe.archive_selection",
        "workspace_preset.fast_culling",
        "workspace_preset.compare_mode",
        "workspace_preset.ai_review",
        "workspace_preset.metadata_audit",
        "workspace_preset.delivery_export",
    ]


def test_without_actions_only_the_data_driven_sections_remain(states) -> None:
    lines, ids = states["main|no-actions|data|no-docks"]
    assert _sections(lines) == [("Search", 10), ("Export", 7), ("Workspace", 7), ("Library", 10), ("Review", 6)]
    assert ids[0] == "smart_filter.unreviewed_raw"


def test_preview_commands_need_the_preview_context_and_a_visible_viewer(states) -> None:
    def preview_ids(label: str) -> list[str]:
        return [command_id for command_id in states[label][1] if command_id.startswith("preview.")]

    for label in ("main|visible-preview-ignored", "preview|hidden-preview", "preview|no-preview-built"):
        assert preview_ids(label) == [], label
    full = preview_ids("preview|visible|full")
    assert full == [
        "preview.close", "preview.previous", "preview.next", "preview.compare", "preview.zoom", "preview.fit",
        "preview.loupe", "preview.focus_assist", "preview.focus_assist_background",
        "preview.focus_assist_color.red", "preview.focus_assist_color.blue", "preview.focus_assist_color.yellow",
        "preview.focus_assist_color.white",
        "preview.focus_assist_strength.low", "preview.focus_assist_strength.medium", "preview.focus_assist_strength.strong",
        "preview.rename", "preview.accept", "preview.reject", "preview.keep", "preview.move", "preview.delete",
        "preview.tag", "preview.photoshop",
    ]
    # No Photoshop executable: that one command goes; no focused image: the per-image block goes.
    assert preview_ids("preview|visible|focused-photoshop-missing-exe") == full[:-1]
    assert preview_ids("preview|visible|bare") == full[:16]


def test_the_preview_block_comes_last(states) -> None:
    ids = states["preview|visible|full"][1]
    first_preview = next(index for index, command_id in enumerate(ids) if command_id.startswith("preview."))
    assert all(command_id.startswith("preview.") for command_id in ids[first_preview:])
    assert ids[:first_preview] == states["preview|hidden-preview"][1]


def test_disabled_actions_are_left_out(states) -> None:
    """Action-backed commands honour ``isEnabled``; the data-driven sections do not."""
    everything = set(states[_FULL][1])
    odd = set(states["main|alternate-enabled|flags-on|data|docks"][1])
    assert odd < everything
    assert {"file.open_folder", "smart_filter.edited", "saved_filter.golden search"} <= everything
    nothing = set(states[_NOTHING_ENABLED][1])
    assert not any(command_id.startswith(("file.", "edit.", "ai.", "help.")) for command_id in nothing)


def test_every_literal_command_id_in_the_source_is_exercised(states) -> None:
    """If a command is added to ``command_palette_controller.py`` without a state
    that emits it, this fails: extend ``PALETTE_STATES`` (or the fixtures) in
    ``tests/builder_snapshot_support.py`` and regenerate the digest table."""
    source = Path(support.__file__).resolve().parents[1] / "image_triage" / "command_palette_controller.py"
    text = source.read_text(encoding="utf-8")
    literal = set(re.findall(r'add_action_command\(\s*"([^"]+)"', text)) | set(re.findall(r'\bid="([^"]+)"', text))
    assert len(literal) >= 99
    emitted = {command_id for _lines, ids in states.values() for command_id in ids}
    assert not sorted(literal - emitted), f"command ids no pinned state emits: {sorted(literal - emitted)}"
    dynamic = re.findall(r'(?:add_action_command\(\s*|\bid=)f"([^"{]+)\{', text)
    for prefix in dynamic:
        assert any(command_id.startswith(prefix) for command_id in emitted), f"no pinned state emits {prefix}*"


def test_toggle_subtitles_follow_the_window_flags(states) -> None:
    def subtitle(label: str, command_id: str) -> str:
        line = next(line for line in states[label][0] if line.startswith(command_id + " | "))
        return re.search(r"subtitle='([^']*)'", line).group(1)

    for command_id in ("tools.performance_logging", "review.compare_mode", "review.auto_advance", "view.zen_mode", "view.burst_groups"):
        assert subtitle(_FULL, command_id) == "On", command_id
        assert subtitle("main|all-enabled|flags-off|data|docks-unchecked", command_id) == "Off", command_id
    assert subtitle(_FULL, "view.burst_stacks") == "Off"  # the one flag the states invert
    assert subtitle(_FULL, "view.grid_view") == "Current view"
    assert subtitle("main|all-enabled|flags-off|data|docks-unchecked", "view.details_view") == "Current view"
    assert subtitle(_FULL, "view.details_density_compact") == "Current density"
    assert subtitle("main|all-enabled|flags-off|data|docks-unchecked", "view.details_density_comfortable") == "Current density"


def test_dock_titles_follow_the_toggle_state(states) -> None:
    def title(label: str, command_id: str) -> str:
        line = next(line for line in states[label][0] if line.startswith(command_id + " | "))
        return re.search(r"title='([^']*)'", line).group(1)

    assert title(_FULL, "dock.library") == "Hide Library"
    assert title(_FULL, "dock.inspector") == "Show Inspector"
    assert title("main|all-enabled|flags-off|data|docks-unchecked", "dock.library") == "Show Library"
    assert title("main|all-enabled|flags-off|data|docks-unchecked", "dock.inspector") == "Hide Inspector"
    assert title(_FULL, "dock.mixed case") == "Hide Mixed Case"


def test_the_states_leave_the_shared_window_as_they_found_it(_shared_main_window, states) -> None:
    """The states patch the shared session window; every patch must be undone so
    later tests see the window they expect."""
    window = _shared_main_window
    for target in support._PALETTE_CALLBACK_TARGETS:
        owner, name = target if isinstance(target, tuple) else (None, target)
        holder = getattr(window, owner) if owner else window
        assert name not in holder.__dict__, f"{name} is still replaced by a recorder"
    assert "recent_destination_paths" not in window._navigation.__dict__
    assert "list_collections" not in window._library_store.__dict__
    assert "list_catalog_roots" not in window._library_store.__dict__
    assert not isinstance(window._preview_ctl.preview_if_built(), support.StubPreview)
    assert window.actions is not None
    assert type(window.workspace_docks).__name__ != "SimpleNamespace", "the stand-in dock object leaked"
