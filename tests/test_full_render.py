"""Full-resolution export authority, cancellation, and encoded pixel fidelity."""
import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtGui import QColor, QColorSpace, QImage

from image_triage import full_render as render, photocraft_bridge as bridge
from image_triage.edited_export import EditedImage, bit_depth, edited_image_for_export, save_preserving_depth
from image_triage.image_resize import _LoadedResizeImage, apply_edits_if_present, _save_resized_image


@dataclass
class Editor:
    read_root: Path
    write_root: Path
    current_source: str = ""
    camera_raw_first: bool = True

    def __post_init__(self):
        self.control = Mock(full_render_supported=True)
        self.lane = Mock()
        self.pending_stashes = {}
        self.stash_errors = {}
        self.shutdown = Mock()
        self.lane.call.side_effect = self.call
        self.dialog = {"open": False}
        self.requests = []

    def call(self, method, params=None):
        if method == "ui.cameraRaw":
            return self.dialog
        assert method == "app.render"
        self.requests.append(params)
        target = self.write_root / (params["out"] + ".png")
        target.parent.mkdir(exist_ok=True)
        image = QImage(120, 80, QImage.Format.Format_RGBA64)
        image.fill(QColor("red"))
        assert image.save(str(target))
        return {"out": target.name, "width": 120, "height": 80, "depth": 16, "format": "png", "icc": False}

    def is_running(self):
        return True

    def path_within_root(self, path):
        try:
            return Path(path).relative_to(self.read_root).as_posix()
        except ValueError:
            return None

    def path_within_write_root(self, path):
        return Path(path).relative_to(self.write_root).as_posix()

    def render_lane(self):
        return self.lane


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / "photo.dng"
    path.write_bytes(b"synthetic raw")
    bridge.recipe_path(str(path)).parent.mkdir()
    return str(path)


def recipe(photo, settings=None, **extra):
    value = {"version": 1, "cameraRaw": settings or {"exposure": 1.0}, **extra}
    bridge.recipe_path(photo).write_text(json.dumps(value))
    return value


def editor(photo):
    return Editor(Path(photo).parent, bridge.recipe_path(photo).parent)


def test_dialog_wins_over_saved_project_recipe_and_current_cache(photo, monkeypatch):
    proc = editor(photo)
    proc.current_source = "same"
    settings = recipe(photo)
    bridge.sidecar_pcraft_path(photo).write_bytes(b"project")
    proc.dialog = {"open": True, "changed": True, "target": "openRaw", "source": "photo.dng", "recipe": settings}
    cached = Mock(side_effect=AssertionError("live edits must precede the cache"))
    monkeypatch.setattr(bridge, "current_render_manifest", cached)
    save = Mock()
    service = render.FullRenderService(lambda: proc, source_key=lambda path: "same", save_open_photo=save)
    result = service.request(photo)
    assert result.outcome == render.RENDERED
    assert proc.requests[0]["recipe"] == settings
    assert "project" not in proc.requests[0]
    save.assert_not_called()
    result.release()


def test_unchanged_dialog_does_not_override_saved_full_editor_work(photo):
    proc = editor(photo)
    proc.current_source = "same"
    settings = recipe(photo)
    bridge.sidecar_pcraft_path(photo).write_bytes(b"full editor work")
    proc.dialog = {"open": True, "changed": False, "target": "openRaw", "recipe": settings}
    service = render.FullRenderService(lambda: proc, source_key=lambda path: "same")
    result = service.request(photo)
    assert result.outcome == render.RENDERED
    assert "project" in proc.requests[0] and "recipe" not in proc.requests[0]
    result.release()


def test_recipe_from_another_photo_is_never_exported_as_this_photo(photo):
    proc = editor(photo)
    proc.current_source = "same"
    proc.dialog = {"open": True, "changed": True, "target": "openRaw", "source": "other.dng", "recipe": recipe(photo)}
    result = render.FullRenderService(lambda: proc, source_key=lambda path: "same").request(photo)
    assert result.outcome == render.FAILED
    assert not proc.requests


def test_pending_live_save_uses_immutable_snapshot_before_disk(photo):
    proc = editor(photo)
    proc.current_source = "same"
    bridge.sidecar_pcraft_path(photo).write_bytes(b"old project")
    recipe(photo)

    def save(editor, path):
        assert path == photo
        editor.pending_stashes[str(bridge.sidecar_pcraft_path(photo))] = object()

    service = render.FullRenderService(lambda: proc, source_key=lambda path: "same", save_open_photo=save)
    result = service.request(photo)
    assert result.outcome == render.RENDERED
    assert proc.requests[0]["snapshot"] == "photo.dng.pcraft"
    assert "recipe" not in proc.requests[0]
    result.release()


