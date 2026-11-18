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


def _write_recipe(path: str, settings: dict, identity=None) -> Path:
    file = bridge.recipe_path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    recipe = {"version": 1, "cameraRaw": settings}
    if identity is not None:
        recipe["identity"] = identity                   # only PhotoCraft writes it
    file.write_text(json.dumps(recipe), encoding="utf-8")
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
    # What an editor without `raw_recipe::Targets::extra` writes on Done: its own settings only (and its identity flag).
    file = _write_recipe(path, {"exposure": 1.25}, identity=True)
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


def test_renders_run_one_at_a_time_and_a_photo_saved_again_renders_again(qapp, folder, monkeypatch):
    host, path, _spec = _open(qapp, folder, monkeypatch, {"version": 1, "cameraRaw": {}})
    other = str(folder / "other.nef")
    release, calls, running, overlap = threading.Event(), [], [], []

    def renderer(photo):
        running.append(photo)
        if len(running) > 1:
            overlap.append(list(running))
        calls.append(photo)
        if len(calls) == 1:
            release.wait(5)
        running.remove(photo)
        return False

    host._renderer = renderer
    host._render_host_owned(path)
    assert wait(qapp, lambda: len(calls) == 1)
    host._render_host_owned(other)
    host._render_host_owned(path)                      # saved again while it renders
    host._render_host_owned(other)                     # saved again while it waits: once is enough
    release.set()
    assert wait(qapp, lambda: host._host_worker is None and len(calls) == 3)
    assert calls == [path, other, path]
    assert not overlap, "full-size renders never run side by side"


def test_a_crop_removed_in_image_triage_is_not_put_back(qapp, folder, monkeypatch):
    done = threading.Event()
    host, path, _spec = _open(qapp, folder, monkeypatch, {"version": 1, "cameraRaw": {"hostCrop": CROP}},
                              lambda photo: done.set() or False)
    # Image Triage's RAW editor wrote the recipe last (no identity flag): the crop was taken off on purpose.
    file = _write_recipe(path, {"exposure": 0.3})
    host._render_host_owned(path)
    assert done.wait(5)
    assert json.loads(file.read_text(encoding="utf-8"))["cameraRaw"] == {"exposure": 0.3}


def test_closing_without_saving_forgets_what_the_photo_was_opened_with(qapp, folder, monkeypatch):
    host, path, _spec = _open(qapp, folder, monkeypatch, {"version": 1, "cameraRaw": {"hostCrop": CROP}}, lambda photo: False)
    assert host._kept[path] == {"hostCrop": CROP}
    host.closeWithoutSaving()
    assert path not in host._kept


def test_an_older_editors_every_save_is_put_right(qapp, folder, monkeypatch):
    host, path, _spec = _open(qapp, folder, monkeypatch, {"version": 1, "cameraRaw": {"aiDenoise": DENOISE}}, lambda photo: False)
    for exposure in (0.5, 0.75):                       # Save, then later Done, while the photo stays open
        file = _write_recipe(path, {"exposure": exposure}, identity=True)
        host._render_host_owned(path)
        assert json.loads(file.read_text(encoding="utf-8"))["cameraRaw"] == {"exposure": exposure, "aiDenoise": DENOISE}


def test_a_photo_without_a_recipe_but_with_an_effect_leaves_its_picture_to_image_triage(qapp, folder, monkeypatch):
    from image_triage import host_effects

    monkeypatch.setattr(host_effects, "load_extras", lambda recipe_path, settings: (settings, {"atmosphere": {"amount": 40}}))
    _host, _path, spec = _open(qapp, folder, monkeypatch, None)
    assert "displayPath" not in spec


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
    assert worked == [("picture", path, {"exposure": 0.5, "hostCrop": CROP}, {"exposure": 0.5, "hostCrop": CROP})]
    monkeypatch.setattr(bridge, "current_render_manifest", lambda photo, verify_project: object())
    assert editor_host._render_with_host_settings(path) is False, "the picture is already of this recipe"
    monkeypatch.setattr(bridge, "current_render_manifest", lambda photo, verify_project: None)
    bridge.sidecar_pcraft_path(path).parent.mkdir(parents=True, exist_ok=True)
    bridge.sidecar_pcraft_path(path).write_bytes(b"project")
    assert editor_host._render_with_host_settings(path) is False, "a PhotoCraft project is what the grid shows"
    assert len(worked) == 1


def _rendered_png(folder) -> Path:
    from PySide6.QtGui import QColor, QImage

    image = QImage(40, 30, QImage.Format.Format_RGB32)
    image.fill(QColor(90, 120, 150))
    target = folder / "rendered.png"
    assert image.save(str(target))
    return target


def test_publishing_only_the_picture_never_writes_the_recipe(qapp, folder):
    from image_triage.raw_crop import publish

    path = str(folder / "raw.nef")
    Path(path).write_bytes(b"raw")
    settings = {"exposure": 0.5, "hostCrop": CROP}
    file = _write_recipe(path, settings, identity=False)
    before = file.read_bytes()
    publish(path, _rendered_png(folder), settings, bridge.file_signature(path), recipe_on_disk=True)
    assert file.read_bytes() == before, "the recipe on disk is left exactly as the editor wrote it"
    assert bridge.current_render_manifest(path, verify_project=False) not in (None, False), "and the picture is its picture"


def test_a_recipe_saved_again_during_the_render_is_never_written_over(qapp, folder):
    import pytest

    from image_triage.raw_crop import publish

    path = str(folder / "raw.nef")
    Path(path).write_bytes(b"raw")
    file = _write_recipe(path, {"exposure": 1.0, "hostCrop": CROP}, identity=False)   # the newer save
    before = file.read_bytes()
    with pytest.raises(ValueError, match="changed"):
        publish(path, _rendered_png(folder), {"exposure": 0.5, "hostCrop": CROP}, bridge.file_signature(path), recipe_on_disk=True)
    assert file.read_bytes() == before
    assert bridge.read_render_manifest(path) is None


def test_an_export_from_the_open_dialog_keeps_image_triages_settings(folder, monkeypatch):
    from image_triage import full_render, host_effects

    path = str(folder / "raw.nef")
    _write_recipe(path, {"exposure": 0.5, "hostCrop": CROP, "aiDenoise": DENOISE})
    monkeypatch.setattr(host_effects, "recipe_for_render", lambda photo, recipe: {**recipe, "laid": True})
    dialog = {"version": 1, "cameraRaw": {"exposure": 0.9}, "identity": False}       # an older editor's dialog
    made = full_render._with_host_settings(path, dialog)
    assert made["cameraRaw"] == {"exposure": 0.9, "hostCrop": CROP, "aiDenoise": DENOISE} and made["laid"]
    newer = {"version": 1, "cameraRaw": {"exposure": 0.9, "hostCrop": {**CROP, "x": 0.3}}}
    assert full_render._with_host_settings(path, newer)["cameraRaw"]["hostCrop"]["x"] == 0.3, "the dialog's own wins"


def test_quitting_while_editing_puts_back_what_an_older_editor_left_out(qapp, folder, monkeypatch):
    host, path, _spec = _open(qapp, folder, monkeypatch, {"version": 1, "cameraRaw": {"hostCrop": CROP}}, lambda photo: False)
    file = _write_recipe(path, {"exposure": 0.4}, identity=True)       # what the older editor writes as the app quits
    host.shutdown()
    assert json.loads(file.read_text(encoding="utf-8"))["cameraRaw"] == {"exposure": 0.4, "hostCrop": CROP}
