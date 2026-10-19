from __future__ import annotations

import hashlib
import json

import numpy as np
import pytest
from PIL import Image

from image_triage import upscale_worker as worker

core = worker.core  # the worker's own copy of upscale_core (sibling import)


def nearest(scale):
    return lambda t: np.repeat(np.repeat(t, scale, axis=2), scale, axis=3)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    blob = b"fake-onnx-weights"
    spec = core.UpscaleModelSpec("fake-x2", "Fake x2", 2, "fake.onnx", len(blob), hashlib.sha256(blob).hexdigest())
    monkeypatch.setitem(core.MODEL_SPECS, "fake-x2", spec)
    models = tmp_path / "models"
    models.mkdir()
    (models / "fake.onnx").write_bytes(blob)
    src = tmp_path / "in.png"
    Image.fromarray(np.random.default_rng(0).integers(0, 256, (20, 30, 3), dtype=np.uint8)).save(src)
    job = {
        "protocolVersion": worker.UPSCALE_PROTOCOL_VERSION,
        "inputPath": str(src),
        "outputPath": str(tmp_path / "out.png"),
        "modelKey": "fake-x2",
        "modelDir": str(models),
        "device": "cpu",
        "overwrite": False,
    }
    factory = lambda path, device, emit: worker._Session(nearest(2), "FakeProvider")
    return job, factory, tmp_path


def run(job, factory):
    lines = []
    result = worker.run_job(job, session_factory=factory, emit=lines.append)
    return result, lines


def test_success_payload_progress_and_output(env):
    job, factory, tmp = env
    result, lines = run(job, factory)
    assert result["outputSize"] == [60, 40] and result["provider"] == "FakeProvider" and result["scale"] == 2
    assert set(result["timings"]) == {"load", "session", "inference", "save", "total"}
    progress = [json.loads(l[len(worker.PROGRESS_PREFIX):]) for l in lines if l.startswith(worker.PROGRESS_PREFIX)]
    assert progress[-1] == {"stage": "save", "done": 1, "total": 1}
    assert any(p["stage"] == "upscale" for p in progress)
    with Image.open(tmp / "out.png") as out:
        assert out.size == (60, 40)
        assert "fake-x2" in out.text["Software"]
    assert not list(tmp.glob("*.partial"))


@pytest.mark.parametrize(
    "edit, category",
    [
        ({"protocolVersion": 99}, "protocol_mismatch"),
        ({"modelKey": "nope"}, "unknown_model"),
        ({"inputPath": "missing.png"}, "bad_image"),
    ],
)
def test_job_errors(env, edit, category):
    job, factory, _ = env
    job.update(edit)
    with pytest.raises(core.UpscaleError) as exc:
        run(job, factory)
    assert exc.value.category == category


def test_missing_or_corrupt_model_is_refused(env):
    job, factory, tmp = env
    (tmp / "models" / "fake.onnx").write_bytes(b"fake-onnx-weighTS")  # same size, wrong hash
    with pytest.raises(core.UpscaleError) as exc:
        run(job, factory)
    assert exc.value.category == "model_unavailable"
    (tmp / "models" / "fake.onnx").unlink()
    with pytest.raises(core.UpscaleError) as exc:
        run(job, factory)
    assert exc.value.category == "model_unavailable"


def test_existing_output_is_refused_unless_overwrite(env):
    job, factory, tmp = env
    (tmp / "out.png").write_bytes(b"old")
    with pytest.raises(core.UpscaleError) as exc:
        run(job, factory)
    assert exc.value.category == "exists" and (tmp / "out.png").read_bytes() == b"old"
    job["overwrite"] = True
    run(job, factory)
    assert (tmp / "out.png").read_bytes() != b"old"


def test_oversized_image_fails_before_any_model_work(env, monkeypatch):
    job, _, _ = env
    monkeypatch.setattr(core, "MAX_PIXELS", 100)
    built = []

    def factory(*a, **k):
        built.append(1)
        return worker._Session(nearest(2), "x")

    with pytest.raises(core.UpscaleError) as exc:
        run(job, factory)
    assert exc.value.category == "too_large" and not built


def test_alpha_is_resized_not_modelled(env):
    job, factory, tmp = env
    rgba = np.zeros((20, 30, 4), np.uint8)
    rgba[..., 3] = 200
    Image.fromarray(rgba, "RGBA").save(tmp / "a.png")
    job["inputPath"] = str(tmp / "a.png")
    run(job, factory)
    with Image.open(tmp / "out.png") as out:
        assert out.mode == "RGBA" and out.getchannel("A").getextrema() == (200, 200)


