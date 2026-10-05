"""WI-8.1 stage B: the popout viewer (``MainWindow.preview``) is built on first
use instead of inside ``MainWindow.__init__``.

Building ``FullScreenPreview`` costs ~0.6 s and used to be ~70% of window
construction, yet nothing needs it until the user opens the popout. These tests
pin the three halves of that contract:

* nothing that merely *mentions* the viewer (startup, theme, density, settings,
  rating, the command palette, closing) may build it - a tripwire on
  ``FullScreenPreview.__init__`` finds any stray reference;
* a lazily built viewer must be configured exactly as an eagerly built one was
  (every window-level setting changed before the build is replayed into it, and
  every one changed afterwards still reaches it);
* property semantics: one build, a non-forcing accessor, an opt-in idle pre-build.
"""
from __future__ import annotations

import os
import traceback
from contextlib import contextmanager
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication, QDialog

from image_triage.preview import FullScreenPreview
from image_triage.ui.display_metrics import display_profile_for_preference
from image_triage.ui.shortcuts import save_shortcut_overrides
from image_triage.ui.theme import AppearanceMode
from image_triage.window import MainWindow
from tests.harness import (
    dispose_window,
    make_jpegs,
    open_folder,
    prepare_application,
    pump_until,
    restore_app_state,
    snapshot_app_state,
)


