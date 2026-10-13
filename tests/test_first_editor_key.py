"""The first press of the editor key: nothing is waited for that does not have to be.

Measured in the real app on a 45 MP raw with the editor already started in the background: 1.85 s from the key to the editor on
screen, of which 0.3 s was a dwell meant for a selection that may move on, 0.73 s the editor developing the raw, and 0.13 s asking
for its theme. The key now skips the dwell, the photo on screen is developed ahead of the key, and the theme is asked for once.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from image_triage import photocraft_bridge as bridge
from image_triage.preview import FullScreenPreview
from image_triage.preview_controller import PreviewController


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def controller(qapp):
    parent = QObject()
    ctl = PreviewController(parent)
    ctl._parent_keepalive = parent
    yield ctl
    ctl._photocraft_save_timer.stop()
    ctl._photocraft_dwell_timer.stop()
    ctl._photocraft_rest_timer.stop()
    ctl._photocraft_executor.shutdown(wait=True)
    ctl._photocraft_preview_executor.shutdown(wait=True)
    qapp.processEvents()


def running_editor(tmp_path, *, camera_raw_first: bool = True):
    write_root = tmp_path / ".image_triage_edits"
    write_root.mkdir(exist_ok=True)
    control = Mock()
    control.camera_raw_supported = True
    control.camera_raw_prefetch_supported = True
    control.call.return_value = {"panel": "#111111", "accent": "#222222"}
    proc = bridge.PhotoCraftProcess(process=Mock(), control=control, hwnd=1, token_file=tmp_path / "token", read_root=tmp_path, write_root=write_root)
    proc.process.poll.return_value = None
    proc.camera_raw_first = camera_raw_first
    return proc


# --- 1. the key does not wait out the dwell -------------------------------------------------------------------

def test_the_editor_key_asks_for_the_photo_and_then_for_it_now(qapp, monkeypatch):
    preview = FullScreenPreview()
    try:
        preview.set_photocraft_available(True)
        preview._editor_on_demand = True
        preview._source_entries = [SimpleNamespace(record=SimpleNamespace(path="C:/photos/IMG.NEF"))]
        monkeypatch.setattr(preview, "show_browsing_picture", lambda: None)
        asked = []
        preview.photocraft_edit_requested.connect(lambda path: asked.append(("edit", path)))
        preview.photocraft_prioritize_requested.connect(lambda: asked.append(("now", None)))
        preview.open_editor()
        assert asked == [("edit", "C:/photos/IMG.NEF"), ("now", None)]
    finally:
        preview.photocraft_close_guard = None
        preview.close()


def test_asking_for_it_now_ends_a_running_dwell(controller):
    started = []
    controller._start_photocraft_raw_stage = lambda generation, path: started.append((generation, path))
    controller._photocraft_dwell_request = (7, "C:/photos/IMG.NEF")
    controller._photocraft_dwell_timer.start()
    controller.prioritize_photocraft_open()
    assert started == [(7, "C:/photos/IMG.NEF")] and not controller._photocraft_dwell_timer.isActive()


# --- 2. the photo on screen is developed ahead of the key ----------------------------------------------------

@pytest.fixture
def resting(controller, tmp_path, monkeypatch):
    """A raw on screen in the popout, the editor up but not in use. Returns (controller, editor, the photo, what was prefetched)."""
    proc = running_editor(tmp_path)
    controller._photocraft = proc
    photo = str(tmp_path / "IMG.NEF")
    state = SimpleNamespace(visible=True, editing=False)
    controller._window = SimpleNamespace(_preview=SimpleNamespace(isVisible=lambda: state.visible, photocraft_edit_active=lambda: state.editing))
    monkeypatch.setattr(bridge.PhotoCraftProcess, "path_within_root", lambda self, path: "IMG.NEF")
    monkeypatch.setattr(bridge, "file_signature", lambda path: "50000000:1700000000")
    sent = []
    controller._send_prefetch = lambda proc, relative, signature, key="": sent.append((relative, signature, key))

    def rest(path=photo):
        controller._rest_on_photo(path)
        controller._prefetch_rested_photo()
        controller._photocraft_preview_executor.submit(lambda: None).result()
        return sent

    return SimpleNamespace(controller=controller, proc=proc, photo=photo, state=state, rest=rest, sent=sent)


def test_a_raw_rested_on_is_developed_ahead_of_the_key(resting):
    (only,) = resting.rest()
    assert only[0] == "IMG.NEF" and only[1] == "50000000:1700000000"


def test_showing_a_photo_only_starts_the_wait(resting):
    resting.controller._rest_on_photo(resting.photo)
    assert resting.controller._photocraft_rest_timer.isActive() and resting.sent == []
    assert resting.controller._photocraft_rest_timer.interval() == PreviewController.REST_PREFETCH_MS


def test_showing_the_same_photo_again_does_not_start_the_wait_over(resting, qapp):
    """The popout shows the photo more than once as it settles; each time must not push the prefetch back."""
    import time

    timer = resting.controller._photocraft_rest_timer
    resting.controller._rest_on_photo(resting.photo)
    time.sleep(0.15)
    before = timer.remainingTime()
    resting.controller._rest_on_photo(resting.photo)
    assert timer.remainingTime() <= before, "the wait was not restarted"
    resting.controller._rest_on_photo(resting.photo.replace("IMG", "OTHER"))
    assert timer.remainingTime() > before, "another photo starts a wait of its own"


def test_moving_on_restarts_the_wait_and_nothing_to_show_cancels_it(resting):
    resting.controller._rest_on_photo(resting.photo)
    resting.controller._rest_on_photo(None)
    assert not resting.controller._photocraft_rest_timer.isActive()
    resting.controller._prefetch_rested_photo()
    assert resting.sent == []


def test_the_same_photo_is_not_developed_twice(resting):
    from image_triage.scanner import normalized_path_key

    resting.proc.prefetch_sent = ("IMG.NEF", normalized_path_key(resting.photo))
    assert resting.rest() == []


@pytest.mark.parametrize("why", ["editor in use", "popout hidden", "not a raw", "editor gone", "no prefetch support", "outside the editor's folder"])
def test_nothing_is_developed_ahead_when_it_would_not_help(resting, why, monkeypatch):
    photo = resting.photo
    if why == "editor in use":
        resting.state.editing = True  # it prepares the next photo itself
    elif why == "popout hidden":
        resting.state.visible = False
    elif why == "not a raw":
        photo = photo.replace(".NEF", ".jpg")
    elif why == "editor gone":
        resting.proc.process.poll.return_value = 1
    elif why == "no prefetch support":
        resting.proc.control.camera_raw_prefetch_supported = False
    elif why == "outside the editor's folder":
        monkeypatch.setattr(bridge.PhotoCraftProcess, "path_within_root", lambda self, path: None)
    assert resting.rest(photo) == []


def test_without_the_camera_raw_workspace_nothing_is_developed_ahead(controller, tmp_path, monkeypatch):
    proc = running_editor(tmp_path, camera_raw_first=False)
    controller._photocraft = proc
    controller._window = SimpleNamespace(_preview=SimpleNamespace(isVisible=lambda: True, photocraft_edit_active=lambda: False))
    sent = []
    controller._send_prefetch = lambda *args, **kwargs: sent.append(args)
    controller._rest_on_photo(str(tmp_path / "IMG.NEF"))
    controller._prefetch_rested_photo()
    controller._photocraft_preview_executor.submit(lambda: None).result()
    assert sent == []


# --- 3. the theme is asked for once per editor ----------------------------------------------------------------

def _open(controller, proc, tmp_path, monkeypatch, name):
    target = str(tmp_path / name)
    proc.current_source = "another photo"
    monkeypatch.setattr(controller, "_resolve_photocraft_target", lambda path: (target, str(proc.write_root / (name + ".pcraft"))))
    monkeypatch.setattr(bridge.PhotoCraftProcess, "path_within_root", lambda self, path: name)
    monkeypatch.setattr(bridge.PhotoCraftProcess, "path_within_write_root", lambda self, path: name + ".pcraft")
    monkeypatch.setattr(controller, "_camera_raw_mode", lambda path: False)
    seen = {}

    def switch(*args, **kwargs):
        seen["window_dealt_with"] = controller._photocraft_window_prepared.is_set()
        return True

    monkeypatch.setattr(controller, "_switch_photocraft_document", switch)
    controller._open_in_photocraft(None, target)
    return seen


def themes_asked(proc) -> int:
    return sum(1 for call in proc.control.call.call_args_list if call.args[0] == "ui.theme")


def test_the_theme_is_asked_for_at_the_first_open_only(controller, tmp_path, monkeypatch):
    proc = running_editor(tmp_path)
    proc.attached_parent = 555
    controller._photocraft = proc
    _open(controller, proc, tmp_path, monkeypatch, "A.NEF")
    assert themes_asked(proc) == 1 and proc.theme_synced
    _open(controller, proc, tmp_path, monkeypatch, "B.NEF")
    assert themes_asked(proc) == 1, "each request is ~120 ms the user waits for the editor"


def test_an_editor_started_in_the_background_reports_its_theme_then(controller, tmp_path, monkeypatch):
    proc = running_editor(tmp_path)
    monkeypatch.setattr(bridge, "launch_photocraft", lambda *args, **kwargs: proc)
    controller._prestart_photocraft(str(tmp_path))
    assert controller._photocraft is proc and themes_asked(proc) == 1 and proc.theme_synced
    proc.attached_parent = 555
    _open(controller, proc, tmp_path, monkeypatch, "A.NEF")
    assert themes_asked(proc) == 1, "the first photo does not wait for it"


def test_a_background_editor_that_cannot_report_its_theme_is_still_kept(controller, tmp_path, monkeypatch):
    proc = running_editor(tmp_path)
    proc.control.call.side_effect = bridge.PhotoCraftError("timeout")
    monkeypatch.setattr(bridge, "launch_photocraft", lambda *args, **kwargs: proc)
    controller._prestart_photocraft(str(tmp_path))
    assert controller._photocraft is proc and not proc.theme_synced


# --- the editor's window is in the popout before the open is sent --------------------------------------------

def test_the_open_is_sent_after_the_window_has_been_dealt_with(controller, tmp_path, monkeypatch):
    proc = running_editor(tmp_path)  # started in the background: no window of ours yet
    controller._photocraft = proc
    controller._window = SimpleNamespace(_preview=None)
    seen = _open(controller, proc, tmp_path, monkeypatch, "A.NEF")
    assert seen == {"window_dealt_with": True}


def test_the_window_step_always_reports_back(controller, tmp_path):
    """Also when there is nothing to do (no popout, a stale request): the worker must not wait out its timeout."""
    controller._window = SimpleNamespace(_preview=None)
    controller._photocraft_window_prepared.clear()
    controller._prepare_photocraft_window(controller._photocraft_generation, running_editor(tmp_path))
    assert controller._photocraft_window_prepared.is_set()


# --- coming back to a photo that was left ------------------------------------------------------------------------

def _reopen_same_photo(controller, proc, tmp_path, monkeypatch, *, camera_raw: bool):
    from image_triage.scanner import normalized_path_key

    target = str(tmp_path / "A.NEF")
    proc.current_source = normalized_path_key(target)
    proc.attached_parent = 555
    proc.control.document_revision.return_value = 3  # the document is still open in the editor
    controller._photocraft = proc
    monkeypatch.setattr(controller, "_resolve_photocraft_target", lambda path: (target, str(proc.write_root / "A.NEF.pcraft")))
    monkeypatch.setattr(bridge.PhotoCraftProcess, "path_within_root", lambda self, path: "A.NEF")
    monkeypatch.setattr(bridge.PhotoCraftProcess, "path_within_write_root", lambda self, path: "A.NEF.pcraft")
    monkeypatch.setattr(controller, "_camera_raw_mode", lambda path: camera_raw)
    monkeypatch.setattr(controller, "_camera_raw_spec", lambda path, proc: {"recipePath": "A.NEF.cameraraw.json"})
    opened = []
    monkeypatch.setattr(controller, "_switch_photocraft_document", lambda *args, **kwargs: opened.append(kwargs.get("camera_raw")) or True)
    controller._open_in_photocraft(None, target)
    return opened


def test_a_photo_left_and_come_back_to_opens_in_camera_raw_again(controller, tmp_path, monkeypatch):
    """Leaving a photo (the editor key, or closing the popout) settles it, which closes its Camera Raw dialog. Coming back used to
    find "the photo is already open" and show the bare document: the photo came up in the full editor by itself."""
    proc = running_editor(tmp_path)
    controller._settle_photocraft_after_leaving(proc)
    assert proc.left
    assert _reopen_same_photo(controller, proc, tmp_path, monkeypatch, camera_raw=True) == [{"recipePath": "A.NEF.cameraraw.json"}]
    assert not proc.left, "it is open again"


def test_closing_the_popout_counts_as_leaving_the_photo(controller, tmp_path, monkeypatch):
    proc = running_editor(tmp_path)
    controller._photocraft = proc
    controller._wait_photocraft_stashes = lambda proc, only=None: None
    controller._settle_closed_photocraft()
    assert proc.left
    assert len(_reopen_same_photo(controller, proc, tmp_path, monkeypatch, camera_raw=True)) == 1


def test_a_photo_still_being_worked_on_is_not_opened_again(controller, tmp_path, monkeypatch):
    """The same photo asked for while the editor is on it (a refresh, a re-sync) must not throw the user back into Camera Raw."""
    proc = running_editor(tmp_path)
    assert _reopen_same_photo(controller, proc, tmp_path, monkeypatch, camera_raw=True) == []


def test_outside_the_camera_raw_workspace_a_left_photo_is_simply_still_open(controller, tmp_path, monkeypatch):
    proc = running_editor(tmp_path, camera_raw_first=False)
    controller._settle_photocraft_after_leaving(proc)
    assert _reopen_same_photo(controller, proc, tmp_path, monkeypatch, camera_raw=False) == []
