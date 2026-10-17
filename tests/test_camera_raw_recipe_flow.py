"""A raw in the Camera Raw workspace opens in Camera Raw with its stored recipe (not from a saved document),
leaving it keeps the recipe without developing or saving, and developing it (Open) is not mistaken for editing."""
from __future__ import annotations

import json
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

def test_a_raw_opens_in_camera_raw_only_in_the_workspace_and_only_with_a_capable_editor(controller, tmp_path):
    photo = str(tmp_path / "frame.NEF")
    assert not controller._camera_raw_mode(str(tmp_path / "frame.jpg"))
    controller._photocraft = raw_process(tmp_path)
    assert controller._camera_raw_mode(photo)
    assert not controller._camera_raw_mode(str(tmp_path / "frame.jpg")), "only raws"
    controller._photocraft = raw_process(tmp_path, camera_raw_first=False)
    assert not controller._camera_raw_mode(photo)
    controller._photocraft = raw_process(tmp_path, supported=False)
    assert not controller._camera_raw_mode(photo), "an editor without the capability keeps the old route"


def test_the_spec_names_where_the_recipe_and_its_render_go(controller, tmp_path):
    photo = str(tmp_path / "frame.NEF")
    spec = controller._camera_raw_spec(photo, raw_process(tmp_path))
    assert spec == {
        "recipePath": "frame.NEF.cameraraw.json",
        "displayPath": "frame.NEF.photocraft.display.jpg",
        "manifestPath": "frame.NEF.photocraft.renders.json",
    }


def test_the_spec_carries_the_stored_recipe(controller, tmp_path):
    photo = tmp_path / "frame.NEF"
    recipe = {"version": 1, "cameraRaw": {"exposure": 1.5}}
    write_recipe(photo, recipe)
    assert controller._camera_raw_spec(str(photo), raw_process(tmp_path))["recipe"] == recipe


def test_a_saved_full_editor_document_is_what_open_continues(controller, tmp_path):
    photo = tmp_path / "frame.NEF"
    sidecar = bridge.sidecar_pcraft_path(str(photo))
    sidecar.parent.mkdir(parents=True)
    sidecar.write_bytes(b"project")
    spec = controller._camera_raw_spec(str(photo), raw_process(tmp_path))
    assert spec["continueFrom"] == ".image_triage_edits/frame.NEF.pcraft"


def test_a_save_still_in_flight_is_continued_from_the_editors_memory(controller, tmp_path):
    photo = tmp_path / "frame.NEF"
    proc = raw_process(tmp_path)
    proc.pending_stashes[str(bridge.sidecar_pcraft_path(str(photo)))] = (4, str(photo))
    assert controller._camera_raw_spec(str(photo), proc)["continueFrom"] == "frame.NEF.pcraft"


def test_a_raw_opens_from_the_raw_even_when_a_full_editor_document_exists(controller, tmp_path):
    photo = tmp_path / "frame.NEF"
    sidecar = bridge.sidecar_pcraft_path(str(photo))
    sidecar.parent.mkdir(parents=True)
    sidecar.write_bytes(b"project")
    controller._photocraft = raw_process(tmp_path)
    assert controller._resolve_photocraft_target(str(photo)) == (str(photo), str(sidecar))
    controller._photocraft = raw_process(tmp_path, camera_raw_first=False)
    assert controller._resolve_photocraft_target(str(photo)) == (str(sidecar), str(sidecar)), "outside the workspace nothing changes"


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


def switching(controller, tmp_path, *, can_hand_over):
    proc = raw_process(tmp_path)
    proc.control.camera_raw_switch_supported = can_hand_over
    proc.control.call.side_effect = lambda method, params=None: {"open": True} if method == "ui.cameraRaw" else {}
    proc.control.app_open = Mock(return_value={"cameraRaw": {"dialog": True}})
    controller._photocraft = proc
    controller._switch_photocraft_document(
        proc, str(tmp_path / "next.NEF"), str(tmp_path / ".image_triage_edits" / "next.pcraft"), camera_raw={"recipePath": "next.json"}
    )
    return proc, [(call.args[0], call.args[1] if len(call.args) > 1 else None) for call in proc.control.call.call_args_list]


