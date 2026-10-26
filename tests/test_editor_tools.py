"""Which models each editor tool needs, the message when one is missing, and the host-job gate that answers a blocked mask job at once."""
from __future__ import annotations

from image_triage import camera_raw_host_jobs as hj
from image_triage import editor_tools as tools
from image_triage import ai_setup_plan as plan
from tests.test_camera_raw_host_jobs import Lane, kept, proc


def test_every_tool_needs_only_real_optional_models_of_the_editor_group() -> None:
    editor_models = {m.key for m in plan.models_in(plan.GROUP_EDIT)}
    for tool in tools.TOOLS:
        assert tool.models and set(tool.models) <= editor_models
    assert len({t.kind for t in tools.TOOLS}) == len(tools.TOOLS)


def test_a_tool_is_available_exactly_when_it_has_all_of_its_models() -> None:
    assert tools.available_kinds(()) == frozenset()
    assert tools.available_kinds({"birefnet"}) == {"subjectMask"}
    assert tools.available_kinds({"birefnet", "oneformer"}) == {"subjectMask", "sceneMasks"}
    assert tools.available_kinds({"oneformer", "sam"}) == {"sceneMasks", "clickSelect"}  # people also needs BiRefNet for the edges
    assert tools.available_kinds(plan.all_model_keys()) == {t.kind for t in tools.TOOLS}


def test_any_subset_of_models_works_and_nothing_is_demanded_twice() -> None:
    keys = [m.key for m in plan.models_in(plan.GROUP_EDIT)]
    for mask in range(1 << len(keys)):
        have = {key for bit, key in enumerate(keys) if mask >> bit & 1}
        for tool in tools.TOOLS:
            missing = tools.missing_models(tool.kind, have)
            assert {m.key for m in missing} == set(tool.models) - have
            assert (tool.kind in tools.available_kinds(have)) is (not missing)
            assert (tools.block_reason(tool.kind, have) == "") is (not missing)


def test_the_message_names_what_to_turn_on_and_its_size() -> None:
    assert tools.block_reason("subjectMask", ()) == (
        "Subject and background masks need Subject masks (425 MB). Open the AI chip, choose Set up AI and turn on Editor tools."
    )
    assert tools.block_reason("clickSelect", ()) == "Click selection needs Click select (150 MB). Open the AI chip, choose Set up AI and turn on Editor tools."
    people = tools.block_reason("peopleMasks", {"sam"})
    assert people.startswith("People masks need Scene regions and Subject masks (621 MB).")
    assert tools.block_reason("peopleMasks", plan.all_model_keys()) == ""


def test_other_jobs_are_not_gated() -> None:
    assert tools.block_reason(hj.KIND_SUPER_RESOLUTION, ()) == ""
    assert tools.block_reason("somethingNew", ()) == ""
    assert tools.missing_models("somethingNew", ()) == ()
    assert tools.tool_for("somethingNew") is None


def test_a_mask_job_without_its_model_is_answered_at_once_and_the_window_is_told() -> None:
    told: list[tuple[str, str]] = []
    lane = Lane({"id": 9, "kind": "subjectMask"})
    runner = hj.HostJobRunner(render=lambda *a, **k: kept(), gate=lambda kind: tools.block_reason(kind, ()), on_blocked=lambda kind, why: told.append((kind, why)))
    assert runner.poll(proc(lane)) is False
    assert lane.of("claim") == [9]
    finish = lane.of("finish")[0]
    assert finish["id"] == 9 and finish["ok"] is False and "Subject masks" in finish["message"] and "Editor tools" in finish["message"]
    assert told and told[0][0] == "subjectMask"
    assert not runner.busy


