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


class NoWarmer:
    ticks = 0

    def tick(self):
        self.ticks += 1


def runner(render, **kw):
    kw.setdefault("warmer", NoWarmer())  # never load real models from a test
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


# --- subject masks (BiRefNet) ----------------------------------------------------------------------------------------------------

def mask_pending(tmp_path, job_id=9, make_input=True):
    source = tmp_path / "in.png"
    if make_input:
        source.write_bytes(b"png")
    return {"id": job_id, "kind": hj.KIND_SUBJECT_MASK, "input": str(source), "output": str(tmp_path / "out.png")}


def test_a_subject_mask_request_is_claimed_run_and_finished(tmp_path):
    lane = Lane(mask_pending(tmp_path))
    seen = {}

    def fake(source, output, on_progress, cancel):
        seen.update(source=source, output=output)
        on_progress("Loading subject model...")
        on_progress("Finding subject...")

    assert runner(lambda *a, **k: kept(), subject_mask=fake).poll(proc(lane)) is True
    assert lane.of("claim") == [9]
    assert str(seen["source"]) == str(tmp_path / "in.png") and str(seen["output"]) == str(tmp_path / "out.png")
    assert lane.of("progress")[-1]["id"] == 9
    assert lane.of("finish") == [{"id": 9, "ok": True, "message": "Subject found."}]


def test_a_subject_mask_without_a_picture_fails_in_the_popup_and_runs_nothing(tmp_path):
    lane = Lane(mask_pending(tmp_path, make_input=False))

    def never(*a, **k):
        raise AssertionError("must not run")

    assert runner(lambda *a, **k: kept(), subject_mask=never).poll(proc(lane)) is False
    (finish,) = lane.of("finish")
    assert finish["ok"] is False and "picture" in finish["message"]


def test_a_failed_subject_mask_says_why_and_a_cancel_is_quiet(tmp_path):
    from image_triage import subject_mask_service as sm

    def broken(*a, **k):
        raise sm.SubjectMaskError("model_missing", "The subject model is not installed.", "Run Set Up AI.")

    lane = Lane(mask_pending(tmp_path))
    runner(lambda *a, **k: kept(), subject_mask=broken).poll(proc(lane))
    assert lane.of("finish") == [{"id": 9, "ok": False, "message": "The subject model is not installed. Run Set Up AI."}]

    def cancelled(*a, **k):
        raise sm.SubjectMaskError("cancelled", "Cancelled")

    lane = Lane(mask_pending(tmp_path))
    runner(lambda *a, **k: kept(), subject_mask=cancelled).poll(proc(lane))
    assert lane.of("finish")[0]["message"] == "Cancelled"


def test_scene_and_people_requests_run_their_own_function_and_say_done(tmp_path):
    for kind, name in ((hj.KIND_SCENE_MASKS, "scene"), (hj.KIND_PEOPLE_MASKS, "people")):
        source = tmp_path / "in.png"
        source.write_bytes(b"png")
        lane = Lane({"id": 4, "kind": kind, "input": str(source), "output": str(tmp_path / "manifest.json")})
        seen = []

        def fake(label):
            def run(src, out, on_progress, cancel):
                seen.append((label, str(out)))
                on_progress("Analyzing...")
            return run

        kw = {"scene_masks": fake("scene"), "people_masks": fake("people"), "subject_mask": fake("subject")}
        assert runner(lambda *a, **k: kept(), **kw).poll(proc(lane)) is True
        assert seen == [(name, str(tmp_path / "manifest.json"))]
        assert lane.of("finish") == [{"id": 4, "ok": True, "message": "Done."}]


def test_every_poll_ticks_the_model_warmer_even_with_nothing_to_do():
    warmer = NoWarmer()
    lane = Lane()
    r = hj.HostJobRunner(render=lambda *a, **k: kept(), clock=Clock(), spawn_thread=sync, warmer=warmer)
    r.poll(proc(lane))
    r.poll(proc(lane))
    assert warmer.ticks == 2
    r.poll(proc(lane, supported=False))
    assert warmer.ticks == 2, "an editor that cannot be asked for anything is not warmed for"