def test_an_editor_that_can_hand_the_dialog_over_is_never_told_to_close_it(controller, tmp_path):
    proc, sent = switching(controller, tmp_path, can_hand_over=True)
    assert ("ui.cameraRaw", {"done": True}) not in sent, "closing the dialog would show it going away and coming back"
    proc.control.app_open.assert_called_once()
    assert proc.control.app_open.call_args.kwargs["camera_raw"] == {"recipePath": "next.json"}


def test_an_older_editor_is_still_told_to_leave_the_photo_first(controller, tmp_path):
    proc, sent = switching(controller, tmp_path, can_hand_over=False)
    assert ("ui.cameraRaw", {"done": True}) in sent
    proc.control.app_open.assert_called_once()


def test_a_busy_dialog_does_not_count_as_the_next_photo_being_up():
    ready = PreviewController._dialog_view_ready
    view = {"x": 1, "y": 2, "width": 3, "height": 4}
    assert not ready({"cameraRaw": {"open": True, "busy": True, "view": view}}), "still the previous photo's dialog"
    assert ready({"cameraRaw": {"open": True, "busy": False, "view": view}})


# --- leaving, and developing --------------------------------------------------------------------------

def test_leaving_a_raw_asks_the_editor_to_keep_its_recipe_and_does_not_save_a_document(controller, tmp_path):
    proc = raw_process(tmp_path, rebaseline=False)
    proc.control.call.return_value = {"open": False}
    controller._save_photocraft_sidecar_if_dirty(proc)
    assert proc.control.call.call_args_list[0].args == ("ui.cameraRaw", {"done": True})
    assert "app.stash" not in methods(proc.control)


def test_developing_a_raw_with_open_is_not_mistaken_for_editing_it(controller, tmp_path):
    proc = raw_process(tmp_path, rebaseline=True)
    proc.control.document_revision.return_value = 3  # Open re-developed the document: its revision moved
    proc.control.call.side_effect = [{"open": False}, {"jobs": []}]
    controller._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=False)
    assert "app.stash" not in methods(proc.control)
    assert proc.opened_revision == 3 and proc.camera_raw_rebaseline is False


def test_edits_after_open_are_saved_once_the_baseline_is_taken(controller, tmp_path):
    proc = raw_process(tmp_path, rebaseline=True)
    proc.control.document_revision.return_value = 3
    proc.control.call.side_effect = [{"open": False}, {"jobs": []}]
    controller._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=False)
    proc.control.document_revision.return_value = 6  # the user painted in the full editor
    proc.control.call.side_effect = [{"open": False}, {"job": 9, "pending": True}]
    controller._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=False)
    assert methods(proc.control)[-1] == "app.stash"


def stashes_sent(control):
    return [call for call in control.call.call_args_list if call.args[0] == "app.stash"]


def test_an_edit_made_before_the_first_poll_is_saved_not_taken_for_the_baseline(controller, tmp_path):
    """Developed at revision 1, edited to 2 before the 1.5 s poll came round: the editor holds the document as changed, so the
    poll that closes the dialog saves it at once instead of taking revision 2 as "unchanged"."""
    proc = raw_process(tmp_path, rebaseline=True, dirty=True)
    proc.control.document_revision.return_value = 2
    proc.control.call.side_effect = [{"open": False}, {"jobs": []}, {"job": 9, "pending": True}]
    controller._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=False)
    assert len(stashes_sent(proc.control)) == 1, "saved in the same call"
    assert proc.camera_raw_rebaseline is False
    assert str(proc.current_sidecar) in proc.save_required


@pytest.mark.parametrize("leaving", [False, True], ids=["autosave", "leave/quit"])
def test_the_required_save_happens_in_the_call_that_finds_it(controller, tmp_path, leaving):
    proc = raw_process(tmp_path, rebaseline=True, dirty=True)
    proc.control.document_revision.return_value = 2
    proc.control.call.side_effect = [{"open": False}, {"jobs": []}, {"job": 9, "pending": True}]
    controller._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=leaving)
    assert len(stashes_sent(proc.control)) == 1


