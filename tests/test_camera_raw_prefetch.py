"""While a photo is up in Camera Raw the editor is asked to get the next raw ready, so opening it costs next to nothing."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from image_triage import photocraft_bridge as bridge
from image_triage.preview_controller import PreviewController
from image_triage.scanner import normalized_path_key


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


def process(tmp_path, *, prefetch=True, workspace=True):
    control = Mock()
    control.camera_raw_supported = True
    control.camera_raw_prefetch_supported = prefetch
    proc = bridge.PhotoCraftProcess(
        process=Mock(), control=control, hwnd=1, token_file=tmp_path / "token",
        read_root=tmp_path, write_root=tmp_path / ".image_triage_edits", camera_raw_first=workspace,
    )
    proc.process.poll.return_value = None
    proc.fast = Mock()
    return proc


def folder(tmp_path, names):
    records = []
    for name in names:
        path = tmp_path / name
        path.write_bytes(b"x" * 10)
        records.append(SimpleNamespace(path=str(path), is_folder=False))
    return records


def drain(controller):
    controller._photocraft_preview_executor.submit(lambda: None).result()


def test_the_file_signature_is_size_and_modified_time(tmp_path):
    path = tmp_path / "a.NEF"
    path.write_bytes(b"12345")
    stat = path.stat()
    assert bridge.file_signature(str(path)) == f"5:{stat.st_mtime_ns}"
    assert bridge.file_signature(str(tmp_path / "missing.NEF")) is None


def test_the_spec_carries_the_signature_the_editor_checks_a_prepared_raw_against(controller, tmp_path):
    photo = tmp_path / "frame.NEF"
    photo.write_bytes(b"abc")
    spec = controller._camera_raw_spec(str(photo), process(tmp_path))
    assert spec["signature"] == bridge.file_signature(str(photo))


def test_the_bridge_sends_the_request_with_a_forward_slash_path():
    control = object.__new__(bridge.PhotoCraftControl)
    control.call = Mock(return_value={"queued": True})
    control.app_prefetch("sub\\next.NEF", "10:1")
    control.call.assert_called_once_with("app.prefetch", {"path": "sub/next.NEF", "signature": "10:1"})


def test_the_next_raw_in_the_direction_of_travel_is_prepared(controller, tmp_path):
    records = folder(tmp_path, ["a.NEF", "b.NEF", "c.NEF"])
    controller._window = SimpleNamespace(_records=records)
    proc = process(tmp_path)
    controller._photocraft = proc
    controller._preview_travel = 1
    controller._prefetch_next_raw(records[1].path)
    drain(controller)
    proc.fast.app_prefetch.assert_called_once_with("c.NEF", bridge.file_signature(records[2].path))
    proc.fast.app_prefetch.reset_mock()
    controller._preview_travel = -1
    controller._prefetch_next_raw(records[1].path)
    drain(controller)
    proc.fast.app_prefetch.assert_called_once_with("a.NEF", bridge.file_signature(records[0].path))


def test_with_no_direction_yet_the_following_photo_is_prepared(controller, tmp_path):
    records = folder(tmp_path, ["a.NEF", "b.NEF"])
    controller._window = SimpleNamespace(_records=records)
    proc = process(tmp_path)
    controller._photocraft = proc
    controller._prefetch_next_raw(records[0].path)
    drain(controller)
    proc.fast.app_prefetch.assert_called_once_with("b.NEF", bridge.file_signature(records[1].path))


def test_the_last_photo_prepares_the_one_before_it(controller, tmp_path):
    records = folder(tmp_path, ["a.NEF", "b.NEF"])
    controller._window = SimpleNamespace(_records=records)
    proc = process(tmp_path)
    controller._photocraft = proc
    controller._preview_travel = 1
    controller._prefetch_next_raw(records[1].path)
    drain(controller)
    proc.fast.app_prefetch.assert_called_once_with("a.NEF", bridge.file_signature(records[0].path))


@pytest.mark.parametrize("neighbour", ["b.jpg", "b.png", "b.tif"])
def test_a_neighbour_that_is_not_a_raw_is_left_alone(controller, tmp_path, neighbour):
    records = folder(tmp_path, ["a.NEF", neighbour])
    controller._window = SimpleNamespace(_records=records)
    proc = process(tmp_path)
    controller._photocraft = proc
    controller._prefetch_next_raw(records[0].path)
    drain(controller)
    proc.fast.app_prefetch.assert_not_called()


def test_a_folder_card_is_never_prepared(controller, tmp_path):
    records = folder(tmp_path, ["a.NEF", "b.NEF"])
    records[1].is_folder = True
    controller._window = SimpleNamespace(_records=records)
    proc = process(tmp_path)
    controller._photocraft = proc
    controller._prefetch_next_raw(records[0].path)
    drain(controller)
    proc.fast.app_prefetch.assert_not_called()


def test_nothing_is_asked_of_an_editor_that_cannot_prefetch_or_is_not_in_the_workspace(controller, tmp_path):
    records = folder(tmp_path, ["a.NEF", "b.NEF"])
    controller._window = SimpleNamespace(_records=records)
    for proc in (process(tmp_path, prefetch=False), process(tmp_path, workspace=False)):
        controller._photocraft = proc
        controller._prefetch_next_raw(records[0].path)
        drain(controller)
        proc.fast.app_prefetch.assert_not_called()
    controller._photocraft = None
    controller._prefetch_next_raw(records[0].path)  # no editor at all: nothing to do, nothing raised


def test_a_photo_missing_from_the_list_or_the_disk_asks_for_nothing(controller, tmp_path):
    records = folder(tmp_path, ["a.NEF", "b.NEF"])
    controller._window = SimpleNamespace(_records=records)
    proc = process(tmp_path)
    controller._photocraft = proc
    controller._prefetch_next_raw(str(tmp_path / "elsewhere.NEF"))
    records[1].path = str(tmp_path / "gone.NEF")
    controller._prefetch_next_raw(records[0].path)
    drain(controller)
    proc.fast.app_prefetch.assert_not_called()


def test_a_refused_request_is_not_an_error(controller, tmp_path):
    records = folder(tmp_path, ["a.NEF", "b.NEF"])
    controller._window = SimpleNamespace(_records=records)
    proc = process(tmp_path)
    proc.fast.app_prefetch.side_effect = bridge.PhotoCraftError("prefetch needs the Camera Raw workspace")
    controller._photocraft = proc
    controller._prefetch_next_raw(records[0].path)
    drain(controller)
    proc.fast.app_prefetch.assert_called_once()


def test_landing_in_camera_raw_starts_the_prefetch(controller, tmp_path):
    records = folder(tmp_path, ["a.NEF", "b.NEF"])
    preview = SimpleNamespace(photocraft_document_ready=Mock())
    controller._window = SimpleNamespace(_records=records, _preview=preview)
    controller._photocraft = process(tmp_path)
    controller._photocraft_generation = 3
    controller._handle_camera_raw_up(3, records[0].path)
    drain(controller)
    preview.photocraft_document_ready.assert_called_once_with(records[0].path)
    controller._photocraft.fast.app_prefetch.assert_called_once()
    controller._photocraft.fast.app_prefetch.reset_mock()
    controller._handle_camera_raw_up(2, records[0].path)  # a stale landing
    drain(controller)
    controller._photocraft.fast.app_prefetch.assert_not_called()
