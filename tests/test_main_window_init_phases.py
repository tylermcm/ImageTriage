"""Audit finding A2, stage 3: ``MainWindow.__init__`` is a short, ordered sequence of phases.

``MainWindow.__init__`` used to be one ~1,200-line function. It is now ``super().__init__()``
followed by calls to private ``_init_*`` phase methods, each a verbatim contiguous slice of the old
body, defined directly below ``__init__`` in call order. Because construction order carries hidden
dependencies (an attribute must exist before a later block reads it, signals connect after the
objects they connect, ``build_main_menu_bar`` needs ``actions.ai_state_actions`` already filled by
the record-filter actions, deferred ``QTimer.singleShot`` callbacks fire in registration order), the
sequence is pinned here so it cannot be reshuffled or quietly regrow:

* ``__init__`` is only ``super().__init__()`` plus calls to the phases in ``INIT_PHASES`` order;
* every phase exists right after ``__init__`` in call order, is private, is called from nowhere
  else, and stays under ``MAX_PHASE_LINES`` lines;
* a phase never reads (outside a deferred lambda/closure) a ``self`` attribute that only a LATER
  phase assigns - the cheap static form of "attribute A exists before block B reads it";
* the few cross-phase ordering rules the stage-3 audit found are asserted explicitly;
* a real, fully constructed window (the shared fixture) has at least one object per phase.

Adding a phase means updating ``INIT_PHASES`` and ``PHASE_PROMISES`` together. The promises are
deliberately minimal - one or two objects per phase, not the whole attribute set - so ordinary
feature work that adds an attribute never touches this file.
"""
from __future__ import annotations

import ast
import functools
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QMetaMethod, QObject, QThreadPool
from PySide6.QtGui import QShortcut
from PySide6.QtWidgets import QMenu, QStackedWidget

import image_triage.window as window_module
from image_triage.filtering import AIStateFilter
from image_triage.ui.actions import MainWindowActions
from image_triage.window import MainWindow

# The phases, in the order ``__init__`` must call them.
INIT_PHASES: tuple[str, ...] = (
    "_init_window_frame_and_launch_state",
    "_init_settings_and_appearance_prefs",
    "_init_core_services",
    "_init_thread_pools_and_controllers",
    "_init_background_indexing_state",
    "_init_scan_task_and_search_state",
    "_init_enrichment_and_ai_review_label_state",
    "_init_job_contexts_and_scope_state",
    "_init_records_controllers_and_state",
    "_init_view_state_and_preferences",
    "_init_cull_thresholds_and_display_policy",
    "_init_session_and_saved_collections",
    "_init_filter_metadata_and_folder_watching",
    "_init_folder_tree_and_drive_list",
    "_init_left_rail_section_widgets",
    "_init_left_rail_pages_and_nav_rail",
    "_init_left_panel_layout",
    "_init_path_controls_and_combos",
    "_init_actions_and_shortcuts",
    "_init_inspector_menus_and_filter_buttons",
    "_init_workspace_toolbars",
    "_init_workspace_bar_and_mode_bars",
    "_init_center_column_and_docks",
    "_init_menu_bar_and_zen_menu",
    "_init_central_container_and_top_bar",
    "_init_status_bar",
    "_init_view_signal_connections",
    "_init_appearance_restore_and_startup_timers",
)

MAX_INIT_LINES = 60       # was ~1,195
MAX_PHASE_LINES = 300     # the largest phase today is ~92; a phase this long should be split