def test_an_editor_that_does_not_say_whether_it_changed_is_saved_to_be_safe(controller, tmp_path):
    proc = raw_process(tmp_path, rebaseline=True, dirty=None)
    proc.control.document_revision.return_value = 3
    proc.control.call.side_effect = [{"open": False}, {"jobs": []}, {"job": 9, "pending": True}]
    controller._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=False)
    assert len(stashes_sent(proc.control)) == 1


def _finish(controller, proc, job_id, state="done", revision=2, path=None):
    proc.control.call.side_effect = None
    proc.control.call.return_value = {"jobs": [{"id": job_id, "command": "app.stash", "state": state, "result": {"revision": revision, "path": path}}]}
    controller._poll_photocraft_stashes(proc)


def test_only_the_matching_save_settles_the_requirement(controller, tmp_path):
    proc = raw_process(tmp_path, rebaseline=True, dirty=True)
    proc.control.document_revision.return_value = 2
    proc.control.call.side_effect = [{"open": False}, {"jobs": []}, {"job": 9, "pending": True}]
    controller._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=False)
    key = str(proc.current_sidecar)
    # Another photo's save finishing does not settle it.
    proc.pending_stashes["other.pcraft"] = (7, "other.NEF")
    proc.stash_meta[7] = ("other.pcraft", proc.document_generation, 100)
    _finish(controller, proc, 7)
    assert key in proc.save_required
    # A failed save of this photo keeps it.
    proc.pending_stashes[key] = (9, proc.source_path)
    _finish(controller, proc, 9, state="failed")
    assert key in proc.save_required
    # The matching successful save settles it.
    proc.pending_stashes[key] = (10, proc.source_path)
    proc.stash_meta[10] = (key, proc.document_generation, 2)
    _finish(controller, proc, 10)
    assert key not in proc.save_required


def test_an_older_documents_save_does_not_settle_a_newer_documents_requirement(controller, tmp_path):
    """Revisions start again with every document: revision 100 of the photo's previous document says nothing about revision 2
    of the one open now."""
    proc = raw_process(tmp_path, rebaseline=True, dirty=True)
    key = str(proc.current_sidecar)
    old_generation = proc.document_generation
    proc.pending_stashes[key] = (8, proc.source_path)
    proc.stash_meta[8] = (key, old_generation, 100)
    proc.document_generation += 1  # the photo was opened again
    proc.save_required[key] = (proc.document_generation, 2)
    _finish(controller, proc, 8, revision=100)
    assert proc.save_required[key] == (proc.document_generation, 2)


def test_a_save_that_completes_at_once_settles_its_requirement(controller, tmp_path):
    proc = raw_process(tmp_path, rebaseline=True, dirty=True)
    proc.control.document_revision.return_value = 2
    proc.control.call.side_effect = [{"open": False}, {"jobs": []}, {"revision": 2}]
    controller._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=False)
    assert str(proc.current_sidecar) not in proc.save_required


def test_the_baseline_waits_for_a_develop_that_is_still_running(controller, tmp_path):
    proc = raw_process(tmp_path, rebaseline=True)
    proc.control.document_revision.return_value = 3
    proc.control.call.side_effect = [{"open": False}, {"jobs": [{"id": 4, "command": "cameraRaw.redevelop", "state": "running"}]}]
    controller._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=False)
    assert proc.opened_revision == 1 and proc.camera_raw_rebaseline is True
    assert "app.stash" not in methods(proc.control)


def test_a_running_stash_does_not_hold_the_baseline_back(controller, tmp_path):
    proc = raw_process(tmp_path, rebaseline=True)
    proc.control.document_revision.return_value = 3
    proc.control.call.side_effect = [{"open": False}, {"jobs": [{"id": 4, "command": "app.stash", "state": "running"}]}]
    controller._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=False)
    assert proc.opened_revision == 3


def test_a_dialog_still_open_is_left_alone(controller, tmp_path):
    proc = raw_process(tmp_path, rebaseline=True)
    proc.control.call.return_value = {"open": True}
    controller._save_photocraft_sidecar_if_dirty(proc, commit_raw_draft=False)
    assert proc.camera_raw_rebaseline is True and proc.opened_revision == 1
    proc.control.document_revision.assert_not_called()
