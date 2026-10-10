"""Browsing the popout with the editor behind: the photo's own picture is on screen before PhotoCraft is
asked for anything, the editor catches up after a dwell (or at once when it is touched), and the picture
goes away only when the editor is showing the same photo."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QObject, QRect, QSize
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from image_triage import photocraft_bridge as bridge
from image_triage.models import ImageRecord
from image_triage.nav_timing import NavigationTimer
from image_triage.preview import FullScreenPreview, PreviewEntry
from image_triage.preview_controller import PreviewController
from image_triage.scanner import normalized_path_key
from image_triage.ui import native_image_layer as layer_module
from image_triage.ui.native_image_layer import NativeImageLayer, fit_rect, layer_rect, normalize_region


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


def solid(width, height, color):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return image


def entry(path, color="blue"):
    record = ImageRecord(path=path, name=Path(path).name, size=1, modified_ns=1)
    return PreviewEntry(record=record, source_path=path, placeholder_image=solid(64, 48, color))


@pytest.fixture
def preview(qapp):
    window = FullScreenPreview()
    window.set_photocraft_available(True)
    yield window
    window.close()


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / "a.jpg"
    solid(80, 60, "red").save(str(path), "JPEG")
    return str(path)


# --- geometry ---------------------------------------------------------------------------------------

HOST = QRect(0, 0, 1000, 800)


def test_region_names_are_normalised():
    assert normalize_region("FULL") == "full"
    assert normalize_region(" off ") == "off"
    assert normalize_region("nonsense") == layer_module.DEFAULT_REGION == "canvas"
    assert normalize_region(None) == "canvas"


@pytest.mark.parametrize("region", ["full", "off"])
def test_full_and_off_cover_the_whole_editor(region):
    viewport = {"canvas": {"x": 200, "y": 40, "width": 700, "height": 700}}
    assert layer_rect(region, HOST, viewport, 1.0) == HOST


def test_canvas_covers_only_the_image_area():
    viewport = {"canvas": {"x": 200, "y": 40, "width": 780, "height": 700}}
    assert layer_rect("canvas", HOST, viewport, 1.0) == QRect(200, 40, 780, 700)


def test_canvas_is_converted_from_physical_pixels_and_offset_by_the_host():
    viewport = {"canvas": {"x": 400, "y": 80, "width": 1560, "height": 1400}}
    host = QRect(10, 20, 1000, 800)
    assert layer_rect("canvas", host, viewport, 2.0) == QRect(210, 60, 780, 700)


def test_an_open_camera_raw_view_is_the_image_area():
    viewport = {
        "canvas": {"x": 0, "y": 0, "width": 1000, "height": 800},
        "cameraRaw": {"open": True, "view": {"x": 100, "y": 50, "width": 600, "height": 500},
                      "preview": {"x": 120, "y": 60, "width": 300, "height": 200}},
    }
    assert layer_rect("canvas", HOST, viewport, 1.0) == QRect(100, 50, 600, 500)
    viewport["cameraRaw"]["view"] = None
    assert layer_rect("canvas", HOST, viewport, 1.0) == QRect(120, 60, 300, 200)
    viewport["cameraRaw"]["preview"] = None
    assert layer_rect("canvas", HOST, viewport, 1.0) == QRect(0, 0, 1000, 800)


def test_canvas_is_clamped_to_the_editor_window():
    viewport = {"canvas": {"x": 600, "y": 0, "width": 900, "height": 800}}
    assert layer_rect("canvas", HOST, viewport, 1.0) == QRect(600, 0, 400, 800)


@pytest.mark.parametrize(
    "viewport",
    [None, {}, {"canvas": None}, {"canvas": {"x": "a", "y": 0, "width": 1, "height": 1}},
     {"canvas": {"x": 0, "y": 0, "width": 10, "height": 500}}, {"cameraRaw": "closed"}],
)
def test_an_unusable_viewport_falls_back_to_the_whole_editor(viewport):
    assert layer_rect("canvas", HOST, viewport, 1.0) == HOST


def test_pictures_are_fitted_and_centred_without_distortion():
    assert fit_rect(QSize(4000, 3000), QSize(1000, 1000)) == QRect(0, 125, 1000, 750)
    assert fit_rect(QSize(100, 100), QSize(200, 100)) == QRect(50, 0, 100, 100)
    assert fit_rect(QSize(0, 0), QSize(100, 100)).isNull()


# --- the layer widget ---------------------------------------------------------------------------------

def test_layer_paints_the_picture_fitted_on_black(qapp):
    layer = NativeImageLayer()
    layer.resize(200, 100)
    layer.set_image(solid(100, 100, "red"), "a.jpg")
    image = layer.grab().toImage()
    assert image.pixelColor(100, 50) == QColor("red")
    assert image.pixelColor(5, 50) == QColor("black")


def test_layer_reports_what_it_painted_and_whether_it_was_a_stand_in(qapp):
    layer = NativeImageLayer()
    layer.resize(50, 50)
    painted = []
    layer.painted.connect(lambda path, placeholder: painted.append((path, placeholder)))
    layer.set_image(solid(10, 10, "red"), "a.jpg", placeholder=True)
    layer.grab()
    layer.set_image(solid(10, 10, "green"), "a.jpg")
    layer.grab()
    layer.set_image(QImage(), "")
    layer.grab()
    assert painted == [("a.jpg", True), ("a.jpg", False)]


def test_clicking_the_layer_is_reported(qapp):
    layer = NativeImageLayer()
    layer.resize(50, 50)
    clicks = []
    layer.clicked.connect(lambda: clicks.append(1))
    from PySide6.QtCore import Qt

    QTest.mouseClick(layer, Qt.MouseButton.LeftButton)
    assert clicks == [1]


# --- the saved render the viewer prefers --------------------------------------------------------------

def touch(path: Path, when: float, content: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.utime(path, (when, when))


def test_the_small_display_render_wins_over_the_full_size_png(tmp_path):
    source = str(tmp_path / "frame.nef")
    touch(bridge.rendered_preview_path(source), 1000)
    touch(bridge.display_render_path(source), 1010)
    assert bridge.saved_render_path(source) == bridge.display_render_path(source)


def test_a_photo_without_saved_edits_has_no_render(tmp_path):
    assert bridge.saved_render_path(str(tmp_path / "frame.nef")) is None


def test_edits_saved_before_display_renders_existed_fall_back_to_the_png(tmp_path):
    source = str(tmp_path / "frame.nef")
    touch(bridge.rendered_preview_path(source), 1000)
    assert bridge.saved_render_path(source) == bridge.rendered_preview_path(source)


def test_an_old_display_render_never_shadows_newer_edits(tmp_path):
    source = str(tmp_path / "frame.nef")
    touch(bridge.display_render_path(source), 1000)
    touch(bridge.rendered_preview_path(source), 1000 + bridge.DISPLAY_STALE_AFTER_S + 5)
    assert bridge.saved_render_path(source) == bridge.rendered_preview_path(source)


def test_an_empty_display_render_is_ignored(tmp_path):
    source = str(tmp_path / "frame.nef")
    touch(bridge.display_render_path(source), 1000, b"")
    touch(bridge.rendered_preview_path(source), 1000)
    assert bridge.saved_render_path(source) == bridge.rendered_preview_path(source)


# --- the popout ---------------------------------------------------------------------------------------

def test_native_first_needs_the_editor_and_a_region(preview):
    assert preview.native_first_active()
    preview.set_overlay_region("full")
    assert preview.native_first_active()
    preview.set_overlay_region("off")
    assert not preview.native_first_active()
    preview.set_overlay_region("canvas")
    preview.set_photocraft_available(False)
    assert not preview.native_first_active()


def test_the_picture_is_up_before_the_editor_is_asked_for_anything(preview, photo):
    seen = []
    preview.photocraft_edit_requested.connect(lambda path: seen.append((path, preview._native_layer.isVisible())))
    preview.show_entries([entry(photo)])
    assert seen == [(photo, True)]
    assert preview._native_layer.path == photo
    assert not preview._native_layer.image_size().isEmpty(), "the grid thumbnail stands in at once"
    assert not preview._photocraft_host.isEnabled(), "input to the editor waits for its document"


def test_region_off_leaves_every_photo_to_the_editor(preview, photo):
    preview.set_overlay_region("off")
    seen = []
    preview.photocraft_edit_requested.connect(seen.append)
    preview.show_entries([entry(photo)])
    assert seen == [photo]
    assert not preview._native_layer.isVisible()


def test_the_popout_decodes_the_photo_itself_while_browsing(preview, photo, qapp):
    pool = Mock()
    preview._pool = pool
    preview.show_entries([entry(photo)])
    qapp.processEvents()
    assert pool.start.called, "decoding must not be switched off just because the editor is in use"


def test_region_off_does_not_decode_behind_the_editor(preview, photo, qapp):
    preview.set_overlay_region("off")
    pool = Mock()
    preview._pool = pool
    preview.show_entries([entry(photo)])
    qapp.processEvents()
    assert not pool.start.called


def test_the_picture_goes_only_after_the_editor_shows_the_same_photo(preview, photo, qapp):
    preview.show_entries([entry(photo)])
    preview.photocraft_document_ready(photo + ".other")
    QTest.qWait(FullScreenPreview.NATIVE_LAYER_RELEASE_MS + 100)
    assert preview._native_layer.isVisible(), "a document for another photo says nothing about this one"
    preview.photocraft_document_ready(photo)
    assert preview._native_layer.isVisible(), "the editor needs a moment to draw before the picture can go"
    QTest.qWait(FullScreenPreview.NATIVE_LAYER_RELEASE_MS + 100)
    assert not preview._native_layer.isVisible()


def test_moving_on_cancels_a_pending_release(preview, photo, tmp_path, qapp):
    other = tmp_path / "b.jpg"
    solid(80, 60, "green").save(str(other), "JPEG")
    preview.show_entries([entry(photo)])
    preview.photocraft_document_ready(photo)
    preview.show_entries([entry(str(other))])
    QTest.qWait(FullScreenPreview.NATIVE_LAYER_RELEASE_MS + 100)
    assert preview._native_layer.isVisible()
    assert preview._native_layer.path == str(other)


def test_the_picture_narrows_to_the_image_area_once_the_editor_is_on_screen(preview, photo, qapp):
    preview.show_entries([entry(photo)])
    preview.showNormal()
    preview.resize(1600, 1000)
    qapp.processEvents()
    preview._native_layer.present(preview._native_layer_target_rect())
    host = preview.panes_widget.rect()
    assert host.width() > 400 and host.height() > 300, "the offscreen popout is too small to test the region"
    assert preview._native_layer.geometry() == host, "before the editor is on screen the picture is the whole viewer"
    preview._photocraft_edit_active = True
    ratio = preview._photocraft_host.devicePixelRatioF()
    canvas = {"x": host.width() // 4 * ratio, "y": host.height() // 8 * ratio,
              "width": host.width() // 2 * ratio, "height": host.height() // 2 * ratio}
    preview.set_photocraft_viewport({"canvas": canvas})
    expected = layer_rect("canvas", host, {"canvas": canvas}, ratio)
    assert expected != host
    assert preview._native_layer.geometry() == expected
    preview.set_overlay_region("full")
    assert preview._native_layer.geometry() == host


def test_clicking_the_picture_asks_for_the_editor_now(preview, photo):
    asked = []
    preview.photocraft_prioritize_requested.connect(lambda: asked.append(1))
    preview.show_entries([entry(photo)])
    from PySide6.QtCore import Qt

    QTest.mouseClick(preview._native_layer, Qt.MouseButton.LeftButton)
    assert asked == [1]


# --- the controller -----------------------------------------------------------------------------------

@pytest.fixture
def controller(qapp):
    parent = QObject()
    ctl = PreviewController(parent)
    yield ctl
    ctl._photocraft_save_timer.stop()
    ctl._photocraft_dwell_timer.stop()
    ctl._photocraft_executor.shutdown(wait=True)
    ctl._photocraft_preview_executor.shutdown(wait=True)
    qapp.processEvents()


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


def native_controller(controller, tmp_path, monkeypatch, *, running=True):
    if running:
        controller._photocraft = fast_proc(tmp_path)
    preview = SimpleNamespace(set_photocraft_loading=Mock(), native_first_active=Mock(return_value=True))
    controller._window = SimpleNamespace(_preview=preview)
    shown, raws = [], []
    monkeypatch.setattr(controller, "_show_photocraft_preview", lambda generation, path: shown.append(path))
    monkeypatch.setattr(controller, "_load_photocraft", lambda generation, path: raws.append(path))
    return shown, raws


def drain(controller):
    controller._photocraft_preview_executor.submit(lambda: None).result()
    controller._photocraft_executor.submit(lambda: None).result()


def test_a_browsed_photo_opens_no_preview_document_and_waits_for_the_dwell(controller, tmp_path, monkeypatch):
    shown, raws = native_controller(controller, tmp_path, monkeypatch)
    photo = str(tmp_path / "a.NEF")
    controller.handle_photocraft_edit_requested(photo)
    drain(controller)
    assert shown == [] and raws == []
    assert controller._photocraft_dwell_timer.isActive()
    assert controller._photocraft_dwell_timer.interval() == controller.PHOTOCRAFT_NATIVE_DWELL_MS
    controller._photocraft_dwell_elapsed()
    drain(controller)
    assert raws == [photo]


def test_moving_on_loads_only_the_photo_that_rested(controller, tmp_path, monkeypatch):
    _, raws = native_controller(controller, tmp_path, monkeypatch)
    for name in ("a", "b", "c"):
        controller.handle_photocraft_edit_requested(str(tmp_path / f"{name}.NEF"))
    controller._photocraft_dwell_elapsed()
    drain(controller)
    assert raws == [str(tmp_path / "c.NEF")]


def test_touching_the_editor_loads_the_photo_without_waiting(controller, tmp_path, monkeypatch):
    _, raws = native_controller(controller, tmp_path, monkeypatch)
    photo = str(tmp_path / "a.NEF")
    controller.handle_photocraft_edit_requested(photo)
    controller.prioritize_photocraft_open()
    drain(controller)
    assert raws == [photo]
    assert not controller._photocraft_dwell_timer.isActive()


def test_touching_the_editor_with_nothing_pending_does_nothing(controller, tmp_path, monkeypatch):
    _, raws = native_controller(controller, tmp_path, monkeypatch)
    controller.prioritize_photocraft_open()
    drain(controller)
    assert raws == []


def test_the_first_photo_starts_the_editor_at_once(controller, tmp_path, monkeypatch):
    _, raws = native_controller(controller, tmp_path, monkeypatch, running=False)
    photo = str(tmp_path / "a.NEF")
    controller.handle_photocraft_edit_requested(photo)
    drain(controller)
    assert raws == [photo]
    assert not controller._photocraft_dwell_timer.isActive()


def test_moving_on_cancels_an_obsolete_open(controller, tmp_path, monkeypatch):
    native_controller(controller, tmp_path, monkeypatch)
    controller._photocraft_raw_inflight = True
    controller.handle_photocraft_edit_requested(str(tmp_path / "b.NEF"))
    drain(controller)
    methods = [call.args[0] for call in controller._photocraft.fast.call.call_args_list]
    assert "jobs.list" in methods


def test_the_editor_path_without_the_picture_is_unchanged(controller, tmp_path, monkeypatch):
    controller._photocraft = fast_proc(tmp_path)
    preview = SimpleNamespace(set_photocraft_loading=Mock(), native_first_active=Mock(return_value=False))
    controller._window = SimpleNamespace(_preview=preview)
    shown, raws = [], []
    monkeypatch.setattr(controller, "_show_photocraft_preview", lambda generation, path: shown.append(path))
    monkeypatch.setattr(controller, "_load_photocraft", lambda generation, path: raws.append(path))
    controller.handle_photocraft_edit_requested(str(tmp_path / "a.NEF"))
    drain(controller)
    assert shown == [str(tmp_path / "a.NEF")] and raws == []
    assert controller._photocraft_dwell_timer.interval() == controller.PHOTOCRAFT_RAW_DWELL_MS


# --- timing and autostash -----------------------------------------------------------------------------

def test_painted_frames_are_timed_to_the_pixels(controller, tmp_path):
    class Clock:
        now = 10.0

        def __call__(self):
            return self.now

    clock = Clock()
    controller.nav_timer = NavigationTimer(clock=clock)
    photo = str(tmp_path / "a.NEF")
    controller._begin_navigation_timing(photo)
    clock.now += 0.012
    controller._handle_native_layer_painted(photo, True)
    clock.now += 0.040
    controller._handle_native_layer_painted(photo, False)
    controller.nav_timer.flush()
    (record,) = controller.nav_timer.finished
    assert record.stages == {"first_pixel": 12.0, "preview": 52.0}


def test_the_key_press_not_the_controller_starts_the_clock(controller, tmp_path):
    class Clock:
        now = 10.0

        def __call__(self):
            return self.now

    clock = Clock()
    controller.nav_timer = NavigationTimer(clock=clock)
    controller._nav_input_at = clock.now
    clock.now += 0.020  # selection, entries and so on happen before the popout is asked to open
    photo = str(tmp_path / "a.NEF")
    controller._begin_navigation_timing(photo)
    clock.now += 0.010
    controller._mark_navigation(photo, "preview")
    assert controller.nav_timer._current.stages["preview"] == 30.0


def test_a_stale_key_press_does_not_backdate_a_later_open(controller, tmp_path):
    class Clock:
        now = 10.0

        def __call__(self):
            return self.now

    clock = Clock()
    controller.nav_timer = NavigationTimer(clock=clock)
    controller._nav_input_at = clock.now
    clock.now += 5.0
    controller._begin_navigation_timing(str(tmp_path / "a.NEF"))
    clock.now += 0.010
    controller._mark_navigation(str(tmp_path / "a.NEF"), "preview")
    assert controller.nav_timer._current.stages["preview"] == 10.0


def test_autostash_also_asks_for_the_display_render(controller, tmp_path):
    source = str(tmp_path / "frame.jpg")
    control = Mock()
    control.document_revision.return_value = 2
    control.call.return_value = {"job": 5, "pending": True}
    proc = bridge.PhotoCraftProcess(
        process=Mock(), control=control, hwnd=123, token_file=tmp_path / "token",
        read_root=tmp_path, write_root=tmp_path / ".image_triage_edits",
        source_path=source, current_source=normalized_path_key(source),
        current_sidecar=bridge.sidecar_pcraft_path(source), opened_revision=1,
    )
    proc.process.poll.return_value = None
    controller._save_photocraft_sidecar_if_dirty(proc)
    (params,) = [call.args[1] for call in control.call.call_args_list if call.args[0] == "app.stash"]
    assert params["display"] == "frame.jpg.photocraft.display.jpg"
    assert params["wait"] is False
