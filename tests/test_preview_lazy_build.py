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

from image_triage.shortcuts import save_shortcut_overrides
from image_triage.theme_tokens import AppearanceMode


def _turn(ms: int = 30) -> None:
    """A real event-loop turn (``processEvents`` alone never runs ``deleteLater``)."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def _no_app_wide_style(*_args, **_kwargs) -> None:
    return None






def _assert_quiet(calls: list[str], step: str) -> None:
    assert not calls, f"the popout viewer was built during: {step}\n{calls[0]}"


# ---------------------------------------------------------------------------------------------
# The tripwire
# ---------------------------------------------------------------------------------------------


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




# ---------------------------------------------------------------------------------------------
# Idle pre-build
# ---------------------------------------------------------------------------------------------










def test_retired_widgets_startup_is_not_an_application_entry_point() -> None:
    from image_triage import main as app_main

    assert not hasattr(app_main, "widgets_main")
    assert not hasattr(app_main, "wants_widgets_ui")




def _assert_viewer_mirrors_window(window, preview) -> None:
    """Every window-held setting the viewer consumes, read back through the viewer."""
    from image_triage.shortcuts import effective_shortcuts, load_shortcut_overrides

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












# ---------------------------------------------------------------------------------------------
# Quick view
# ---------------------------------------------------------------------------------------------
