"""A raw in the Camera Raw workspace opens in Camera Raw with its stored recipe (not from a saved document),
leaving it keeps the recipe without developing or saving, and developing it (Open) is not mistaken for editing."""
from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from image_triage import photocraft_bridge as bridge
from image_triage.scanner import normalized_path_key




def raw_process(tmp_path, *, name="frame.NEF", camera_raw_first=True, rebaseline=True, supported=True, dirty=False):
    source = str(tmp_path / name)
    control = Mock()
    control.camera_raw_supported = supported
    control.document_revision.return_value = 1
    # The editor's own view of the document: its revision, and whether it holds it as changed since the develop.
    control.dirty = dirty
    control.document_state.side_effect = lambda: (control.document_revision.return_value, control.dirty)
    control.call.return_value = {"job": 5, "pending": True}
    proc = bridge.PhotoCraftProcess(
        process=Mock(), control=control, hwnd=123, token_file=tmp_path / "token",
        read_root=tmp_path, write_root=tmp_path / ".image_triage_edits",
        source_path=source, current_source=normalized_path_key(source),
        current_sidecar=bridge.sidecar_pcraft_path(source), opened_revision=1,
        camera_raw_first=camera_raw_first, camera_raw_rebaseline=rebaseline,
    )
    proc.process.poll.return_value = None
    return proc


def methods(control):
    return [call.args[0] for call in control.call.call_args_list]


# --- the recipe file ----------------------------------------------------------------------------------

def write_recipe(photo, recipe):
    target = bridge.recipe_path(str(photo))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(recipe), encoding="utf-8")
    return target


def test_a_photo_without_a_recipe_has_none(tmp_path):
    assert bridge.load_recipe(str(tmp_path / "frame.NEF")) is None


def test_a_stored_recipe_is_loaded(tmp_path):
    photo = tmp_path / "frame.NEF"
    recipe = {"version": 1, "cameraRaw": {"exposure": 1.5}}
    write_recipe(photo, recipe)
    assert bridge.load_recipe(str(photo)) == recipe


@pytest.mark.parametrize("content", ["not json", "[1, 2]", '{"version": 2, "cameraRaw": {}}', '{"version": 1}', '{"version": 1, "cameraRaw": 5}'])
def test_an_unusable_recipe_is_set_aside_not_deleted_and_not_used(tmp_path, content):
    photo = tmp_path / "frame.NEF"
    target = bridge.recipe_path(str(photo))
    target.parent.mkdir(parents=True)
    target.write_text(content, encoding="utf-8")
    assert bridge.load_recipe(str(photo)) is None
    assert not target.exists()
    assert target.with_name(target.name + ".bad").read_text(encoding="utf-8") == content


# --- opening a raw in Camera Raw ----------------------------------------------------------------------













def test_the_bridge_asks_the_editor_to_open_a_raw_in_camera_raw():
    control = object.__new__(bridge.PhotoCraftControl)
    control.camera_raw_supported = True
    control.call = Mock(return_value={"cameraRaw": {"dialog": True}})
    spec = {"recipePath": "a.json"}
    result = control.app_open("frame.NEF", replace=True, camera_raw=spec)
    control.call.assert_called_once_with("app.open", {"path": "frame.NEF", "replace": True, "cameraRaw": spec})
    assert result["cameraRaw"]["dialog"] is True


def test_a_raw_the_editor_cannot_develop_is_left_as_the_editor_opened_it():
    control = object.__new__(bridge.PhotoCraftControl)
    control.camera_raw_supported = True
    control.call = Mock(return_value={"cameraRaw": {"dialog": False}})
    result = control.app_open("frame.dng", replace=True, camera_raw={"recipePath": "a.json"})
    # One open: the editor showed the camera's embedded preview, and there is no second route to try.
    control.call.assert_called_once()
    assert result == {"cameraRaw": {"dialog": False}}


def test_an_editor_without_the_capability_ignores_the_spec():
    control = object.__new__(bridge.PhotoCraftControl)
    control.camera_raw_supported = False
    control.call = Mock(return_value={})
    control.app_open("frame.dng", replace=True, camera_raw={"recipePath": "a.json"})
    (method, params), = [c.args for c in control.call.call_args_list]
    assert params == {"path": "frame.dng", "replace": True}


def test_the_handoff_probe_reads_the_capability():
    answers = {
        "app.stash": "stash requires path and preview",
        "app.bind": "bind requires an open document, .pcraft path and .png preview",
    }

    def probe(capabilities):
        control = object.__new__(bridge.PhotoCraftControl)

        def call(method, params=None):
            if method == "app.handoff":
                return capabilities
            raise bridge.PhotoCraftError(answers[method])

        control.call = call
        control.require_hosted_handoff()
        return control

    assert probe({"version": 2, "cameraRaw": 1}).camera_raw_supported is True
    assert probe({"version": 2}).camera_raw_supported is False
    assert probe({"version": 2, "cameraRaw": 1, "cameraRawSwitch": 1}).camera_raw_switch_supported is True
    assert probe({"version": 2, "cameraRaw": 1, "cameraRawPrefetch": 1}).camera_raw_switch_supported is False










# --- leaving, and developing --------------------------------------------------------------------------







def stashes_sent(control):
    return [call for call in control.call.call_args_list if call.args[0] == "app.stash"]
