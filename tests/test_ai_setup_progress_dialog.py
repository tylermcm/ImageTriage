"""The Set Up AI progress view: steps, bar, cancel, and the ready and failed endings."""
from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication

from image_triage.ui.ai_setup_progress_dialog import AISetupProgressDialog


@pytest.fixture
def dialog():
    QApplication.instance() or QApplication([])
    d = AISetupProgressDialog()
    d.show()
    QApplication.processEvents()
    yield d
    d.close()


def test_the_steps_follow_the_progress(dialog) -> None:
    dialog.show_progress(0, 12, "about 8 min left")
    assert dialog.step_states() == ["run", "todo", "todo"]
    assert dialog.bar.value() == 12 and dialog.eta.text() == "about 8 min left"
    dialog.show_progress(1, 55, "about 3 min left")
    assert dialog.step_states() == ["done", "run", "todo"]
    dialog.show_progress(2, 96, "")
    assert dialog.step_states() == ["done", "done", "run"]
    assert not dialog.showing_end()


def test_the_dialog_is_not_modal_and_keeps_one_size(dialog) -> None:
    size = dialog.size()
    assert not dialog.isModal()
    dialog.show_progress(1, 40, "")
    dialog.show_ready("done")
    dialog.show_failed("it broke")
    assert dialog.size() == size


def test_cancel_asks_to_stop_and_then_waits(dialog) -> None:
    asked: list[bool] = []
    dialog.cancel_requested.connect(lambda: asked.append(True))
    dialog.cancel.click()
    assert asked == [True]
    dialog.show_stopping()
    assert not dialog.cancel.isEnabled() and dialog.eta.text() == "Stopping..."
    dialog.show_progress(0, 5, "about 1 min left")
    assert dialog.cancel.isEnabled()


def test_ready_and_failed_endings(dialog) -> None:
    dialog.show_ready("Open this page any time from the AI chip.")
    assert dialog.showing_end() and dialog.end_title.text() == "AI is ready"
    assert dialog.step_states() == ["done", "done", "done"]
    dialog.show_failed("The download stopped.")
    assert dialog.end_title.text() == "AI setup did not finish" and dialog.end_note.text() == "The download stopped."
    dialog.show_progress(0, 0, "")  # a new run comes back to the bar
    assert not dialog.showing_end()
    dialog.show_ready("")
    dialog.done_button.click()
    assert not dialog.isVisible()