def test_float_project_cannot_use_a_quantized_png_cache(photo, monkeypatch):
    proc = editor(photo)
    bridge.sidecar_pcraft_path(photo).write_bytes(b"float project")
    cache = bridge.RenderOutput(Path(photo).with_suffix(".png"), "png", 20, 10, 16, True)
    manifest = bridge.RenderManifest(1, "project", "sig", bridge.RENDER_ALGORITHM, {"png": cache}, project_depth=32)
    monkeypatch.setattr(bridge, "current_render_manifest", lambda *args, **kwargs: manifest)
    result = render.FullRenderService(lambda: proc).request(photo)
    assert result.outcome == render.RENDERED and proc.requests[0]["project"]
    result.release()


def test_editor_render_does_not_depend_on_original_decoder_support(photo, monkeypatch):
    from PySide6.QtCore import QSize
    from image_triage import image_resize

    image = QImage(120, 80, QImage.Format.Format_RGBA64)
    monkeypatch.setattr(image_resize, "edited_image_for_export", lambda path: EditedImage(image, None))
    monkeypatch.setattr(image_resize, "_load_resize_image", Mock(side_effect=OSError("unsupported original")))
    loaded = image_resize.load_image_for_export(photo, target_size=QSize(), ignore_orientation=False, strip_metadata=False)
    assert loaded.image.size() == image.size()


def test_render_must_describe_actual_size_depth_and_profile_presence(tmp_path):
    with pytest.raises(bridge.PhotoCraftError):
        render._described(tmp_path / "output.png", {"out": "output.png"}, temporary=True)


def test_an_unexpected_render_path_never_reads_or_removes_the_original(photo):
    proc = editor(photo)
    recipe(photo)
    original = Path(photo).read_bytes()
    proc.lane.call.side_effect = lambda *args: {"out": photo}
    assert render.FullRenderService(lambda: proc).request(photo).outcome == render.FAILED
    assert Path(photo).read_bytes() == original


def test_temporary_editor_is_unhashable_and_still_quits(photo):
    recipe(photo)
    proc = editor(photo)
    service = render.FullRenderService(lambda: None, launch=lambda folder: proc)
    result = service.request(photo)
    assert result.outcome == render.RENDERED
    proc.shutdown.assert_called_once()
    result.release()
    assert not result.path.exists()


def test_shutdown_during_launch_drains_process_and_refuses_queued_work(photo):
    recipe(photo)
    entered, finish = threading.Event(), threading.Event()
    proc = editor(photo)

    def launch(folder):
        entered.set()
        assert finish.wait(3)
        return proc

    service = render.FullRenderService(lambda: None, launch=launch)
    results = []
    worker = threading.Thread(target=lambda: results.append(service.request(photo)))
    worker.start()
    assert entered.wait(3)
    closer = threading.Thread(target=service.shutdown)
    closer.start()
    finish.set()
    worker.join(3)
    closer.join(3)
    assert not worker.is_alive() and not closer.is_alive()
    assert results[0].outcome == render.FAILED
    assert service.request(photo).outcome == render.FAILED
    assert not proc.requests
    proc.shutdown.assert_called_once()


def test_shutdown_cancels_running_job_through_render_lane(photo):
    recipe(photo)
    proc = editor(photo)
    entered, cancelled = threading.Event(), threading.Event()

    def call(method, params=None):
        if method == "app.render":
            return {"pending": True, "job": 17}
        if method == "jobs.cancel":
            cancelled.set()
            return {}
        entered.set()
        return {"jobs": [{"id": 17, "state": "cancelled" if cancelled.is_set() else "running"}]}

    proc.lane.call.side_effect = call
    service = render.FullRenderService(lambda: proc)
    results = []
    worker = threading.Thread(target=lambda: results.append(service.request(photo)))
    worker.start()
    assert entered.wait(3)
    service.shutdown()
    worker.join(3)
    assert cancelled.is_set()
    assert results[0].outcome == render.FAILED


@pytest.mark.parametrize("value", ["bad JSON", '{"version":1,"cameraRaw":{"exposure":[]}}', '{"version":1,"cameraRaw":{"exposure":99}}'])
def test_unreadable_or_invalid_recipe_never_becomes_original(photo, value):
    bridge.recipe_path(photo).write_text(value)
    assert render.FullRenderService(lambda: None).request(photo).outcome == render.FAILED


def test_quarantining_unreadable_edits_does_not_turn_them_into_no_edits(photo):
    from image_triage.edit_storage import session_has_edits

    bridge.recipe_path(photo).write_bytes(b"\xffbad recipe")
    assert bridge.recipe_state(photo)[0] == bridge.RECIPE_INVALID
    assert bridge.load_recipe(photo) is None
    assert not bridge.recipe_path(photo).exists()
    assert session_has_edits(photo)
    assert render.FullRenderService(lambda: None).request(photo).outcome == render.FAILED


def test_identity_recipe_needs_no_editor(photo):
    recipe(photo, {"sharpenRadius": 2}, identity=True)
    assert render.FullRenderService(lambda: None).request(photo) == render.NO_EDITS