def test_main_reports_one_error_line_and_exit_code(env, tmp_path, capsys):
    job, _, _ = env
    job["modelKey"] = "nope"
    path = tmp_path / "job.json"
    path.write_text(json.dumps(job))
    assert worker.main(["--job", str(path)]) == 1
    lines = [l for l in capsys.readouterr().out.splitlines() if l]
    errors = [l for l in lines if l.startswith(worker.ERROR_PREFIX)]
    assert len(errors) == 1 and json.loads(errors[0][len(worker.ERROR_PREFIX):])["category"] == "unknown_model"
    assert not any(l.startswith(worker.RESULT_PREFIX) for l in lines)


def test_unexpected_exception_is_worker_failed(env, tmp_path, capsys):
    job, _, _ = env

    def boom(path, device, emit):
        raise RuntimeError("kaboom")

    path = tmp_path / "job.json"
    path.write_text(json.dumps(job))
    original = worker.default_session_factory
    worker.default_session_factory = boom
    try:
        assert worker.main(["--job", str(path)]) == 1
    finally:
        worker.default_session_factory = original
    out = capsys.readouterr().out
    assert "worker_failed" in out and "kaboom" in out


def test_provider_fallback_when_self_check_disagrees(monkeypatch, tmp_path):
    """default_session_factory must skip a provider whose output disagrees with the CPU."""
    import types

    cpu = lambda t: np.asarray(t) * 2.0
    garbage = lambda t: np.asarray(t) * 2.0 + 0.5

    fake_ort = types.SimpleNamespace(get_available_providers=lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"])
    monkeypatch.setitem(__import__("sys").modules, "onnxruntime", fake_ort)
    monkeypatch.setattr(worker, "_make_ort_session", lambda path, provider: (garbage, provider) if provider.startswith("CUDA") else (cpu, provider))
    lines = []
    session = worker.default_session_factory(tmp_path / "m.onnx", "auto", lines.append)
    assert session.provider == "CPUExecutionProvider"
    assert any("disagreed" in l for l in lines)
    monkeypatch.setattr(worker, "_make_ort_session", lambda path, provider: (cpu, provider))
    assert worker.default_session_factory(tmp_path / "m.onnx", "auto", lines.append).provider == "CUDAExecutionProvider"


def test_gpu_libraries_are_preloaded_before_any_session_unless_the_cpu_was_asked_for(monkeypatch, tmp_path):
    import types

    order = []
    cpu = lambda t: np.asarray(t) * 2.0
    fake_ort = types.SimpleNamespace(
        get_available_providers=lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"], preload_dlls=lambda: order.append("preload")
    )
    monkeypatch.setitem(__import__("sys").modules, "onnxruntime", fake_ort)
    monkeypatch.setattr(worker, "_make_ort_session", lambda path, provider: (order.append(provider), (cpu, provider))[1])
    worker.default_session_factory(tmp_path / "m.onnx", "auto", lambda line: None)
    assert order[0] == "preload"
    order.clear()
    worker.default_session_factory(tmp_path / "m.onnx", "cpu", lambda line: None)
    assert "preload" not in order


def test_preload_failure_or_absence_is_harmless(monkeypatch, tmp_path):
    import types

    cpu = lambda t: np.asarray(t) * 2.0
    for ort_like in (
        types.SimpleNamespace(get_available_providers=lambda: ["CPUExecutionProvider"]),  # an onnxruntime without preload_dlls
        types.SimpleNamespace(get_available_providers=lambda: ["CPUExecutionProvider"], preload_dlls=lambda: 1 / 0),
    ):
        monkeypatch.setitem(__import__("sys").modules, "onnxruntime", ort_like)
        monkeypatch.setattr(worker, "_make_ort_session", lambda path, provider: (cpu, provider))
        assert worker.default_session_factory(tmp_path / "m.onnx", "auto", lambda line: None).provider == "CPUExecutionProvider"


def test_a_provider_that_silently_fell_back_to_the_cpu_is_not_reported_as_that_provider(monkeypatch, tmp_path):
    """onnxruntime does not raise when CUDA cannot load (missing cublas DLL): it runs on the CPU. The worker used to label such a run CUDA."""
    import types

    cpu = lambda t: np.asarray(t) * 2.0
    fake_ort = types.SimpleNamespace(get_available_providers=lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"])
    monkeypatch.setitem(__import__("sys").modules, "onnxruntime", fake_ort)
    # the "CUDA" session reports that it is really running on the CPU, with identical numbers
    monkeypatch.setattr(worker, "_make_ort_session", lambda path, provider: (cpu, "CPUExecutionProvider"))
    lines = []
    session = worker.default_session_factory(tmp_path / "m.onnx", "auto", lines.append)
    assert session.provider == "CPUExecutionProvider"
    assert any("fell back to CPUExecutionProvider" in w for w in session.warnings)
    assert any("could not be loaded" in line for line in lines)
