"""Image Triage's own RAW settings (crop and Transform, AI Denoise, Light Depth, Atmosphere, Bokeh) survive an edit in the hosted
PhotoCraft editor, and the photo's picture shows them afterwards."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from image_triage import photocraft_bridge as bridge
from image_triage.qml_shell import editor_host
from image_triage.qml_shell.editor_host import EDITING
from image_triage.raw_crop import DEFAULT_CROP
from test_qml_shell_editor import folder, make, wait  # noqa: F401  (fixtures and helpers)
from test_qml_shell import qapp  # noqa: F401

CROP = {**DEFAULT_CROP, "x": 0.1, "w": 0.8, "vertical": -20.0, "upright": "guided", "guides": [[0.1, 0.1, 0.12, 0.9]]}
DENOISE = {"enabled": True, "amount": 60}


def _write_recipe(path: str, settings: dict) -> Path:
    file = bridge.recipe_path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps({"version": 1, "cameraRaw": settings}), encoding="utf-8")
    return file


def _open(qapp, folder, monkeypatch, recipe, renderer=None):
    monkeypatch.setattr(bridge, "load_recipe", lambda path: recipe)
    host, _mover, _launched = make(qapp, folder)
    if renderer is not None:
        host._renderer = renderer
    path = str(folder / "raw.nef")
    host.openPath(path)
    assert wait(qapp, lambda: host.state == EDITING)
    return host, path, host._proc.control.raw_specs[-1]


def test_a_photo_with_only_photocraft_settings_keeps_photocrafts_picture(qapp, folder, monkeypatch):
    _host, path, spec = _open(qapp, folder, monkeypatch, {"version": 1, "cameraRaw": {"exposure": 0.5, "hostCrop": dict(DEFAULT_CROP)}})
    assert spec["displayPath"] == bridge.display_render_path(path).name


def test_a_cropped_or_denoised_photo_leaves_its_picture_to_image_triage(qapp, folder, monkeypatch):
    for settings in ({"exposure": 0.5, "hostCrop": CROP}, {"aiDenoise": DENOISE}):
        _host, _path, spec = _open(qapp, folder, monkeypatch, {"version": 1, "cameraRaw": settings})
        assert "displayPath" not in spec and "manifestPath" not in spec, settings
        assert spec["recipe"]["cameraRaw"] == settings, "the editor is still given every setting, so it can write them back"


def test_settings_an_older_photocraft_drops_are_put_back_and_the_picture_rendered(qapp, folder, monkeypatch):
    rendered, done = [], threading.Event()

    def renderer(path):
        rendered.append(json.loads(bridge.recipe_path(path).read_text(encoding="utf-8")))
        done.set()
        return True

    opened = {"version": 1, "cameraRaw": {"exposure": 0.5, "hostCrop": CROP, "aiDenoise": DENOISE}}
    host, path, _spec = _open(qapp, folder, monkeypatch, opened, renderer)
    # What an editor without `raw_recipe::Targets::extra` writes on Done: its own settings only.
    file = _write_recipe(path, {"exposure": 1.25})
    saved = []
    host.editSaved.connect(saved.append)
    host.close()
    assert done.wait(5)
    assert rendered[0]["cameraRaw"] == {"exposure": 1.25, "hostCrop": CROP, "aiDenoise": DENOISE}
    assert json.loads(file.read_text(encoding="utf-8"))["cameraRaw"]["exposure"] == 1.25, "the editor's edit is kept"
    assert wait(qapp, lambda: saved.count(path) >= 2), "the grid is told again once the picture is there"


def test_settings_the_editor_kept_are_left_as_they_are(qapp, folder, monkeypatch):
    done = threading.Event()
    host, path, _spec = _open(qapp, folder, monkeypatch, {"version": 1, "cameraRaw": {"hostCrop": CROP}},
                              lambda path: done.set() or False)
    written = {"version": 1, "cameraRaw": {"exposure": 1.0, "hostCrop": {**CROP, "x": 0.2}}, "identity": False}
    file = bridge.recipe_path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(written), encoding="utf-8")
    host.close()
    assert done.wait(5)
    assert json.loads(file.read_text(encoding="utf-8")) == written


def test_a_save_during_a_render_renders_again_after_it(qapp, folder, monkeypatch):
    host, path, _spec = _open(qapp, folder, monkeypatch, {"version": 1, "cameraRaw": {}})
    release, calls, finished = threading.Event(), [], threading.Event()

    def renderer(photo):
        calls.append(photo)
        if len(calls) == 1:
            release.wait(5)
        else:
            finished.set()
        return False

    host._renderer = renderer
    host._render_host_owned(path)
    assert wait(qapp, lambda: len(calls) == 1)
    host._render_host_owned(path)
    host._render_host_owned(path)
    release.set()
    assert finished.wait(5)
    assert wait(qapp, lambda: path not in host._host_renders)
    assert calls == [path, path], "two saves during one render make one more render, not two"


def test_the_picture_is_rendered_only_when_it_is_needed(folder, monkeypatch):
    path = str(folder / "raw.nef")
    worked = []
    monkeypatch.setattr("image_triage.qml_shell.raw_editor.RawEditor._work",
                        staticmethod(lambda action, photo, original, draft, directory: worked.append((action, photo, original, draft))))
    _write_recipe(path, {"exposure": 0.5})
    assert editor_host._render_with_host_settings(path) is False, "nothing of Image Triage's own: PhotoCraft's picture is right"
    _write_recipe(path, {"exposure": 0.5, "hostCrop": CROP})
    assert editor_host._render_with_host_settings(str(folder / "a.jpg")) is False
    assert editor_host._render_with_host_settings(path) is True
    assert worked == [("save", path, {"exposure": 0.5, "hostCrop": CROP}, {"exposure": 0.5, "hostCrop": CROP})]
    monkeypatch.setattr(bridge, "current_render_manifest", lambda photo, verify_project: object())
    assert editor_host._render_with_host_settings(path) is False, "the picture is already of this recipe"
    monkeypatch.setattr(bridge, "current_render_manifest", lambda photo, verify_project: None)
    bridge.sidecar_pcraft_path(path).parent.mkdir(parents=True, exist_ok=True)
    bridge.sidecar_pcraft_path(path).write_bytes(b"project")
    assert editor_host._render_with_host_settings(path) is False, "a PhotoCraft project is what the grid shows"
    assert len(worked) == 1
