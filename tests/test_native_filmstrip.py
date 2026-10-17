from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject
from PySide6.QtGui import QColor, QImage, QPixmap
from PySide6.QtWidgets import QApplication

from image_triage.models import ImageRecord
from image_triage.photocraft_filmstrip import NativeFilmstrip
from image_triage.preview import FullScreenPreview


@pytest.fixture
def adapter(tmp_path):
    app = QApplication.instance() or QApplication([])
    records = [ImageRecord(str(tmp_path / f"{index}.nef"), f"{index}.nef", 1, 1) for index in range(80)]
    preview = SimpleNamespace(_source_entries=[SimpleNamespace(record=records[0])], active=False, selected=[])
    preview.isVisible = lambda: True
    preview.set_native_filmstrip = lambda active: setattr(preview, "active", active)
    preview._handle_studio_filmstrip_selected = preview.selected.append
    controller = QObject()
    controller._window = SimpleNamespace(_records=records, _annotations={})
    controller.preview_if_built = lambda: preview
    controller._filmstrip_edit_state = lambda path: (0, 0)
    image = QImage(400, 300, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    pixmap = QPixmap.fromImage(image)
    controller.preview_filmstrip_thumb = lambda index: pixmap
    strip = NativeFilmstrip(controller)
    strip.refresh_source()
    strip.proc = object()
    yield strip, controller, preview
    strip.stop()
    app.processEvents()


def test_visible_window_and_pixels_stay_within_control_transport_budget(adapter):
    import base64
    import json

    strip, _, _ = adapter
    packet, uploaded = strip._packet()
    thumbnails = [item["thumbnail"] for item in packet["items"] if "thumbnail" in item]
    assert len(thumbnails) == 1 and len(packet["items"]) <= 32
    thumb = thumbnails[0]
    assert max(thumb["width"], thumb["height"]) <= 384
    assert len(base64.b64decode(thumb["rgba"])) == thumb["width"] * thumb["height"] * 4
    assert len(json.dumps(packet).encode()) < 1 << 20
    assert uploaded[0] == strip.identities[0]


def test_native_click_is_validated_and_delivered_exactly_once(adapter):
    strip, _, preview = adapter
    request = {"serial": 1, "generation": strip.generation, "kind": "select", "index": 1, "id": strip.identities[1]}
    reply = {"generation": strip.generation, "ready": True, "enabled": True, "cameraRawOpen": True, "first": 0, "last": 10, "request": request}
    strip._apply(strip.proc, strip.generation, reply, None)
    strip._apply(strip.proc, strip.generation, reply, None)
    assert preview.active and preview.selected == [1]
    request.update(serial=2, id="different-photo")
    strip._apply(strip.proc, strip.generation, reply, None)
    assert preview.selected == [1]


def test_sorting_rejects_a_reply_from_the_old_browse_generation(adapter):
    strip, controller, preview = adapter
    old_generation = strip.generation
    controller._window._records.reverse()
    strip.refresh_source()
    strip._apply(strip.proc, old_generation, {"generation": old_generation, "enabled": True, "cameraRawOpen": True}, None)
    assert not preview.active and strip.generation > old_generation


def test_scrolling_away_from_current_photo_does_not_force_the_tray_back(adapter):
    strip, _, _ = adapter
    strip.first, strip.last = 40, 50
    packet, _ = strip._packet()
    assert [item["index"] for item in packet["items"]] == [*range(40, 50), 0]
    assert packet["current"] == 0


def test_discarded_native_thumbnails_are_uploaded_again_on_return(adapter):
    strip, _, _ = adapter
    packet, uploaded = strip._packet()
    strip.sent[uploaded[0]] = uploaded[1]
    strip.sent[strip.identities[15]] = "old-version"
    strip.first, strip.last = 40, 50
    strip._packet()
    assert strip.identities[15] not in strip.sent


def test_lost_reply_never_restores_qt_over_a_potentially_visible_native_strip(adapter):
    strip, _, preview = adapter
    preview.active = True
    strip._apply(strip.proc, strip.generation, RuntimeError("connection closed"), None)
    assert preview.active


def test_process_exit_restores_the_retained_host_strip(adapter):
    strip, _, preview = adapter
    preview.active = True
    strip.proc = SimpleNamespace(is_running=lambda: False)
    strip._tick()
    assert not preview.active


def test_empty_browse_window_does_not_index_a_removed_photo(adapter):
    strip, controller, _ = adapter
    controller._window._records.clear()
    packet, uploaded = strip._packet()
    assert packet["total"] == 0 and packet["items"] == [] and uploaded is None


def test_rescanning_keeps_the_committed_tray_and_defers_clicks(adapter):
    strip, controller, preview = adapter
    preview.active = True
    identities, generation = strip.identities, strip.generation
    controller._window._scan_in_progress = True
    controller._window._records.clear()
    strip.refresh_source()
    request = {"serial": 1, "generation": generation, "kind": "select", "index": 1, "id": identities[1]}
    strip._apply(strip.proc, generation, {"generation": generation, "request": request}, None)
    packet, uploaded = strip._packet()
    assert strip.identities == identities and strip.generation == generation
    assert preview.active and preview.selected == [] and strip.ack == 0
    assert packet["total"] == len(identities) and uploaded is None


@pytest.mark.parametrize("kind,value", [("rating", 4), ("reject", None), ("navigate", 70), ("sort", "Filename"), ("filter", "Edited")])
def test_native_actions_use_existing_host_ownership_and_are_delivered_once(adapter, kind, value):
    strip, controller, preview = adapter
    calls = []
    controller.handle_preview_rating_requested = lambda path, rating: calls.append((path, rating))
    controller.handle_preview_reject_requested = lambda path: calls.append((path,))
    controller._window._views = SimpleNamespace(set_sort_mode=lambda mode: calls.append((mode.value,)))
    controller._window._records_view = SimpleNamespace(set_filter_mode=lambda mode: calls.append((mode.value,)))
    request = {"serial": 1, "generation": strip.generation, "kind": kind, "index": 0, "id": strip.identities[0], "value": value}
    reply = {"generation": strip.generation, "ready": True, "cameraRawOpen": True, "request": request}
    strip._apply(strip.proc, strip.generation, reply, None)
    strip._apply(strip.proc, strip.generation, reply, None)
    if kind == "navigate":
        assert preview.selected == [70]
    else:
        assert len(calls) == 1


@pytest.mark.parametrize("kind,value", [("rating", True), ("rating", 6), ("navigate", -1), ("navigate", 80), ("sort", "shell command"), ("filter", "unknown"), ("delete", None)])
def test_invalid_native_actions_are_not_acknowledged(adapter, kind, value):
    strip, _, preview = adapter
    request = {"serial": 1, "generation": strip.generation, "kind": kind, "index": 0, "id": strip.identities[0], "value": value}
    strip._apply(strip.proc, strip.generation, {"generation": strip.generation, "request": request}, None)
    assert strip.ack == 0 and preview.selected == []


def test_host_strip_is_detached_and_can_be_restored_without_recreating_it():
    app = QApplication.instance() or QApplication([])
    preview = FullScreenPreview()
    try:
        strip = preview._filmstrip
        layout = strip.parentWidget().layout()
        preview._photocraft_edit_active = True
        preview.set_native_filmstrip(True)
        assert layout.indexOf(strip) == -1 and strip.isHidden()
        preview.set_native_filmstrip(False)
        assert preview._filmstrip is strip and layout.indexOf(strip) >= 0
    finally:
        preview.close()
        app.processEvents()
