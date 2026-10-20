"""A setup run on the real window: the progress view, the chip's percent, cancel, and the endings. The installers are not started; their callbacks are fed by hand."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from image_triage.ai_health import CapabilityHealth
from image_triage.ai_runtime_packages import AI_RUNTIME_GPU_VARIANT
from image_triage.ai_status import AIState


@pytest.fixture
def setup(main_window, monkeypatch):
    main_window.resize(1400, 800)
    main_window.show()
    QApplication.processEvents()
    controller = main_window._ai_setup
    monkeypatch.setattr(controller, "missing_capability_bundles", lambda: ("maniqa", "faces_blink"))
    monkeypatch.setattr(controller, "core_installed", lambda: False)
    warnings: list[str] = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(str(a[2])) or QMessageBox.StandardButton.Ok))
    controller._test_warnings = warnings
    yield controller
    if controller._progress_dialog is not None:
        controller._progress_dialog.close()
    controller._progress = None
    controller._last_ai_readiness_results = {}  # the window is shared between tests
    main_window._ai_status.close_card()
    main_window.hide()


def status(window):
    window._ai_status.refresh()
    QApplication.processEvents()
    return window._ai_status.status()


def test_a_run_shows_its_steps_and_the_chip_shows_the_percent(setup, main_window) -> None:
    setup.begin_setup_progress(variant=AI_RUNTIME_GPU_VARIANT, runtime_missing=True)
    dialog = setup._progress_dialog
    assert dialog is not None and dialog.isVisible() and not dialog.isModal()
    assert dialog.step_states() == ["run", "todo", "todo"] and dialog.bar.value() == 0

    setup.handle_ai_runtime_install_progress("  Downloading torch-2.6.0-cp313-win_amd64.whl (3300.0 MB)")
    assert 0 < dialog.bar.value() < 50 and dialog.step_states()[0] == "run"
    setup.handle_ai_runtime_install_progress("Installing collected packages: torch")
    assert dialog.step_states() == ["done", "run", "todo"]
    percent = dialog.bar.value()

    # The chip says Installing with the same percent, while a task is active.
    main_window._active_ai_runtime_task = SimpleNamespace(cancel=lambda: None)
    try:
        installing = status(main_window)
        assert installing.state is AIState.INSTALLING and installing.percent == percent and installing.small == f"{percent}%"
        assert main_window.ai_status_chip.isVisible()
    finally:
        main_window._active_ai_runtime_task = None

    # The model downloads report through the busy message.
    setup.set_ai_setup_busy("Downloading MANIQA image quality ranker...")
    setup.set_ai_setup_busy("MANIQA image quality ranker: ckpt_koniq10k.pt 270 / 543 MB")
    assert dialog.bar.value() >= percent


def test_finishing_ready_turns_the_view_into_ai_is_ready(setup) -> None:
    setup.begin_setup_progress(variant=AI_RUNTIME_GPU_VARIANT, runtime_missing=False)
    dialog = setup._progress_dialog
    ready = CapabilityHealth(key="faces", name="Faces and people", summary="", ready=True, stage="ready")
    setup._progress.start_check()
    setup.handle_ai_readiness_finished({"faces": ready, "culling": ready}, "AI Setup", "The AI runtime was installed.")
    assert dialog.showing_end() and dialog.end_title.text() == "AI is ready"
    assert dialog.end_note.text() == "The AI runtime was installed."
    assert setup._progress is None


def test_a_failed_check_says_so_and_stays_in_front(setup, monkeypatch) -> None:
    import image_triage.ui.ai_readiness as readiness

    shown: list[str] = []
    monkeypatch.setattr(readiness.AIReadinessDialog, "exec", lambda self: shown.append("results") or 0)
    setup.begin_setup_progress(variant=AI_RUNTIME_GPU_VARIANT, runtime_missing=False)
    dialog = setup._progress_dialog
    dialog.hide()
    broken = CapabilityHealth(key="faces", name="Faces and people", summary="", ready=False, stage="import", message="mediapipe is missing")
    setup.handle_ai_readiness_finished({"faces": broken}, "AI Setup", "")
    assert dialog.showing_end() and dialog.end_title.text() == "AI setup did not finish"
    assert dialog.isVisible()  # a failure comes to the front even if the view was hidden


def test_cancel_stops_the_installer_and_the_next_step_never_starts(setup, main_window, monkeypatch) -> None:
    cancelled: list[str] = []
    main_window._active_ai_runtime_task = SimpleNamespace(cancel=lambda: cancelled.append("runtime"))
    started: list[str] = []
    monkeypatch.setattr(setup, "start_ai_capability_bundles", lambda **k: started.append("bundles"))
    monkeypatch.setattr(setup, "start_ai_model_download", lambda **k: started.append("models"))
    setup.begin_setup_progress(variant=AI_RUNTIME_GPU_VARIANT, runtime_missing=True)
    dialog = setup._progress_dialog
    dialog.cancel.click()
    assert cancelled == ["runtime"] and not dialog.cancel.isEnabled() and dialog.eta.text() == "Stopping..."
    # The installer reports the stop as a failure; nothing else starts and no warning pops up.
    setup.handle_ai_runtime_install_failed("Setup was cancelled.")
    assert dialog.showing_end() and "cancelled" in dialog.end_note.text()
    assert started == [] and setup._progress is None and setup._test_warnings == []
    main_window._active_ai_runtime_task = None


def test_a_finished_step_after_cancel_does_not_continue(setup, main_window, monkeypatch) -> None:
    started: list[str] = []
    monkeypatch.setattr(setup, "start_ai_capability_bundles", lambda **k: started.append("bundles"))
    setup.begin_setup_progress(variant=AI_RUNTIME_GPU_VARIANT, runtime_missing=True)
    setup.cancel_setup()
    setup.handle_ai_runtime_install_finished("root", AI_RUNTIME_GPU_VARIANT)
    assert started == [] and setup._progress is None


def test_show_details_and_cancel_on_the_chip_card_reach_the_run(setup, main_window) -> None:
    main_window._active_ai_runtime_task = SimpleNamespace(cancel=lambda: None)
    try:
        setup.begin_setup_progress(variant=AI_RUNTIME_GPU_VARIANT, runtime_missing=True)
        setup._progress_dialog.hide()
        main_window._ai_status.refresh()
        main_window.ai_status_chip.click()
        QApplication.processEvents()
        popover = main_window._ai_status._popover
        buttons = popover.findChildren(QPushButton)
        assert [b.text() for b in buttons] == ["Show details", "Cancel"]
        next(b for b in buttons if b.text() == "Show details").click()
        assert setup._progress_dialog.isVisible()
    finally:
        main_window._active_ai_runtime_task = None