def _turn(ms: int = 30) -> None:
    """A real event-loop turn (``processEvents`` alone never runs ``deleteLater``)."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _no_app_wide_style(*_args, **_kwargs) -> None:
    return None


@contextmanager
def _fresh_window(*, seed=None, **window_kwargs):
    """A brand-new real window (the shared fixture window may already have a
    built preview). ``seed(settings)`` runs before construction so the window
    starts from non-default persisted preferences, like a returning user's.

    The app-wide stylesheet/palette is left alone: applying it repolishes every
    live widget, and late in a full run the widgets leaked by thousands of
    earlier tests make a single apply take minutes. The viewer's own styling is
    per-widget, so nothing these tests check depends on it."""
    from image_triage.app_identity import user_settings

    app = prepare_application()
    save_shortcut_overrides({})
    if seed is not None:
        seed(user_settings())
    snapshot = snapshot_app_state()
    with mock.patch.object(QApplication, "setStyleSheet", _no_app_wide_style), mock.patch.object(
        QApplication, "setPalette", _no_app_wide_style
    ):
        window = MainWindow(**window_kwargs)
        app.processEvents()
        try:
            yield window
        finally:
            dispose_window(window)
    restore_app_state(snapshot)
    save_shortcut_overrides({})


@pytest.fixture
def tripwire():
    """Armed ``FullScreenPreview.__init__``: records who built it and refuses to,
    so a stray ``window.preview`` fails loudly instead of costing 0.6 s."""
    calls: list[str] = []

    def boom(self, *args, **kwargs):
        calls.append("".join(traceback.format_stack(limit=14)[:-1]))
        raise RuntimeError("FullScreenPreview constructed while the tripwire is armed")

    with mock.patch.object(FullScreenPreview, "__init__", boom):
        yield calls


def _assert_quiet(calls: list[str], step: str) -> None:
    assert not calls, f"the popout viewer was built during: {step}\n{calls[0]}"


# ---------------------------------------------------------------------------------------------
# The tripwire
# ---------------------------------------------------------------------------------------------
def test_startup_and_everyday_use_never_build_the_preview(tripwire, dialogs, tmp_path) -> None:
    with _fresh_window() as window:
        _assert_quiet(tripwire, "MainWindow.__init__")
        assert window._preview_ctl.preview_if_built() is None

        window.show()
        _turn(400)  # _post_show_display_setup, _finish_startup_restore, startup focus timers...
        _assert_quiet(tripwire, "show() and the startup timers")

        window._appearance.set_appearance_mode(AppearanceMode.LIGHT)
        window._ui_gamma = 1.3
        window._appearance.apply_appearance()
        _turn()
        _assert_quiet(tripwire, "_apply_appearance (theme and gamma change)")

        window._interface_size = "compact"
        window._display_profile = None
        window._appearance.apply_display_profile()
        window.resize(1180, 720)
        _turn(60)
        _assert_quiet(tripwire, "_apply_display_profile / resize")

        window._inspector.update_action_states()
        window._settings_ctl.apply_shortcut_overrides()
        _assert_quiet(tripwire, "_update_action_states / _apply_shortcut_overrides")

        window._views.handle_auto_advance_toggled(False)
        window._views.handle_auto_advance_toggled(True)
        window._views.handle_auto_bracket_toggled(False)
        window.actions.compare_mode.setChecked(True)
        window.actions.compare_mode.setChecked(False)
        _assert_quiet(tripwire, "auto-advance / auto-bracket / compare toggles")

        folder = tmp_path / "shoot"
        make_jpegs(folder, [f"IMG_{index}.jpg" for index in range(6)])
        open_folder(window, folder, 6)
        _turn(60)
        _assert_quiet(tripwire, "opening a folder")

        window.grid.set_current_index(1)
        window.grid.step_current(1)
        window._annotation_ctl.toggle_winner(2)
        window._annotation_ctl.toggle_reject(3)
        _turn(60)
        _assert_quiet(tripwire, "selection / winner / reject changes")

        window._command_palette.build_commands("main")
        window._command_palette.open()
        assert window._active_command_palette is not None
        window._active_command_palette.reject()
        _turn()
        _assert_quiet(tripwire, "command palette build_commands / open / dismiss")

        window._catalog.begin_collection_mode("create")
        window._catalog.cancel_collection_mode()
        _assert_quiet(tripwire, "collection mode on and off")

        window._zen.set_zen_mode(True)
        window._zen.set_zen_mode(False)
        _assert_quiet(tripwire, "zen mode on and off")

        with mock.patch.object(window, "_exec_dialog_with_geometry", side_effect=_accept_changed_settings):
            window._settings_ctl.show_settings()
        _turn()
        _assert_quiet(tripwire, "the Settings dialog applying changes")

        window.close()
        _turn()
        _assert_quiet(tripwire, "closing the window")
        assert window._preview_ctl.preview_if_built() is None

    # Positive control: the wire does fire when something really asks for the viewer.
    with _fresh_window() as window:
        with pytest.raises(RuntimeError, match="tripwire"):
            window.preview
        assert len(tripwire) == 1, "a real use of window.preview must reach the builder"
        assert window._preview_ctl.preview_if_built() is None, "a failed build must not leave a half-built viewer behind"


def _accept_changed_settings(dialog, _key):
    """Stand-in for exec(): change the settings the viewer mirrors, press OK."""
    dialog.preview_preload_batch_spin.setValue(37)
    dialog.ui_gamma_slider.setValue(125)
    dialog.interface_size_combo.setCurrentIndex(dialog.interface_size_combo.findData("spacious"))
    dialog.auto_advance_checkbox.setChecked(not dialog.auto_advance_checkbox.isChecked())
    return QDialog.DialogCode.Accepted


# ---------------------------------------------------------------------------------------------
# Property semantics
# ---------------------------------------------------------------------------------------------
def test_first_access_builds_exactly_once_and_the_accessor_never_builds(dialogs) -> None:
    built: list[FullScreenPreview] = []
    real_init = FullScreenPreview.__init__

    def counting(self, *args, **kwargs):
        built.append(self)
        real_init(self, *args, **kwargs)

    with _fresh_window() as window:
        assert window._preview_ctl.preview_if_built() is None
        assert window._preview_ctl.preview_is_visible() is False
        assert built == []

        with mock.patch.object(FullScreenPreview, "__init__", counting):
            first = window.preview
            second = window.preview
            assert first is second
            assert len(built) == 1
            assert window._preview_ctl.preview_if_built() is first
            assert first.parent() is window
            assert window._preview_ctl.preview_is_visible() is False  # built, but not on screen
            assert window._preview_navigation_dirty is False


def test_closing_a_window_with_an_unbuilt_preview_does_not_build_it(tripwire, dialogs) -> None:
    with _fresh_window() as window:
        window.show()
        _turn(100)
        window.close()
        _turn()
        _assert_quiet(tripwire, "closing a window whose preview was never built")
        assert window._preview_ctl.preview_if_built() is None


# ---------------------------------------------------------------------------------------------
# Idle pre-build
# ---------------------------------------------------------------------------------------------
def test_deferred_build_builds_once_and_is_idempotent(dialogs) -> None:
    built: list[FullScreenPreview] = []
    real_init = FullScreenPreview.__init__

    def counting(self, *args, **kwargs):
        built.append(self)
        real_init(self, *args, **kwargs)

    with _fresh_window() as window:
        window.show()
        _turn(50)
        with mock.patch.object(FullScreenPreview, "__init__", counting):
            window.schedule_deferred_preview_build(delay_ms=0)
            window.schedule_deferred_preview_build(delay_ms=0)  # already scheduled: no second timer
            assert window._preview_ctl.preview_if_built() is None, "the build must wait for the event loop, not run inline"
            assert pump_until(lambda: window._preview_ctl.preview_if_built() is not None)
            _turn(50)
            assert len(built) == 1
            preview = window._preview_ctl.preview_if_built()
            window.schedule_deferred_preview_build(delay_ms=0)  # already built: a no-op
            _turn(50)
            assert len(built) == 1
            assert window.preview is preview


def test_deferred_build_waits_for_its_delay(dialogs) -> None:
    with _fresh_window() as window:
        window.show()
        window.schedule_deferred_preview_build(delay_ms=60_000)
        _turn(100)
        assert window._preview_ctl.preview_if_built() is None


def test_deferred_build_firing_after_the_window_closed_is_harmless(tripwire, dialogs) -> None:
    with _fresh_window() as window:
        window.show()
        _turn(50)
        window.schedule_deferred_preview_build(delay_ms=0)
        window.close()
        _turn(150)
        _assert_quiet(tripwire, "a deferred build firing after the window was closed")
        assert window._preview_ctl.preview_if_built() is None


def test_deferred_build_for_a_window_that_was_never_shown_is_skipped(tripwire, dialogs) -> None:
    with _fresh_window() as window:
        window.schedule_deferred_preview_build(delay_ms=0)
        _turn(100)
        _assert_quiet(tripwire, "a deferred build on a window that was never shown")


def test_a_destroyed_window_takes_its_pending_build_with_it(dialogs) -> None:
    with _fresh_window() as window:
        window.show()
        window.schedule_deferred_preview_build(delay_ms=50)
    _turn(150)  # the timer is the window's child: nothing may fire into a deleted window


def test_the_app_entry_point_opts_in_to_the_prebuild_and_a_quick_view_does_not() -> None:
    import ast
    import inspect
    import textwrap

    from image_triage import main as app_main

    tree = ast.parse(textwrap.dedent(inspect.getsource(app_main.main)))
    guarded = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            calls = [
                call
                for stmt in node.body
                for call in ast.walk(stmt)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "schedule_deferred_preview_build"
            ]
            if calls:
                guarded.append(ast.unparse(node.test))
    # Called once, and only for a normal launch: a quick view builds the viewer
    # synchronously when it opens its image, so a background build would be wasted.
    assert guarded == ["not quick_view"]


# ---------------------------------------------------------------------------------------------
# Equivalence: a lazily built viewer is configured like the eagerly built one was
# ---------------------------------------------------------------------------------------------
def _seed_returning_user(settings) -> None:
    """Non-default persisted preferences, so a viewer that merely used its own
    defaults (instead of the window's state) cannot pass by accident."""
    settings.setValue(MainWindow.AUTO_ADVANCE_KEY, False)
    settings.setValue(MainWindow.PREVIEW_PRELOAD_BATCH_SIZE_KEY, 23)
    settings.setValue(MainWindow.AUTO_BRACKET_KEY, False)
    settings.setValue(MainWindow.APPEARANCE_SLATE_MIGRATION_KEY, True)
    settings.setValue(MainWindow.APPEARANCE_KEY, AppearanceMode.MIDNIGHT.value)
    settings.setValue(MainWindow.UI_GAMMA_KEY, 1.2)
    settings.setValue(MainWindow.INTERFACE_SIZE_KEY, "compact")
    save_shortcut_overrides(
        {
            "keep_at_cursor": "Ctrl+Alt+F10",
            "tag_at_cursor": "Ctrl+Alt+F9",
            "accept_selection": "Q",
            "reject_selection": "Ctrl+Alt+X",
            "open_command_palette": "Ctrl+Alt+K",
        }
    )


def _assert_viewer_mirrors_window(window, preview) -> None:
    """Every window-held setting the viewer consumes, read back through the viewer."""
    from image_triage.ui.shortcuts import effective_shortcuts, load_shortcut_overrides

    assert preview._theme == window._theme
    assert preview._display_profile == window._display_profile
    assert preview._auto_advance_enabled == window._auto_advance_enabled
    assert preview.preload_batch_size() == window._preview_preload_batch_size
    assert preview.auto_bracket_button.isChecked() == window._auto_bracket_enabled
    assert preview.compare_mode() == window._compare_enabled
    assert preview.compare_toggle_button.isChecked() == window._compare_enabled
    assert preview.compare_count_combo.isEnabled() == window._compare_enabled
    assert preview._photoshop_available == bool(window._photoshop_executable)
    assert preview.photoshop_button.isEnabled() == (bool(window._photoshop_executable) and not window._collection_mode)
    assert preview._collection_browse_mode == bool(window._collection_mode)

    assert preview._winner_shortcut == QKeySequence(window.actions.accept_selection.shortcut())
    assert preview._reject_shortcut == QKeySequence(window.actions.reject_selection.shortcut())
    review_keys = effective_shortcuts(window._REVIEW_KEY_BINDING_IDS, load_shortcut_overrides())
    for binding_id, sequence in preview._review_key_shortcuts.items():
        assert sequence == QKeySequence(review_keys[binding_id]), binding_id

    shortcut = window._command_palette_shortcut_preview
    assert shortcut is not None and shortcut.parent() is preview
    assert shortcut.key() == window._command_palette_shortcut_main.key()
    assert shortcut.isEnabled() == window._command_palette_shortcut_main.isEnabled()


def _widget_styles(preview) -> list[tuple[str, str, str]]:
    from PySide6.QtWidgets import QWidget

    return [(type(w).__name__, w.objectName(), w.styleSheet()) for w in preview.findChildren(QWidget)]


def test_a_lazily_built_viewer_replays_every_setting_changed_before_the_build(dialogs) -> None:
    with _fresh_window(seed=_seed_returning_user) as window:
        window.show()
        _turn(300)
        assert window._preview_ctl.preview_if_built() is None

        # Everything the viewer mirrors, changed through the real code paths, with no viewer around.
        window._photoshop_executable = r"C:\Fake\Photoshop.exe"
        window._appearance.set_appearance_mode(AppearanceMode.LIGHT)
        window._ui_gamma = 0.85
        window._appearance.apply_appearance()
        with mock.patch.object(window, "_exec_dialog_with_geometry", side_effect=_accept_changed_settings):
            window._settings_ctl.show_settings()  # preload 37, gamma 1.25, spacious, auto-advance flipped
        window._views.handle_auto_bracket_toggled(True)
        window.actions.compare_mode.setChecked(True)
        save_shortcut_overrides(
            {"keep_at_cursor": "Ctrl+Alt+F8", "accept_selection": "E", "open_command_palette": "Ctrl+Alt+J"}
        )
        window._settings_ctl.apply_shortcut_overrides()
        window._catalog.begin_collection_mode("create")
        _turn(100)

        assert window._preview_ctl.preview_if_built() is None, "none of the above may have built the viewer"
        assert window._preview_preload_batch_size == 37
        assert window._compare_enabled is True

        preview = window.preview
        _assert_viewer_mirrors_window(window, preview)
        assert preview.preload_batch_size() == 37
        assert preview.compare_mode() is True and preview._collection_browse_mode is True
        assert preview._review_key_shortcuts["keep_at_cursor"] == QKeySequence("Ctrl+Alt+F8")
        assert preview._winner_shortcut == QKeySequence("E")
        assert window._command_palette_shortcut_preview.key() == QKeySequence("Ctrl+Alt+J")


def test_a_lazily_built_viewer_is_styled_like_a_hand_configured_one(dialogs) -> None:
    """Oracle for the theme/density half: the old startup built the viewer, applied the
    display profile, then the theme. Doing exactly that by hand on a standalone viewer
    must give widget-for-widget the same styling as the viewer the window builds lazily."""
    with _fresh_window(seed=_seed_returning_user) as window:
        window.show()
        _turn(300)
        lazy = window.preview
        reference = FullScreenPreview(window)  # parented like the window's own: the initial size depends on it
        try:
            reference.apply_display_profile(window._display_profile)
            reference.apply_theme(window._theme)
            assert lazy.styleSheet() == reference.styleSheet()
            assert lazy._studio_applied_key == reference._studio_applied_key
            assert _widget_styles(lazy) == _widget_styles(reference)
            assert lazy._display_profile == reference._display_profile
            assert lazy.width() == reference.width() and lazy.height() == reference.height()
        finally:
            reference.close()
            reference.deleteLater()


def test_settings_changed_after_the_build_still_reach_the_viewer(dialogs) -> None:
    with _fresh_window(seed=_seed_returning_user) as window:
        window.show()
        _turn(300)
        preview = window.preview
        _assert_viewer_mirrors_window(window, preview)

        window._appearance.set_appearance_mode(AppearanceMode.LIGHT)
        window._ui_gamma = 0.9
        window._appearance.apply_appearance()
        assert preview._theme == window._theme and window._theme.name == "light"

        window._interface_size = "spacious"
        window._display_profile = None
        window._appearance.apply_display_profile()
        assert preview._display_profile == window._display_profile
        assert preview._display_profile == display_profile_for_preference(
            window.central_container.width(), window.central_container.height(), "spacious"
        )

        window._views.handle_auto_advance_toggled(True)
        assert preview._auto_advance_enabled is True
        window._views.handle_auto_bracket_toggled(True)
        assert preview.auto_bracket_button.isChecked() is True
        window.actions.compare_mode.setChecked(True)
        assert preview.compare_mode() is True
        window.actions.compare_mode.setChecked(False)
        assert preview.compare_mode() is False

        window._catalog.begin_collection_mode("create")
        assert preview._collection_browse_mode is True
        window._catalog.cancel_collection_mode()
        assert preview._collection_browse_mode is False

        with mock.patch.object(window, "_exec_dialog_with_geometry", side_effect=_accept_changed_settings):
            window._settings_ctl.show_settings()
        assert preview.preload_batch_size() == 37 == window._preview_preload_batch_size
        assert preview._auto_advance_enabled == window._auto_advance_enabled

        save_shortcut_overrides(
            {"keep_at_cursor": "Ctrl+Alt+F7", "reject_selection": "Ctrl+Alt+Y", "open_command_palette": "Ctrl+Alt+L"}
        )
        window._settings_ctl.apply_shortcut_overrides()
        _assert_viewer_mirrors_window(window, preview)
        assert preview._review_key_shortcuts["keep_at_cursor"] == QKeySequence("Ctrl+Alt+F7")
        assert window._command_palette_shortcut_preview.key() == QKeySequence("Ctrl+Alt+L")


def test_the_palette_shortcut_made_with_a_late_viewer_follows_the_windows_state(dialogs) -> None:
    with _fresh_window(seed=_seed_returning_user) as window:
        window._command_palette.set_shortcuts_enabled(False)  # e.g. a palette is open right now
        preview = window.preview
        shortcut = window._command_palette_shortcut_preview
        assert shortcut.parent() is preview
        assert shortcut.key() == QKeySequence("Ctrl+Alt+K") and not shortcut.isEnabled()

        window._command_palette.set_shortcuts_enabled(True)
        assert shortcut.isEnabled()

        shortcut.activated.emit()  # wired to the palette, in the preview context
        assert window._active_command_palette is not None
        assert "preview" in window._command_palette_dialogs
        window._active_command_palette.reject()
        _turn()


def test_signals_wired_by_the_builder_reach_the_window(dialogs, tmp_path) -> None:
    folder = tmp_path / "shoot"
    make_jpegs(folder, ["a.jpg", "b.jpg"])
    with _fresh_window() as window:
        window.show()
        open_folder(window, folder, 2)
        preview = window.preview

        target = next(record.path for record in window._records if record.path.endswith("a.jpg"))
        preview.rating_requested.emit(target, 4)
        assert window._annotations[target].rating == 4

        window._preview_navigation_dirty = True
        preview.closed.emit()
        assert window._preview_navigation_dirty is False

        preview.command_palette_requested.emit()
        assert window._active_command_palette is not None
        assert window._active_command_palette is window._command_palette_dialogs["preview"]
        window._active_command_palette.reject()
        _turn()


# ---------------------------------------------------------------------------------------------
# Quick view
# ---------------------------------------------------------------------------------------------
def test_a_quick_view_launch_builds_the_viewer_when_it_opens_the_image(dialogs, tmp_path) -> None:
    (image,) = make_jpegs(tmp_path / "launch", ["only.jpg"])
    with _fresh_window(launch_target=image, quick_view=True) as window:
        assert window._preview_ctl.preview_if_built() is None, "construction alone must still not build it"
        assert pump_until(window._preview_ctl.preview_is_visible, timeout=20), "the quick view never opened its image"
        preview = window._preview_ctl.preview_if_built()
        assert preview is not None and preview is window.preview
        assert preview.isVisible() and window._pending_quick_view_path == ""
        window._quick_view_mode = False  # closing a real quick view quits the application
        preview.close()
        _turn()
