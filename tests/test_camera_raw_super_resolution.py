from __future__ import annotations

from pathlib import Path

import pytest

from image_triage import camera_raw_super_resolution as sr
from image_triage import upscale_service as svc
from image_triage.full_render import FAILED, NO_EDITS, RENDERED, RenderResult


def render(tmp_path, *, depth=8, fmt="png", temporary=True):
    path = tmp_path / ("r." + ("tif" if fmt == "tiff" else "png"))
    path.write_bytes(b"x")
    return RenderResult(RENDERED, path=path, format=fmt, width=100, height=50, depth=depth, icc=True, temporary=temporary)


def ok_availability():
    return svc.UpscaleAvailability(True, "", {sr.SUPER_RESOLUTION_MODEL: ""})


def fake_run(calls=None, size=(200, 100)):
    def run(request, progress=None, cancel=None):
        if calls is not None:
            calls.append(request)
        request.output_path.write_bytes(b"up")
        return svc.UpscaleResult(request.output_path, request.model_key, 2, "CPUExecutionProvider", (100, 50), size)

    return run


def test_wants_super_resolution():
    assert sr.wants_super_resolution({"version": 1, "cameraRaw": {"superResolution": True}})
    assert not sr.wants_super_resolution({"version": 1, "cameraRaw": {"superResolution": False}})
    assert not sr.wants_super_resolution({"cameraRaw": {"superResolution": 1}})
    assert not sr.wants_super_resolution(None)
    assert not sr.wants_super_resolution({"cameraRaw": []})


def test_successful_upsize_replaces_the_render_and_releases_the_original(tmp_path):
    original = render(tmp_path)
    calls = []
    result = sr.apply_super_resolution(original, run=fake_run(calls), availability=ok_availability)
    assert result.outcome == RENDERED and (result.width, result.height) == (200, 100) and result.depth == 8
    assert result.temporary and result.icc and result.path.name == "r_2x_ai.png" and result.path.read_bytes() == b"up"
    assert not original.path.exists()  # the un-upsized temp render is released
    assert calls[0].apply_orientation is False and calls[0].model_key == sr.SUPER_RESOLUTION_MODEL


def test_a_render_that_is_not_ours_to_delete_is_kept(tmp_path):
    original = render(tmp_path, temporary=False)
    result = sr.apply_super_resolution(original, run=fake_run(), availability=ok_availability)
    assert original.path.exists() and result.temporary


def test_tiff_stays_tiff(tmp_path):
    result = sr.apply_super_resolution(render(tmp_path, fmt="tiff"), run=fake_run(), availability=ok_availability)
    assert result.path.suffix == ".tif" and result.format == "tiff"


def test_passes_through_when_nothing_was_rendered():
    assert sr.apply_super_resolution(NO_EDITS) is NO_EDITS


def test_a_16_bit_render_is_reduced_to_8_bits_for_the_backend_and_the_result_is_8_bit(tmp_path):
    original = render(tmp_path, depth=16, fmt="tiff")
    calls = []
    result = sr.apply_super_resolution(
        original, run=fake_run(calls), availability=ok_availability, to_8bit=lambda src, dst: dst.write_bytes(b"8bit")
    )
    assert result.outcome == RENDERED and result.depth == 8 and (result.width, result.height) == (200, 100)
    assert calls[0].input_path.name == "r.8bit.png"  # the backend never saw the 16-bit file
    assert not original.path.exists() and not (tmp_path / "r.8bit.png").exists()  # both temporaries are gone


def test_a_failed_reduction_is_a_failure_and_leaves_nothing_behind(tmp_path):
    original = render(tmp_path, depth=16)

    def broken(src, dst):
        raise RuntimeError("cannot read the 16-bit picture")

    result = sr.apply_super_resolution(original, run=fake_run(), availability=ok_availability, to_8bit=broken)
    assert result.outcome == FAILED and "cannot read" in result.reason
    assert not original.path.exists()


@pytest.mark.parametrize(
    "state, expect",
    [
        (svc.UpscaleAvailability(False, "AI runtime is not installed", {sr.SUPER_RESOLUTION_MODEL: ""}), "not installed"),
        (svc.UpscaleAvailability(True, "", {sr.SUPER_RESOLUTION_MODEL: "Model file missing"}), "Model file missing"),
    ],
)
def test_unavailable_backend_is_a_failure(tmp_path, state, expect):
    original = render(tmp_path)

    def run(*a, **k):
        raise AssertionError("must not start a job")

    result = sr.apply_super_resolution(original, run=run, availability=lambda: state)
    assert result.outcome == FAILED and expect in result.reason and "could not run" in result.reason
    assert not original.path.exists()


def test_service_error_and_unexpected_error_are_failures(tmp_path):
    def too_big(*a, **k):
        raise svc.UpscaleServiceError("too_large", "result too big", "crop it")

    result = sr.apply_super_resolution(render(tmp_path), run=too_big, availability=ok_availability)
    assert result.outcome == FAILED and "result too big" in result.reason and "crop it" in result.reason

    def boom(*a, **k):
        raise RuntimeError("kaboom")

    result = sr.apply_super_resolution(render(tmp_path), run=boom, availability=ok_availability)
    assert result.outcome == FAILED and "kaboom" in result.reason


def test_cancel_is_reported_as_cancelled(tmp_path):
    def cancelled(*a, **k):
        raise svc.UpscaleServiceError("cancelled", "Upscaling was cancelled.")

    result = sr.apply_super_resolution(render(tmp_path), run=cancelled, availability=ok_availability)
    assert result.outcome == FAILED and result.reason == "Super Resolution was cancelled"
