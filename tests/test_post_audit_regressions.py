"""Regression tests for the crashes found by the post-remediation audit (docs/post_remediation_audit.md, F-01..F-04).

Each one is a call that named something deleted or moved during the WI-2.x / WI-4.4 clean-up and was never exercised by a
test (they sit behind a modal dialog, a menu, or a header overflow). They run on a real ``MainWindow``.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QDialog, QMenu

import image_triage.window as window_module
from tests.harness import install_dialog_guards, make_jpegs
from tests.test_preview_lazy_build import _fresh_window


def test_show_hidden_folders_toggle_rescans_the_current_folder(tmp_path, monkeypatch):
    """F-02: the View action used to raise AttributeError (``_current_path_for_index``) before rescanning."""
    install_dialog_guards(monkeypatch)
    folder = tmp_path / "shoot"
    folder.mkdir()
    make_jpegs(folder, ["a.jpg", "b.jpg"])
    with _fresh_window() as window:
        window._current_folder = str(folder)
        window._scope_kind = "folder"
        window._handle_show_hidden_folders_toggled(True)
        assert window._show_hidden_folders is True
        window._handle_show_hidden_folders_toggled(False)
        assert window._show_hidden_folders is False


def test_folder_menu_add_to_library_starts_indexing(tmp_path, monkeypatch):
    """F-03: choosing "Add To Library" used to raise AttributeError (``_start_catalog_refresh``) after adding the root."""
    install_dialog_guards(monkeypatch)
    folder = tmp_path / "shoot"
    folder.mkdir()
    started: list[tuple] = []

    class _PickAddToLibraryMenu(QMenu):
        # QMenu.exec cannot be patched on the class (PySide resolves it natively), and the real one would block.
        def exec(self, *args, **kwargs):
            return next(action for action in self.actions() if action.text() == "Add To Library")

    monkeypatch.setattr(window_module, "QMenu", _PickAddToLibraryMenu)
    with _fresh_window() as window:
        monkeypatch.setattr(window._library_store, "add_catalog_root", lambda path: None)
        monkeypatch.setattr(
            window._catalog,
            "start_catalog_refresh",
            lambda roots, *, label: started.append((tuple(roots), label)) or True,
        )
        window._show_folder_context_menu(str(folder), QPoint(0, 0), is_favorite=False)
    assert started and started[0][0] == (str(folder),)


def test_ai_setup_dialog_accepted_returns_a_selection(monkeypatch):
    """F-01: accepting the dialog used to raise NameError (``semantic_missing``)."""
    install_dialog_guards(monkeypatch)
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    with _fresh_window() as window:
        selection = window._ai_setup.show_ai_setup_dialog(
            automatic=False,
            title="Set Up AI",
            prompt_text="",
            allow_runtime=True,
            allow_model=True,
            default_install_runtime=True,
            default_include_torch_runtime=True,
            default_download_aiculler_clip_model=False,
            default_download_aiculler_topiq_model=False,
            default_download_aiculler_face_model=False,
            default_download_semantic_model=False,
        )
    assert selection is not None
    assert selection.download_semantic_model is False


def test_preview_overflow_review_menu_reflects_the_bracket_button(monkeypatch):
    """F-04: the overflow "Review" group read ``self._auto_bracket_enabled``, which only the main window has."""
    install_dialog_guards(monkeypatch)
    with _fresh_window() as window:
        preview = window.preview
        preview._preview_header_overflow_hidden_groups = ("review",)
        for checked in (True, False):
            preview.set_auto_bracket_mode(checked)
            preview._populate_header_overflow_menu()
            review_action = next(a for a in preview.preview_header_overflow_menu.actions() if a.text() == "Review")
            auto_bracket = next(a for a in review_action.menu().actions() if a.text() == "Auto-Bracket")
            assert auto_bracket.isChecked() is checked