def test_recipe_identity_uses_settings_and_cannot_hide_an_exposure(photo):
    assert bridge.classify_recipe({"version": 1, "cameraRaw": {"exposure": 0, "sharpenRadius": 2}})[0] == bridge.RECIPE_IDENTITY
    assert bridge.classify_recipe({"version": 1, "cameraRaw": {"exposure": 1}, "identity": True})[0] == bridge.RECIPE_EDITED
    assert bridge.classify_recipe({"version": 1, "cameraRaw": {"hslHue": [0] * 8, "pointCurve": [[0, 0], [255, 255]]}})[0] == bridge.RECIPE_IDENTITY
    assert bridge.classify_recipe({"version": True, "cameraRaw": {}})[0] == bridge.RECIPE_INVALID


@pytest.mark.parametrize("settings", [{"seed": -1}, {"pointCurve": [[0, 0]]}, {"pointCurve": [[100, 0], [99, 255]]}, {"futureSetting": 1}])
def test_malformed_settings_cannot_be_classified_as_identity(settings):
    assert bridge.classify_recipe({"version": 1, "cameraRaw": settings})[0] == bridge.RECIPE_INVALID


def test_host_recipe_schema_tracks_the_editor_schema():
    source = Path(__file__).resolve().parents[2] / "photocraft/crates/algo/src/camera_raw.rs"
    if not source.is_file():
        pytest.skip("companion checkout is not available")
    body = re.search(r"pub struct CameraRaw \{(.*?)\n\}", source.read_text(), re.S).group(1)
    names = re.findall(r"pub (\w+):", body)
    camel = lambda name: name.split("_")[0] + "".join(word.title() for word in name.split("_")[1:])
    assert {camel(name) for name in names} == bridge.RECIPE_FIELDS


def test_live_edits_without_any_sidecar_reach_export(photo, monkeypatch):
    image = QImage(120, 80, QImage.Format.Format_RGBA64)
    image.fill(QColor("red"))
    monkeypatch.setattr("image_triage.image_resize.edited_image_for_export", lambda path: EditedImage(image, None))
    loaded = _LoadedResizeImage(QImage(20, 10, QImage.Format.Format_RGB32))
    assert apply_edits_if_present(photo, loaded).image.size() == image.size()


@pytest.mark.parametrize("fmt,suffix,depth", [(QImage.Format.Format_RGBA64, ".png", 16), (QImage.Format.Format_RGBA64, ".tif", 16), (QImage.Format.Format_RGBA32FPx4, ".tif", 32)])
def test_full_pixels_keep_depth_and_prophoto_profile(tmp_path, monkeypatch, fmt, suffix, depth):
    image = QImage(120, 80, fmt)
    image.fill(QColor.fromRgbF(0.4, 0.5, 0.6))
    image.setColorSpace(QColorSpace(QColorSpace.NamedColorSpace.ProPhotoRgb))
    path = tmp_path / ("render" + suffix)
    assert image.save(str(path))
    result = render.RenderResult(render.RENDERED, path=path, width=120, height=80, depth=depth, icc=True)
    monkeypatch.setattr(render, "request_full_render", lambda source: result)
    decoded = edited_image_for_export("photo.dng")
    target = tmp_path / ("output" + suffix)
    save_preserving_depth(decoded.image, target_path=str(target), target_suffix=suffix, exif_bytes=None, icc_profile=decoded.icc_profile)
    output = QImage(str(target))
    assert bit_depth(output) == depth
    assert output.colorSpace() == image.colorSpace()
    assert output.size() == image.size()
    jpeg = tmp_path / "eight.jpg"
    note = _save_resized_image(decoded.image, target_path=str(jpeg), target_suffix=".jpg", exif_bytes=None, icc_profile=decoded.icc_profile)
    assert "8 bits" in note


def test_no_full_render_may_enlarge_the_display_cache(photo):
    bridge.sidecar_pcraft_path(photo).write_bytes(b"project without full render")
    display = QImage(20, 10, QImage.Format.Format_RGB32)
    assert display.save(str(bridge.display_render_path(photo)))
    assert render.FullRenderService(lambda: None).request(photo).outcome == render.FAILED


def test_failed_edited_photo_is_reported_and_batch_continues(tmp_path, monkeypatch):
    from image_triage.image_convert import ConvertOptions, ConvertSourceItem, apply_convert_plan, build_convert_plan

    paths = [tmp_path / name for name in ("bad.png", "good.png")]
    for path in paths:
        image = QImage(12, 8, QImage.Format.Format_RGB32)
        image.fill(QColor("blue"))
        assert image.save(str(path))
    monkeypatch.setattr(render, "request_full_render", lambda path: render.failed("editor render failed") if Path(path).name == "bad.png" else render.NO_EDITS)
    options = ConvertOptions(output_suffix=".tiff")
    plan = build_convert_plan([ConvertSourceItem(str(path), path.name) for path in paths], options)
    result = apply_convert_plan(plan, options)
    assert len(result) == 1 and "good" in result[0]
    assert result.failed == ((str(paths[0]), "editor render failed"),)
    assert "bad.png" in result.report()
    assert not Path(plan.executable_items[0].target_path).exists()
