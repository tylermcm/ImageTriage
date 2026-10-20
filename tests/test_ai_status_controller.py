"""The AI status chip on the real window: what shows where, the card that hangs from it, and the first-run prompt on the first Analyze."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication, QPushButton

from image_triage.ai_status import AIState
from image_triage.ai_status_controller import FIRST_RUN_KEY


@pytest.fixture
def shown(main_window):
    main_window.resize(1400, 800)
    main_window.show()
    QApplication.processEvents()
    yield main_window
    controller = main_window._ai_status
    controller.close_card()
    main_window.hide()


def set_ready(window, monkeypatch, ready: bool) -> None:
    monkeypatch.setattr(window._ai_setup, "core_installed", lambda: ready)
    monkeypatch.setattr(window._ai_setup, "missing_capability_bundles", lambda: ())
    window._ai_status.refresh()
    QApplication.processEvents()


def button_labels(popover) -> list[str]:
    return [b.text() for b in popover.findChildren(QPushButton)]


def test_without_a_core_the_chip_is_in_the_top_bar_and_the_status_bar_text_is_hidden(shown, monkeypatch) -> None:
    set_ready(shown, monkeypatch, False)
    chip, text = shown.ai_status_chip, shown.ai_status_text
    assert shown._ai_status.status().state is AIState.NEEDS_SETUP
    assert chip.isVisible() and not text.isVisible()
    assert chip.status().label == "Set up AI" and chip.status().small.endswith(("MB", "GB"))
    # It sits in the top bar, to the left of the search box, and its ring turns.
    bar = shown.app_top_bar
    assert chip.parent() is bar
    assert chip.geometry().right() <= shown._toolbar.app_search_box.geometry().left()
    assert chip.ring_running()


def test_when_everything_is_installed_only_quiet_text_remains(shown, monkeypatch) -> None:
    set_ready(shown, monkeypatch, True)
    assert shown._ai_status.status().state is AIState.READY
    assert shown.ai_status_text.isVisible() and not shown.ai_status_chip.isVisible()
    assert shown.ai_status_text.text() == "AI ready"
    # It lives with the other status-bar items, not in the message area a message would hide.
    assert shown.ai_status_text.parent() is shown.statusBar()
    shown.statusBar().showMessage("24 photos")
    QApplication.processEvents()
    assert shown.ai_status_text.isVisible()


def test_clicking_the_chip_opens_a_card_below_it_and_the_text_opens_one_above(shown, monkeypatch) -> None:
    set_ready(shown, monkeypatch, False)
    shown.ai_status_chip.click()
    popover = shown._ai_status._popover
    assert popover is not None and popover.isVisible()
    assert any(label.startswith("Set up AI") for label in button_labels(popover))
    assert "Not now" in button_labels(popover)
    chip_bottom = shown.ai_status_chip.mapToGlobal(shown.ai_status_chip.rect().bottomLeft()).y()
    assert popover.y() >= chip_bottom
    shown._ai_status.close_card()
    set_ready(shown, monkeypatch, True)
    shown.ai_status_text.click()
    above = shown._ai_status._popover
    assert above is not None and above.isVisible()
    assert "Open AI setup" in button_labels(above) and "Check installation" in button_labels(above)
    assert above.y() + above.height() <= shown.ai_status_text.mapToGlobal(shown.ai_status_text.rect().topLeft()).y() + 4


def test_an_install_in_progress_shows_installing_with_its_message(shown, monkeypatch) -> None:
    set_ready(shown, monkeypatch, False)
    monkeypatch.setattr(shown, "_active_ai_runtime_task", object())
    shown._ai_status.note_install_message("Downloading the AI runtime")
    assert shown._ai_status.status().state is AIState.INSTALLING
    assert shown._ai_status.status().detail == "Downloading the AI runtime"
    assert shown.ai_status_chip.isVisible()


def test_the_first_analyze_offers_setup_once(shown, monkeypatch) -> None:
    set_ready(shown, monkeypatch, False)
    shown._settings.setValue(FIRST_RUN_KEY, False)
    ran: list[str] = []
    monkeypatch.setattr(shown._ai_run, "run_ai_pipeline", lambda: ran.append("pipeline"))
    monkeypatch.setattr(shown._ai_run, "analysis_block_reason", lambda: "")
    shown._ai_run.analyze_current_folder()
    popover = shown._ai_status._popover
    assert popover is not None and popover.isVisible() and ran == []  # the prompt waits for an answer
    assert any(label.startswith("Set up AI") for label in button_labels(popover))
    next(b for b in popover.findChildren(QPushButton) if b.text() == "Analyze without it").click()
    assert ran == ["pipeline"]
    assert shown._settings.value(FIRST_RUN_KEY, False, bool) is True
    shown._ai_run.analyze_current_folder()  # answered once: never asked again
    assert ran == ["pipeline", "pipeline"]


def test_choosing_setup_opens_the_setup_dialog_and_does_not_analyze(shown, monkeypatch) -> None:
    set_ready(shown, monkeypatch, False)
    shown._settings.setValue(FIRST_RUN_KEY, False)
    opened: list[str] = []
    ran: list[str] = []
    monkeypatch.setattr(shown._ai_setup, "download_ai_model", lambda: opened.append("setup"))
    monkeypatch.setattr(shown._ai_run, "run_ai_pipeline", lambda: ran.append("pipeline"))
    monkeypatch.setattr(shown._ai_run, "analysis_block_reason", lambda: "")
    shown._ai_run.analyze_current_folder()
    popover = shown._ai_status._popover
    next(b for b in popover.findChildren(QPushButton) if b.text().startswith("Set up AI")).click()
    assert opened == ["setup"] and ran == []


def test_no_prompt_when_ai_is_already_set_up(shown, monkeypatch) -> None:
    set_ready(shown, monkeypatch, True)
    shown._settings.setValue(FIRST_RUN_KEY, False)
    ran: list[str] = []
    monkeypatch.setattr(shown._ai_run, "run_ai_pipeline", lambda: ran.append("pipeline"))
    monkeypatch.setattr(shown._ai_run, "analysis_block_reason", lambda: "")
    shown._ai_run.analyze_current_folder()
    assert ran == ["pipeline"] and shown._ai_status._popover is None


def test_a_model_pinned_to_a_new_revision_shows_as_an_update_with_its_models(shown, monkeypatch) -> None:
    import image_triage.ai_status_controller as module
    from image_triage.ai_model_store import STATE_MISSING, STATE_STALE

    set_ready(shown, monkeypatch, True)
    monkeypatch.setattr(shown._ai_setup, "missing_capability_bundles", lambda: ("faces_blink", "maniqa"))
    states = {"faces_blink": STATE_STALE, "maniqa": STATE_MISSING}
    monkeypatch.setattr(module, "bundle_status", lambda key: type("S", (), {"state": states[key]})())
    shown._ai_status.refresh()
    status = shown._ai_status.status()
    assert status.state is AIState.UPDATE
    assert status.detail.startswith("1 AI model changed")  # only the one that was installed from an older revision counts as changed
    assert [name for name, _size in status.items] == ["Face landmarks for the blink detector", "MANIQA image quality ranker"]
    assert shown.ai_status_chip.isVisible() and shown.ai_status_chip.status().label == "AI update"
    shown.ai_status_chip.click()
    popover = shown._ai_status._popover
    assert any(label.startswith("Update (") for label in button_labels(popover)) and "Later" in button_labels(popover)


def test_the_update_button_downloads_just_those_models_with_the_progress_view(shown, monkeypatch) -> None:
    set_ready(shown, monkeypatch, True)
    started: list[str] = []
    monkeypatch.setattr(shown._ai_setup, "missing_capability_bundles", lambda: ("maniqa",))
    monkeypatch.setattr(shown._ai_setup, "start_ai_capability_bundles", lambda **k: started.append(k["title"]))
    try:
        shown._ai_setup.update_ai_models()
        assert started == ["AI update"]
        dialog = shown._ai_setup._progress_dialog
        assert dialog is not None and dialog.isVisible()
        assert shown._ai_setup._progress is not None
        shown._ai_setup.update_ai_models()  # a second press while it runs only brings the view back
        assert started == ["AI update"]
    finally:
        shown._ai_setup._progress = None
        if shown._ai_setup._progress_dialog is not None:
            shown._ai_setup._progress_dialog.close()
