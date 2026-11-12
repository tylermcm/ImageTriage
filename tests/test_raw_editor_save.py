"""RAW saves publish only after a complete render, leaving failed drafts retryable."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QImage, QColor

from image_triage import photocraft_bridge as bridge
from image_triage.qml_shell.raw_editor import RawEditor
from image_triage.raw_crop import publish
from test_qml_shell import qapp  # noqa: F401


def picture(path):
    image = QImage(32, 24, QImage.Format_RGBA64)
    image.fill(QColor("red"))
    assert image.save(str(path))


@pytest.fixture
def save_job(tmp_path, monkeypatch):
    raw = tmp_path / "photo.dng"
    raw.write_bytes(b"original RAW")
    initial = tmp_path / "initial.png"
    picture(initial)
    original = {"exposure": .25}
    publish(str(raw), initial, original, bridge.file_signature(str(raw)))
    recipe = bridge.recipe_path(str(raw))
    manifest = bridge.render_manifest_path(str(raw))
    before = (recipe.read_bytes(), manifest.read_bytes())
    jobs = []
    state = SimpleNamespace(fail=False, corrupt=False, rendered=None, during=None)

    def launch(*args, write_root, **kwargs):
        def call(method, params):
            if method == "ui.menu.invoke":
                return {}
            if method == "ui.cameraRaw":
                recipe.write_bytes(b'{"version":1,"cameraRaw":{"exposure":1}}')
                return {"open": False, "result": {"changed": True}}
            assert method == "app.render"
            # The recipe and manifest must remain the previous generation throughout rendering.
            assert (recipe.read_bytes(), manifest.read_bytes()) == before
            if state.during:
                state.during()
            if state.fail:
                raise bridge.PhotoCraftError("render timed out")
            state.rendered = Path(write_root) / (params["out"] + ".png")
            if state.corrupt:
                state.rendered.write_bytes(b"incomplete image")
            else:
                picture(state.rendered)
            return {"out": state.rendered.name}

        proc = SimpleNamespace(control=SimpleNamespace(full_render_supported=True,
                               render_manifest_supported=True, call=call,
                               app_open=lambda *a, **kw: {"cameraRaw": {"dialog": True}}),
                               path_within_root=lambda path: Path(path).name,
                               stopped=False, is_running=lambda: False)
        proc.shutdown = lambda: setattr(proc, "stopped", True)
        jobs.append(proc)
        return proc

    monkeypatch.setattr(bridge, "launch_photocraft", launch)
    return raw, original, recipe, manifest, before, jobs, state


def test_basic_save_publishes_completed_render_and_keeps_host_settings(save_job):
    raw, original, recipe, manifest, before, jobs, state = save_job
    draft = {"exposure": 1, "superResolution": False}
    result = RawEditor._work("save", str(raw), original, draft, "")
    assert bridge.load_recipe(str(raw))["cameraRaw"] == draft
    assert bridge.full_render_path(str(raw)) == Path(result)
    assert bridge.saved_render_path(str(raw)).exists()
    assert QImage(result).size().width() == 32
    assert QImage(result).depth() > 32
    assert raw.read_bytes() == b"original RAW"
    assert jobs[-1].stopped and not state.rendered.exists()


@pytest.mark.parametrize("failure", ["fail", "corrupt"])
def test_render_failure_preserves_saved_generation_and_can_retry(save_job, qapp, failure):
    raw, original, recipe, manifest, before, jobs, state = save_job
    draft = {"exposure": 1}
    setattr(state, failure, True)
    with pytest.raises((bridge.PhotoCraftError, ValueError)) as caught:
        RawEditor._work("save", str(raw), original, draft, "")
    assert (recipe.read_bytes(), manifest.read_bytes()) == before
    assert jobs[-1].stopped
    editor = RawEditor(memory=True)
    editor.open(str(raw))
    editor._original, editor._draft, editor._saving = original, draft, True
    editor._arrived(editor._revision, "save", "", str(caught.value))
    assert not editor.saving and editor.dirty and editor.error
    assert editor._draft == draft and editor._original == original
    setattr(state, failure, False)
    result = RawEditor._work("save", str(raw), editor._original, editor._draft, "")
    editor._arrived(editor._revision, "save", result, "")
    assert not editor._path and bridge.load_recipe(str(raw))["cameraRaw"] == draft
    editor.shutdown()


def test_recipe_changed_during_render_is_not_overwritten(save_job):
    raw, original, recipe, manifest, before, jobs, state = save_job
    state.during = lambda: recipe.write_bytes(b'{"version":1,"cameraRaw":{"exposure":2}}')
    with pytest.raises(ValueError, match="saved recipe changed"):
        RawEditor._work("save", str(raw), original, {"exposure": 1}, "")
    assert bridge.load_recipe(str(raw))["cameraRaw"] == {"exposure": 2}
    assert manifest.read_bytes() == before[1]
    assert jobs[-1].stopped and not state.rendered.exists()