# One or two things each phase structurally promises to have built (checked on a live window).
PHASE_PROMISES: dict[str, tuple[str, ...]] = {
    "_init_window_frame_and_launch_state": ("_startup_launch_target", "_pending_quick_view_path"),
    "_init_settings_and_appearance_prefs": ("_settings", "_topbar_slots"),
    "_init_core_services": ("thumbnail_manager", "grid", "details_view"),
    "_init_thread_pools_and_controllers": ("_scan_pool", "_catalog", "_folder_ops"),
    "_init_background_indexing_state": ("_semantic_index_pool", "_face_index_pool"),
    "_init_scan_task_and_search_state": ("_recycle_bin", "_annotation_reapply_timer"),
    "_init_enrichment_and_ai_review_label_state": ("_review_chunk_flush_timer", "_scope_enrichment_debounce_timer"),
    "_init_job_contexts_and_scope_state": ("_job_controllers", "_archive_job_key"),
    "_init_records_controllers_and_state": ("_records_repo", "_record_ops", "_records_view", "_command_palette"),
    "_init_view_state_and_preferences": ("_filter_query", "_zen_menu_reveal_timer"),
    "_init_cull_thresholds_and_display_policy": ("_effective_loupe_card_style", "_display_class_value"),
    "_init_session_and_saved_collections": ("_session_id", "_annotation_persistence_queue", "_undo_stack"),
    "_init_filter_metadata_and_folder_watching": ("_filter_metadata_manager", "_folder_watcher"),
    "_init_folder_tree_and_drive_list": ("folder_model", "folder_tree", "drive_list"),
    "_init_left_rail_section_widgets": ("favorites_list", "face_groups_panel", "projects_list"),
    "_init_left_rail_pages_and_nav_rail": ("left_nav_pages", "left_nav_rail"),
    "_init_left_panel_layout": ("left_panel",),
    "_init_path_controls_and_combos": ("manual_path_combo", "sort_combo", "columns_combo"),
    "_init_actions_and_shortcuts": ("actions", "_toolbar_menus", "_zen_toggle_shortcut"),
    "_init_inspector_menus_and_filter_buttons": ("inspector_panel", "filter_toolbar_menu", "manual_search_field"),
    "_init_workspace_toolbars": ("manual_toolbar", "ai_toolbar", "toolbar_stack"),
    "_init_workspace_bar_and_mode_bars": ("workspace_bar", "tool_mode_bar", "collection_mode_bar"),
    "_init_center_column_and_docks": ("workspace_docks", "browser_stack", "workspace_center_layout"),
    "_init_menu_bar_and_zen_menu": ("menu_corner_widget", "_zen_menu_animation"),
    "_init_central_container_and_top_bar": ("app_top_bar", "central_container", "summary_strip"),
    "_init_status_bar": ("filter_summary_label", "clear_filters_button"),
    "_init_view_signal_connections": ("grid", "details_view"),
    "_init_appearance_restore_and_startup_timers": ("_startup_window_state",),
}


# --------------------------------------------------------------------------- AST helpers
@functools.lru_cache(maxsize=1)
def _class_ast() -> ast.ClassDef:
    # window.py is ~24,000 lines; the helpers below re-ask for the class per phase, so parse it once.
    tree = ast.parse(Path(window_module.__file__).read_text(encoding="utf-8"))
    return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MainWindow")


def _methods(cls: ast.ClassDef) -> list[ast.FunctionDef]:
    return [n for n in cls.body if isinstance(n, ast.FunctionDef)]


def _lines(fn: ast.FunctionDef) -> int:
    return fn.end_lineno - fn.lineno + 1


def _self_attr_calls(node: ast.AST) -> list[str]:
    """Names of ``self.<name>(...)`` calls anywhere under ``node`` (document order)."""
    out = []
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and isinstance(sub.func.value, ast.Name)
            and sub.func.value.id == "self"
        ):
            out.append(sub.func.attr)
    return out


def _phase_bodies() -> dict[str, ast.FunctionDef]:
    by_name = {m.name: m for m in _methods(_class_ast())}
    return {name: by_name[name] for name in INIT_PHASES}


def _phase_index_calling(call_name: str) -> int:
    """Index in INIT_PHASES of the phase whose body calls ``call_name`` (bare or ``self.``)."""
    found = []
    for index, name in enumerate(INIT_PHASES):
        for sub in ast.walk(_phase_bodies()[name]):
            if isinstance(sub, ast.Call):
                func = sub.func
                if (isinstance(func, ast.Name) and func.id == call_name) or (
                    isinstance(func, ast.Attribute) and func.attr == call_name
                ):
                    found.append(index)
                    break
    assert len(found) == 1, f"{call_name!r} should be called by exactly one phase, found in {found}"
    return found[0]


