from __future__ import annotations


def test_real_main_window_builds_headlessly(main_window, dialogs) -> None:
    assert main_window.windowTitle() == "Image Triage"
    assert main_window._settings.fileName().endswith(".ini")


def test_shared_window_state_is_reset_between_tests(main_window) -> None:
    assert main_window._undo_stack == [] and main_window._annotations == {} and main_window._records == []