def test_a_mask_job_with_its_model_is_left_for_its_handler_and_super_resolution_is_unaffected() -> None:
    lane = Lane({"id": 3, "kind": "subjectMask"})
    runner = hj.HostJobRunner(render=lambda *a, **k: kept(), gate=lambda kind: tools.block_reason(kind, {"birefnet"}))
    assert runner.poll(proc(lane)) is False and not lane.of("claim") and not lane.of("finish")  # not blocked: not this runner's job to run it
    # Super Resolution still runs, whatever models are installed.
    lane = Lane({"id": 4, "kind": hj.KIND_SUPER_RESOLUTION})
    started = hj.HostJobRunner(render=lambda *a, **k: kept(), spawn_thread=lambda target: target(), gate=lambda kind: tools.block_reason(kind, ()))
    assert started.poll(proc(lane)) is True and lane.of("claim") == [4] and lane.of("finish")[0]["ok"] is True


def test_the_window_shows_why_a_mask_job_was_blocked(main_window, monkeypatch) -> None:
    shown: list[str] = []
    bar = main_window.statusBar()
    monkeypatch.setattr(tools, "installed_models", lambda: frozenset())  # whatever this PC has installed, none of the editor models are
    monkeypatch.setattr(bar, "showMessage", lambda text, timeout=0: shown.append(text))
    runner = main_window._preview_ctl._host_jobs
    lane = Lane({"id": 5, "kind": "sceneMasks"})
    assert runner.poll(proc(lane)) is False
    assert shown and "Landscape masks" in shown[-1] and "Scene regions" in shown[-1]


class ToolsLane(Lane):
    """The real editor answers a poll that carries `tools` with the same state as a plain one."""

    def call(self, method, params=None):
        if params and "tools" in params and len(params) == 1:
            self.calls.append(params)
            return {"pending": self.pending}
        return super().call(method, params)


def proc_with(lane, *, tools_supported):
    p = proc(lane)
    p.control.camera_raw_host_tools_supported = tools_supported
    return p


def test_an_editor_that_understands_it_is_told_which_tools_this_host_can_run() -> None:
    lane = ToolsLane()
    runner = hj.HostJobRunner(render=lambda *a, **k: kept(), gate=lambda kind: tools.block_reason(kind, {"birefnet", "oneformer"}))
    runner.poll(proc_with(lane, tools_supported=True))
    assert lane.calls == [{"tools": ["sceneMasks", "subjectMask"]}]  # people needs SAM too, click select and depth need their own models


def test_an_editor_that_does_not_advertise_it_gets_the_plain_poll_as_before() -> None:
    lane = ToolsLane()
    hj.HostJobRunner(render=lambda *a, **k: kept()).poll(proc_with(lane, tools_supported=False))
    assert lane.calls == [{}]
    lane = ToolsLane()
    hj.HostJobRunner(render=lambda *a, **k: kept()).poll(proc(lane))  # no attribute at all (an older bridge)
    assert lane.calls == [{}]


def test_no_models_means_an_empty_list_which_the_editor_reads_as_none_available() -> None:
    lane = ToolsLane()
    hj.HostJobRunner(render=lambda *a, **k: kept(), gate=lambda kind: tools.block_reason(kind, ())).poll(proc_with(lane, tools_supported=True))
    assert lane.calls == [{"tools": []}]


def test_a_pending_job_still_arrives_and_runs_when_the_poll_carries_the_list() -> None:
    lane = ToolsLane({"id": 4, "kind": hj.KIND_SUPER_RESOLUTION})
    runner = hj.HostJobRunner(render=lambda *a, **k: kept(), spawn_thread=lambda target: target(), gate=lambda kind: tools.block_reason(kind, ()))
    assert runner.poll(proc_with(lane, tools_supported=True)) is True
    assert lane.of("claim") == [4] and lane.of("finish")[0]["ok"] is True


def test_the_kinds_are_the_strings_the_editor_sends() -> None:
    assert {"subjectMask", "sceneMasks", "peopleMasks"} <= set(tools.EDITOR_KINDS)  # confirmed against photocraft's camera_raw_host_job_ui.rs
