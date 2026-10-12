"""Two things the editor's control channel needs from its host after the upstream merge of Oct 10."""
from __future__ import annotations

from unittest.mock import Mock

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from image_triage import photocraft_bridge as bridge
from image_triage.preview_controller import PreviewController


def control_replying(*lines: bytes) -> bridge.PhotoCraftControl:
    control = object.__new__(bridge.PhotoCraftControl)
    control._next_id = 1
    control._file = Mock()
    control._file.readline.side_effect = list(lines)
    return control


def test_an_unreadable_reply_says_what_the_editor_sent():
    control = control_replying(b"\n")
    with pytest.raises(bridge.PhotoCraftError) as caught:
        control.call("jobs.list")
    message = str(caught.value)
    assert "jobs.list" in message and "unreadable" in message and "b'\\n'" in message


def test_a_closed_channel_is_still_reported_as_closed():
    control = control_replying(b"")
    with pytest.raises(bridge.PhotoCraftError, match="closed unexpectedly"):
        control.call("jobs.list")


def test_a_good_reply_still_returns_its_result():
    control = control_replying(b'{"id": 1, "ok": true, "result": {"jobs": []}}\n')
    assert control.call("jobs.list") == {"jobs": []}


def test_requests_from_two_threads_do_not_interleave_on_one_connection():
    """Each thread must read the reply to its own request: an exchange is one write and one read, never shared."""
    import json
    import threading
    import time

    control = object.__new__(bridge.PhotoCraftControl)
    control._next_id = 1
    inside, overlaps, pending = [0], [0], []

    class Channel:
        def write(self, data):
            inside[0] += 1
            overlaps[0] += inside[0] > 1
            pending.append(json.loads(data)["id"])

        def flush(self):
            time.sleep(0.002)  # long enough for another thread to barge in without the lock

        def readline(self):
            reply = json.dumps({"id": pending.pop(), "ok": True, "result": {}}).encode() + b"\n"
            inside[0] -= 1
            return reply

    control._file = Channel()
    errors = []

    def hammer():
        try:
            for _ in range(40):
                control.call("ui.viewport")
        except Exception as error:  # noqa: BLE001 - the test reports whatever went wrong
            errors.append(error)

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors, errors
    assert overlaps[0] == 0


def test_fitting_the_canvas_does_not_use_a_menu_click():
    """The editor refuses every menu command while Camera Raw is open, which in the workspace is nearly always."""
    app = QApplication.instance() or QApplication([])
    parent = QObject()
    controller = PreviewController(parent)
    try:
        proc = Mock()
        proc.is_running.return_value = True
        controller._photocraft_generation = 4
        controller._fit_photocraft(proc, 4)
        proc.control.call.assert_called_once_with("engine.execute", {"command": "view.fitOnScreen"})
        proc.control.call.reset_mock()
        controller._fit_photocraft(proc, 3)  # a stale request does nothing
        proc.control.call.assert_not_called()
    finally:
        controller._photocraft_save_timer.stop()
        controller._photocraft_dwell_timer.stop()
        controller._photocraft_executor.shutdown(wait=True)
        controller._photocraft_preview_executor.shutdown(wait=True)
        app.processEvents()