# --------------------------------------------------------------------------- structure
def test_init_is_super_plus_the_phase_calls_in_order() -> None:
    cls = _class_ast()
    init = next(m for m in _methods(cls) if m.name == "__init__")
    assert _lines(init) <= MAX_INIT_LINES, f"__init__ regrew to {_lines(init)} lines; add a phase method instead"
    first, *rest = init.body
    assert ast.unparse(first) == "super().__init__()", "super().__init__() must stay the first statement"
    called = []
    for stmt in rest:
        assert (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Call)
            and isinstance(stmt.value.func, ast.Attribute)
            and isinstance(stmt.value.func.value, ast.Name)
            and stmt.value.func.value.id == "self"
        ), f"__init__ may only call phase methods, found: {ast.unparse(stmt)}"
        called.append(stmt.value.func.attr)
    assert tuple(called) == INIT_PHASES, (
        "MainWindow.__init__ must call its phases in the documented order (INIT_PHASES). "
        f"first mismatch: {next((c for c, p in zip(called, INIT_PHASES) if c != p), None)!r}; "
        f"called {len(called)} phases, expected {len(INIT_PHASES)}"
    )


def test_phase_methods_follow_init_in_call_order_and_stay_small() -> None:
    methods = _methods(_class_ast())
    names = [m.name for m in methods]
    start = names.index("__init__") + 1
    assert tuple(names[start : start + len(INIT_PHASES)]) == INIT_PHASES, "phases must sit directly below __init__, in call order"
    for method in methods[start : start + len(INIT_PHASES)]:
        assert method.name.startswith("_init_"), f"{method.name}: phase methods are private and use the _init_ prefix"
        assert _lines(method) <= MAX_PHASE_LINES, f"{method.name} is {_lines(method)} lines; split it"
        assert method.decorator_list == [], f"{method.name}: phases are plain methods"
    assert len(set(INIT_PHASES)) == len(INIT_PHASES)


def test_phases_are_only_called_from_init() -> None:
    cls = _class_ast()
    for method in _methods(cls):
        if method.name == "__init__":
            continue
        called = set(_self_attr_calls(method)) & set(INIT_PHASES)
        assert not called, f"{method.name} calls construction phase(s) {sorted(called)}; they must run exactly once, from __init__"


def test_phase_promises_cover_every_phase() -> None:
    assert tuple(PHASE_PROMISES) == INIT_PHASES


def test_no_phase_reads_an_attribute_only_a_later_phase_assigns() -> None:
    """Static ordering guard: a direct ``self.x`` read in phase N must not depend on phase M > N.

    Reads inside lambdas / nested functions are deferred (they run on a signal, long after
    construction), so they are exempt, as are names that are class attributes, methods or
    properties of ``MainWindow``/``QMainWindow``.
    """
    bodies = _phase_bodies()
    stored_in: dict[str, int] = {}
    for index, name in enumerate(INIT_PHASES):
        for sub in ast.walk(bodies[name]):
            if (
                isinstance(sub, ast.Attribute)
                and isinstance(sub.ctx, ast.Store)
                and isinstance(sub.value, ast.Name)
                and sub.value.id == "self"
            ):
                stored_in.setdefault(sub.attr, index)

    def direct_loads(fn: ast.FunctionDef) -> set[str]:
        loads: set[str] = set()

        def visit(node: ast.AST) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue  # deferred code
                if (
                    isinstance(child, ast.Attribute)
                    and isinstance(child.ctx, ast.Load)
                    and isinstance(child.value, ast.Name)
                    and child.value.id == "self"
                ):
                    loads.add(child.attr)
                visit(child)

        visit(fn)
        return loads

    violations = []
    for index, name in enumerate(INIT_PHASES):
        for attr in sorted(direct_loads(bodies[name])):
            first_store = stored_in.get(attr)
            if first_store is not None and first_store > index and not hasattr(MainWindow, attr):
                violations.append(f"{name} reads self.{attr}, first assigned in {INIT_PHASES[first_store]}")
    assert not violations, "\n".join(violations)


