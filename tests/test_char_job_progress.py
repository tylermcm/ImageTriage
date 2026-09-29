"""Characterization for WI-4.2: job/progress dialog consolidation.

`_update_progress_dialog` used to be shadowed by an instance attribute of the
same name (`self._update_progress_dialog: QProgressDialog | None = None`, set
in __init__ for the app-update download dialog). Because Python resolves an
instance attribute before a same-named class method, every call site that
tried to invoke `self._update_progress_dialog(dialog, current=..., ...)` -
resize, convert, archive create/extract, workflow export, catalog refresh,
and batch rename progress ticks - actually called `None(...)` (or later a
QProgressDialog instance) and raised TypeError, silently swallowed by Qt's
slot exception handling. None of these ticks had test coverage, so the bug
went unnoticed: those dialogs only ever showed "Preparing..." and a final
flash, never a live percentage. The fix removed the colliding attribute
(the app-update dialog now goes through the same `_job_controllers` /
JobController machinery as everything else), which is what these tests lock
in.
"""
from __future__ import annotations

from unittest.mock import patch

from PySide6.QtWidgets import QProgressDialog

from image_triage.job_controller import JobController


def test_update_progress_dialog_is_a_real_callable_method(main_window) -> None:
    assert "_update_progress_dialog" not in main_window.__dict__
    dialog = QProgressDialog(main_window)
    main_window._update_progress_dialog(
        dialog,
        current=3,
        total=10,
        message="Working on it...",
        default_label="fallback",
    )
    assert dialog.value() == 3
    assert dialog.maximum() == 10
    assert dialog.labelText() == "Working on it..."


def test_update_progress_dialog_falls_back_to_default_label_when_message_is_empty(main_window) -> None:
    dialog = QProgressDialog(main_window)
    main_window._update_progress_dialog(
        dialog, current=1, total=1, message="", default_label="fallback label"
    )
    assert dialog.labelText() == "fallback label"


def test_resize_progress_tick_updates_the_dialog_without_raising(main_window) -> None:
    main_window._handle_resize_started(5)
    main_window._handle_resize_progress(2, 5, "Saving b.jpg...")

    dialog = main_window._resize_progress_dialog
    assert dialog is not None
    assert dialog.value() == 2
    assert dialog.maximum() == 5
    assert dialog.labelText() == "Saving b.jpg..."
    main_window._close_resize_progress_dialog()


def test_catalog_refresh_progress_tick_updates_the_dialog_without_raising(main_window) -> None:
    catalog = main_window._catalog
    dialog = catalog._show_progress_dialog(3)
    catalog._handle_progress(1, 3, "Scanning C:/shots")

    assert dialog.value() == 1
    assert dialog.maximum() == 3
    assert dialog.labelText() == "Scanning C:/shots"
    catalog._close_progress_dialog()


def test_app_update_dialog_goes_through_job_controller_not_a_shadowed_attribute(main_window) -> None:
    dialog = main_window._show_update_progress_dialog()
    assert isinstance(dialog, QProgressDialog)
    assert isinstance(main_window._job_controllers.get("app_update"), JobController)

    main_window._handle_update_download_progress(5 * 1024 * 1024, 10 * 1024 * 1024, "installer.exe")
    assert dialog.labelText() == "Downloading installer.exe (5/10 MB)..."

    main_window._close_update_progress_dialog()
    assert "app_update" not in main_window._job_controllers


def test_ai_uninstall_dialog_goes_through_job_controller(main_window) -> None:
    with patch.object(main_window._ai_model_pool, "start") as fake_start:
        def run_synchronously(task):
            task.signals.finished.emit((0, [], []))

        fake_start.side_effect = run_synchronously
        main_window._run_ai_uninstall(())

    assert "ai_uninstall" not in main_window._job_controllers
