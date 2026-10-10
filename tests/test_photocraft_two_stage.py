"""Two-stage display: a cached preview opens in PhotoCraft at once, the RAW follows once the
selection rests, and a RAW load for a photo already left can never surface or bind."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QObject
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from image_triage import photocraft_bridge as bridge
from image_triage import photocraft_preview
from image_triage.preview_controller import PreviewController


@pytest.fixture
def controller():
    app = QApplication.instance() or QApplication([])
    parent = QObject()
    ctl = PreviewController(parent)
    yield ctl
    ctl._photocraft_save_timer.stop()
    ctl._photocraft_dwell_timer.stop()
    ctl._photocraft_executor.shutdown(wait=True)
    ctl._photocraft_preview_executor.shutdown(wait=True)
    app.processEvents()


def fast_proc(tmp_path):
    control = Mock()
    control.document_revision.return_value = 2
    control.call.return_value = {"job": 5, "pending": True}
    proc = bridge.PhotoCraftProcess(
        process=Mock(), control=control, hwnd=123, token_file=tmp_path / "token",
        read_root=tmp_path, write_root=tmp_path / ".image_triage_edits",
    )
    proc.process.poll.return_value = None
    proc.fast = Mock()
    proc.fast.call.return_value = {"jobs": []}
    return proc


def test_preview_file_is_made_once_and_reused(tmp_path, monkeypatch):
    from image_triage import imaging

    photo = tmp_path / "frame.NEF"
    photo.write_bytes(b"raw")
    loads = []

    def fake_load(path, size, *, prefer_embedded):
        loads.append((path, size.width(), prefer_embedded))
        image = QImage(40, 30, QImage.Format.Format_RGB32)
        image.fill(QColor("green"))
        return image, None

    monkeypatch.setattr(imaging, "load_image_for_display", fake_load)
    root = tmp_path / ".image_triage_edits"
    first = photocraft_preview.ensure_preview(str(photo), root)
    again = photocraft_preview.ensure_preview(str(photo), root)
    assert first == again and first.is_file() and first.parent == root / "previews"
    assert len(loads) == 1 and loads[0][2] is True
    assert not list(first.parent.glob("*.tmp"))
    photo.write_bytes(b"changed raw")
    assert photocraft_preview.preview_path_for(str(photo), root) != first


def test_preview_failure_names_the_photo(tmp_path, monkeypatch):
    from image_triage import imaging

    monkeypatch.setattr(imaging, "load_image_for_display", lambda *a, **k: (QImage(), "no embedded preview"))
    photo = tmp_path / "frame.NEF"
    photo.write_bytes(b"raw")
    with pytest.raises(RuntimeError, match="frame.NEF"):
        photocraft_preview.ensure_preview(str(photo), tmp_path / ".image_triage_edits")


def test_fast_lane_needs_a_running_process_for_this_folder_without_saved_edits(controller, tmp_path):
    photo = str(tmp_path / "frame.NEF")
    assert not controller._photocraft_fast_lane_ready(photo)
    proc = fast_proc(tmp_path)
    controller._photocraft = proc
    assert controller._photocraft_fast_lane_ready(photo)
    assert not controller._photocraft_fast_lane_ready(str(tmp_path.parent / "other-folder" / "frame.NEF"))
    assert not controller._photocraft_fast_lane_ready(str(tmp_path / "frame.jpg")), "flat files already open fast"
    proc.fast_unavailable = True
    assert not controller._photocraft_fast_lane_ready(photo)
    proc.fast_unavailable = False
    sidecar = bridge.sidecar_pcraft_path(photo)
    sidecar.parent.mkdir(parents=True)
    sidecar.write_bytes(b"project")
    assert not controller._photocraft_fast_lane_ready(photo)


def test_request_shows_the_preview_now_and_loads_the_raw_only_after_the_dwell(controller, tmp_path, monkeypatch):
    controller._photocraft = fast_proc(tmp_path)
    controller._window = SimpleNamespace(_preview=SimpleNamespace(set_photocraft_loading=Mock(), native_first_active=Mock(return_value=False)))
    previews, raws = [], []
    monkeypatch.setattr(controller, "_show_photocraft_preview", lambda generation, path: previews.append((generation, path)))
    monkeypatch.setattr(controller, "_load_photocraft", lambda generation, path: raws.append((generation, path)))
    controller.handle_photocraft_edit_requested(str(tmp_path / "a.NEF"))
    controller._photocraft_preview_executor.submit(lambda: None).result()
    assert len(previews) == 1 and raws == []
    assert controller._photocraft_dwell_timer.isActive()
    controller._photocraft_dwell_elapsed()
    controller._photocraft_executor.submit(lambda: None).result()
    assert raws == [(controller._photocraft_generation, str(tmp_path / "a.NEF"))]


def test_moving_on_before_the_dwell_never_loads_the_earlier_raw(controller, tmp_path, monkeypatch):
    controller._photocraft = fast_proc(tmp_path)
    controller._window = SimpleNamespace(_preview=SimpleNamespace(set_photocraft_loading=Mock(), native_first_active=Mock(return_value=False)))
    raws = []
    monkeypatch.setattr(controller, "_show_photocraft_preview", lambda *a: None)
    monkeypatch.setattr(controller, "_load_photocraft", lambda generation, path: raws.append(path))
    controller.handle_photocraft_edit_requested(str(tmp_path / "a.NEF"))
    controller.handle_photocraft_edit_requested(str(tmp_path / "b.NEF"))
    controller._photocraft_dwell_elapsed()
    controller._photocraft_executor.submit(lambda: None).result()
    assert raws == [str(tmp_path / "b.NEF")]


def test_first_photo_of_a_folder_goes_straight_to_the_raw(controller, tmp_path, monkeypatch):
    controller._window = SimpleNamespace(_preview=SimpleNamespace(set_photocraft_loading=Mock(), native_first_active=Mock(return_value=False)))
    raws = []
    monkeypatch.setattr(controller, "_load_photocraft", lambda generation, path: raws.append(path))
    controller.handle_photocraft_edit_requested(str(tmp_path / "a.NEF"))
    controller._photocraft_executor.submit(lambda: None).result()
    assert raws == [str(tmp_path / "a.NEF")]
    assert not controller._photocraft_dwell_timer.isActive()


def test_preview_stage_opens_on_the_fast_lane_and_saves_the_photo_it_replaces(controller, tmp_path, monkeypatch):
    proc = fast_proc(tmp_path)
    proc.current_sidecar = bridge.sidecar_pcraft_path(str(tmp_path / "old.NEF"))
    proc.source_path = str(tmp_path / "old.NEF")
    proc.opened_revision = 1
    controller._photocraft = proc
    preview_file = tmp_path / ".image_triage_edits" / "previews" / "new.jpg"
    monkeypatch.setattr(photocraft_preview, "ensure_preview", lambda path, root: preview_file)
    controller._photocraft_generation = 3
    shown = []
    controller._photocraft_preview_shown.connect(lambda g, p, e: shown.append((g, p, e)))
    controller._show_photocraft_preview(3, str(tmp_path / "new.NEF"))
    proc.fast.app_open.assert_called_once_with(".image_triage_edits/previews/new.jpg", replace=True)
    assert proc.current_sidecar is None
    assert proc.preview_source is not None
    stashes = [c for c in proc.fast.call.call_args_list if c.args and c.args[0] == "app.stash"]
    assert stashes
    assert not proc.control.call.called
    QApplication.instance().processEvents()
    assert shown and shown[0][2] is None


def test_preview_stage_cancels_a_raw_open_that_is_still_running(controller, tmp_path, monkeypatch):
    proc = fast_proc(tmp_path)
    proc.fast.call.side_effect = lambda method, params=None: (
        {"jobs": [{"id": 9, "command": "app.open", "state": "running"}, {"id": 4, "command": "app.stash", "state": "running"}]}
        if method == "jobs.list"
        else {}
    )
    controller._photocraft = proc
    monkeypatch.setattr(photocraft_preview, "ensure_preview", lambda path, root: tmp_path / ".image_triage_edits" / "previews" / "p.jpg")
    controller._photocraft_generation = 1
    controller._photocraft_raw_inflight = True
    controller._show_photocraft_preview(1, str(tmp_path / "x.NEF"))
    cancels = [c.args for c in proc.fast.call.call_args_list if c.args[0] == "jobs.cancel"]
    assert cancels == [("jobs.cancel", {"job": 9})]
    controller._photocraft_raw_inflight = False


def test_no_cancel_is_sent_when_no_raw_open_is_in_flight(controller, tmp_path, monkeypatch):
    proc = fast_proc(tmp_path)
    controller._photocraft = proc
    monkeypatch.setattr(photocraft_preview, "ensure_preview", lambda path, root: tmp_path / ".image_triage_edits" / "previews" / "p.jpg")
    controller._photocraft_generation = 1
    controller._show_photocraft_preview(1, str(tmp_path / "x.NEF"))
    assert not [c for c in proc.fast.call.call_args_list if c.args[0] in ("jobs.list", "jobs.cancel")]


def test_a_stale_preview_request_does_nothing(controller, tmp_path, monkeypatch):
    proc = fast_proc(tmp_path)
    controller._photocraft = proc
    made = Mock()
    monkeypatch.setattr(photocraft_preview, "ensure_preview", made)
    controller._photocraft_generation = 5
    controller._show_photocraft_preview(4, str(tmp_path / "old.NEF"))
    made.assert_not_called()
    proc.fast.app_open.assert_not_called()


def test_a_failed_preview_falls_back_to_the_raw_directly(controller, tmp_path, monkeypatch):
    preview = SimpleNamespace(
        isVisible=lambda: True, _compare_mode=False, _collection_browse_mode=False, _before_after_enabled=False,
        show_photocraft_host=Mock(), set_photocraft_loading=Mock(), photocraft_host_hwnd=lambda: 1,
    )
    controller._window = SimpleNamespace(_preview=preview)
    controller._photocraft = fast_proc(tmp_path)
    raws = []
    monkeypatch.setattr(controller, "_load_photocraft", lambda generation, path: raws.append(path))
    controller._photocraft_generation = 2
    controller._finish_photocraft_preview(2, str(tmp_path / "a.NEF"), RuntimeError("no embedded preview"))
    controller._photocraft_executor.submit(lambda: None).result()
    assert raws == [str(tmp_path / "a.NEF")]
    preview.show_photocraft_host.assert_not_called()


def test_a_connection_failure_turns_the_fast_lane_off(controller, tmp_path, monkeypatch):
    proc = fast_proc(tmp_path)
    proc.fast = None
    proc.port = 1
    monkeypatch.setattr(bridge, "PhotoCraftControl", Mock(side_effect=OSError("refused")))
    controller._photocraft = proc
    controller._photocraft_generation = 1
    controller._show_photocraft_preview(1, str(tmp_path / "x.NEF"))
    assert proc.fast_unavailable


def test_a_raw_open_for_a_photo_already_left_is_not_recorded_or_bound(controller, tmp_path):
    proc = fast_proc(tmp_path)
    proc.control.app_open = Mock()
    controller._photocraft = proc
    controller._photocraft_generation = 8
    target = tmp_path / ".image_triage_edits" / "x.pcraft"
    assert controller._switch_photocraft_document(proc, str(tmp_path / "old.NEF"), str(target), generation=7) is False
    assert proc.current_sidecar is None and proc.preview_source is None
    assert controller._switch_photocraft_document(proc, str(tmp_path / "new.NEF"), str(target), generation=8) is True
    assert proc.current_sidecar == target


def test_cancel_open_jobs_never_touches_saves():
    fast = Mock()
    fast.call.side_effect = lambda method, params=None: (
        {"jobs": [{"id": 1, "command": "app.stash", "state": "running"}, {"id": 2, "command": "app.open", "state": "done"}]}
        if method == "jobs.list"
        else {}
    )
    proc = bridge.PhotoCraftProcess(process=Mock(), control=Mock(), hwnd=1, token_file=None, read_root=None)
    proc.fast = fast
    assert proc.cancel_open_jobs() == 0
    assert not [c for c in fast.call.call_args_list if c.args[0] == "jobs.cancel"]


def test_an_open_is_abandoned_if_the_selection_moved_on_while_the_raw_was_unpacking(tmp_path, monkeypatch):
    from image_triage import photocraft_raw_source

    control = bridge.PhotoCraftControl.__new__(bridge.PhotoCraftControl)
    control.raw_smart_supported = True
    control.raw_sensor_supported = True
    control.read_root = tmp_path
    control.call = Mock(return_value={})
    sensor = tmp_path / ".image_triage_edits" / "raw-sensors" / "s.sensor.dng"
    monkeypatch.setattr(photocraft_raw_source, "materialize_sensor_dng", Mock(return_value=sensor))
    with pytest.raises(bridge.PhotoCraftSuperseded):
        control.app_open("camera.NEF", replace=True, should_continue=lambda: False)
    control.call.assert_not_called()
    control.app_open("camera.NEF", replace=True, should_continue=lambda: True)
    assert control.call.call_args.args[0] == "app.open"


def test_switch_hands_the_open_a_check_tied_to_the_current_generation(controller, tmp_path):
    proc = fast_proc(tmp_path)
    proc.control.app_open = Mock()
    controller._photocraft = proc
    controller._photocraft_generation = 4
    controller._switch_photocraft_document(proc, str(tmp_path / "a.NEF"), str(tmp_path / ".image_triage_edits" / "a.pcraft"), generation=4)
    check = proc.control.app_open.call_args.kwargs["should_continue"]
    assert check() is True
    controller._photocraft_generation = 5
    assert check() is False


def test_a_stale_raw_that_finished_anyway_is_covered_by_the_current_preview(controller, tmp_path, monkeypatch):
    proc = fast_proc(tmp_path)
    controller._photocraft = proc
    controller._window = SimpleNamespace(_preview=None)
    current = str(tmp_path / "now.NEF")
    controller._photocraft_current_path = current
    controller._photocraft_generation = 6
    shown = []
    monkeypatch.setattr(controller, "_show_photocraft_preview", lambda generation, path: shown.append((generation, path)))

    def open_that_finishes_after_the_selection_moved_on(preview, path):
        controller._photocraft_generation = 7
        controller._photocraft_needs_preview_refresh = True

    monkeypatch.setattr(controller, "_open_in_photocraft", open_that_finishes_after_the_selection_moved_on)
    controller._load_photocraft(6, str(tmp_path / "x.NEF"))
    controller._photocraft_preview_executor.submit(lambda: None).result()
    assert shown == [(7, current)]
    assert controller._photocraft_needs_preview_refresh is False