def test_cross_phase_ordering_rules_found_in_the_stage_3_audit() -> None:
    first = INIT_PHASES.index
    # self._preview must exist before anything can reach the lazily built viewer: first statements of the first phase.
    frame = _phase_bodies()[INIT_PHASES[0]]
    stored = [
        sub.attr
        for stmt in frame.body[:3]
        for sub in ast.walk(stmt)
        if isinstance(sub, ast.Attribute) and isinstance(sub.ctx, ast.Store)
    ]
    assert "_preview" in stored, "self._preview = None must be set at the very top of construction"

    # the settings store is opened before any phase reads it
    settings_phase = first("_init_settings_and_appearance_prefs")
    for name in INIT_PHASES[:settings_phase]:
        assert "_settings" not in {
            sub.attr for sub in ast.walk(_phase_bodies()[name]) if isinstance(sub, ast.Attribute)
        }, f"{name} touches self._settings before it is opened"

    # actions are built, then the record-filter actions fill actions.ai_state_actions, then the menu bar reads it
    build_actions = _phase_index_calling("build_main_window_actions")
    filter_actions = _phase_index_calling("build_record_filter_actions")
    menu_bar = _phase_index_calling("build_main_menu_bar")
    docks = _phase_index_calling("build_workspace_docks")
    assert build_actions <= filter_actions < menu_bar, "build_main_menu_bar reads actions.ai_state_actions"
    assert docks < menu_bar, "build_main_menu_bar is handed workspace_docks.toggle_actions"
    # the controllers that fill the filter actions exist before the actions are built
    assert _phase_index_calling("RecordsViewController") < filter_actions
    # the top bar hosts actions, so it is built after them
    assert build_actions < _phase_index_calling("build_prototype_top_bar")
    # the window state is restored after the docks it restores exist, and last
    assert docks < _phase_index_calling("restore_window_state") == len(INIT_PHASES) - 1


# --------------------------------------------------------------------------- a real window
def _signal_connected(obj: QObject, name: str) -> bool:
    meta = obj.metaObject()
    for i in range(meta.methodCount()):
        method = meta.method(i)
        if method.methodType() == QMetaMethod.MethodType.Signal and bytes(method.name()).decode() == name:
            return obj.isSignalConnected(method)
    raise AssertionError(f"{type(obj).__name__} has no signal {name!r}")


@pytest.mark.parametrize("phase", INIT_PHASES)
def test_constructed_window_has_what_each_phase_promises(main_window, phase: str) -> None:
    for attr in PHASE_PROMISES[phase]:
        assert getattr(main_window, attr, None) is not None, f"{phase} should have built self.{attr}"


def test_constructed_window_object_types_and_wiring(main_window) -> None:
    window = main_window
    assert window.windowTitle() == "Image Triage"
    assert isinstance(window.actions, MainWindowActions)
    assert isinstance(window._scan_pool, QThreadPool) and window._scan_pool.maxThreadCount() == 1
    assert isinstance(window._zen_toggle_shortcut, QShortcut)
    assert window.left_nav_pages.count() == 4 and isinstance(window.left_nav_pages, QStackedWidget)
    assert window.toolbar_stack.count() == 2
    assert window.browser_stack.count() == 2
    assert window.centralWidget() is window.central_container
    assert window.statusBar() is not None
    # phases that connect signals ran after the objects they connect existed
    assert _signal_connected(window.grid, "current_changed")
    assert _signal_connected(window.folder_tree, "clicked")
    assert _signal_connected(window.thumbnail_manager, "thumbnail_ready")
    assert _signal_connected(window._annotation_persistence_queue, "failed")
    # the startup restore (last phase) decided how the window opens
    assert window._startup_window_state in {"maximized", "fullscreen"}


def test_ai_state_actions_are_filled_before_the_menu_bar_reads_them(main_window) -> None:
    """Runtime half of the ordering rule asserted statically above."""
    window = main_window
    assert set(window.actions.ai_state_actions) == set(AIStateFilter)
    assert window._ai_state_actions == window.actions.ai_state_actions
    menu_actions = {action for menu in window.menuBar().findChildren(QMenu) for action in menu.actions()}
    # the three the AI results menu lists (ui/menus.py: add_ai_results_actions)
    for mode in (AIStateFilter.TOP_PICKS, AIStateFilter.NEEDS_REVIEW, AIStateFilter.LIKELY_REJECTS):
        assert window.actions.ai_state_actions[mode] in menu_actions, f"{mode} filter action did not reach the main menu bar"
