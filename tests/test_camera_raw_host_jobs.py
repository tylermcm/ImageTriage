from __future__ import annotations

from types import SimpleNamespace

from image_triage import camera_raw_host_jobs as hj
from image_triage import full_render as fr
from image_triage import upscale_service as svc


class Lane:
    def __init__(self, pending=None, cancel_after=None):
        self.pending = pending
        self.calls = []
        self.cancel_after = cancel_after

    def call(self, method, params=None):
        assert method == "ui.hostJob"
        self.calls.append(params)
        if not params:
            return {"pending": self.pending}
        if "progress" in params:
            n = sum(1 for c in self.calls if c and "progress" in c)
            return {"cancel": self.cancel_after is not None and n >= self.cancel_after}
        return {}

    def of(self, key):
        return [c[key] for c in self.calls if c and key in c]


def proc(lane, source="C:/photos/a.nef", supported=True):
    return SimpleNamespace(control=SimpleNamespace(camera_raw_host_job_supported=supported), fast_lane=lambda: lane, source_path=source)


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 1.0  # every look at the clock is a second later, so every report is due
        return self.t


def sync(target):
    target()


def kept(path="C:/photos/.edits/a.nef.superres.png"):
    return fr.RenderResult(fr.RENDERED, path=path, format="png", width=20, height=10, depth=8, temporary=False)


def runner(render, **kw):
    return hj.HostJobRunner(render=render, clock=Clock(), spawn_thread=sync, **kw)


def pending(job_id=7, kind=hj.KIND_SUPER_RESOLUTION):
    return {"id": job_id, "kind": kind}


def test_polling_always_checks_in_but_starts_nothing_without_a_request():
    lane = Lane()
    assert runner(lambda *a, **k: kept()).poll(proc(lane)) is False and lane.calls == [{}]
    lane = Lane(pending(kind="somethingElse"))
    assert runner(lambda *a, **k: kept()).poll(proc(lane)) is False and not lane.of("claim")
    lane = Lane(pending())
    assert runner(lambda *a, **k: kept()).poll(proc(lane, supported=False)) is False and lane.calls == []


def test_a_request_is_claimed_rendered_with_progress_and_finished():
    seen = {}

    def render(source, progress=None, cancel=None):
        seen["source"] = source
        for done in (1, 2, 4):
            progress(svc.UpscaleProgress("upscale", done, 4))
        progress(svc.UpscaleProgress("save", 1, 1))
        return kept()

    lane = Lane(pending())
    r = runner(render)
    assert r.poll(proc(lane, source="C:/photos/a.nef")) is True
    assert seen["source"] == "C:/photos/a.nef" and lane.of("claim") == [7]
    fractions = [p["fraction"] for p in lane.of("progress")]
    assert fractions[0] == 0.0 and fractions == sorted(fractions) and fractions[-1] == 0.99
    assert any(p.get("etaSeconds") is not None for p in lane.of("progress"))
    [finish] = lane.of("finish")
    assert finish["id"] == 7 and finish["ok"] is True
    assert not r.busy


def test_a_render_that_could_not_be_kept_is_still_reported_as_applied_and_released(tmp_path):
    work = tmp_path / "w.png"
    work.write_bytes(b"x")
    temp = fr.RenderResult(fr.RENDERED, path=work, format="png", width=2, height=2, depth=8, temporary=True)
    lane = Lane(pending())
    runner(lambda *a, **k: temp).poll(proc(lane))
    [finish] = lane.of("finish")
    assert finish["ok"] is True and "could not be kept" in finish["message"] and not work.exists()


def test_cancel_in_a_progress_reply_stops_the_job_quietly():
    def render(source, progress=None, cancel=None):
        for done in range(1, 50):
            progress(svc.UpscaleProgress("upscale", done, 50))
            if cancel.is_set():
                return fr.failed("cancelled")
        return kept()

    lane = Lane(pending(), cancel_after=2)
    r = runner(render)
    r.poll(proc(lane))
    [finish] = lane.of("finish")
    assert finish["ok"] is False and finish["message"] == "Cancelled" and not r.busy


def test_failures_are_reported_to_the_popup():
    def explode(*a, **k):
        raise RuntimeError("kaboom")

    for render, expect in [
        (lambda *a, **k: fr.failed("Super Resolution could not run: model missing"), "model missing"),
        (explode, "kaboom"),
    ]:
        lane = Lane(pending())
        r = runner(render)
        r.poll(proc(lane))
        [finish] = lane.of("finish")
        assert finish["ok"] is False and expect in finish["message"] and not r.busy


def test_no_open_photo_is_reported_and_one_job_runs_at_a_time():
    lane = Lane(pending())
    assert runner(lambda *a, **k: kept()).poll(proc(lane, source="")) is False
    assert lane.of("finish")[0]["ok"] is False

    started = []
    r = hj.HostJobRunner(render=lambda *a, **k: kept(), clock=Clock(), spawn_thread=lambda target: started.append(target))
    assert r.poll(proc(Lane(pending()))) is True and r.busy
    second = Lane(pending(8))
    assert r.poll(proc(second)) is False and not second.of("claim")  # it checked in, and left the request waiting


def test_a_broken_editor_never_raises_from_poll():
    class Broken:
        def call(self, *a, **k):
            raise OSError("connection closed")

    assert runner(lambda *a, **k: kept()).poll(proc(Broken())) is False
