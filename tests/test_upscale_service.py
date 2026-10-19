from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from image_triage import upscale_core as core
from image_triage import upscale_service as svc
from image_triage.ai_env import AIRuntimeUnavailable


class FakeProcess:
    def __init__(self, lines, code=0, block=None):
        self._lines = list(lines)
        self._code = code
        self._block = block
        self.terminated = False
        self.stdout = self
        self._i = 0

    def readline(self):
        if self._block is not None and self._i >= len(self._lines):
            self._block.wait(2)
            return ""
        if self._i < len(self._lines):
            self._i += 1
            return self._lines[self._i - 1] + "\n"
        return ""

    def poll(self):
        return None if (self._block is not None and not self.terminated and not self._block.is_set()) else self._code

    def terminate(self):
        self.terminated = True
        if self._block is not None:
            self._block.set()

    def wait(self, timeout=None):
        return self._code

    def kill(self):
        self.terminated = True


RESULT = {
    "outputPath": "o.png",
    "modelKey": "realesrgan-x2plus",
    "scale": 2,
    "provider": "CPUExecutionProvider",
    "inputSize": [10, 10],
    "outputSize": [20, 20],
    "warnings": ["w"],
    "timings": {"total": 1.0},
}


@pytest.fixture()
def request_(tmp_path):
    src = tmp_path / "a.png"
    Image.new("RGB", (10, 10)).save(src)
    return svc.UpscaleRequest(input_path=src, output_path=tmp_path / "o.png", model_dir=tmp_path)


def resolver(*a, **k):
    return SimpleNamespace(profile_id="cpu", device="cpu", site_packages=(Path("."),), primary_site_packages=Path("."))


def build(job_path):
    return ["python", "worker.py", "--job", str(job_path)]


def run(request, process, progress=None, cancel=None, spawned=None):
    def spawn(command, env):
        if spawned is not None:
            spawned.append((command, env))
        return process

    return svc.run_upscale(request, progress, cancel, spawn=spawn, runtime_resolver=resolver, command_builder=build)


def test_success_parses_progress_and_result(request_):
    lines = ["noise", 'PROGRESS {"stage": "upscale", "done": 1, "total": 4}', "RESULT " + json.dumps(RESULT)]
    seen, spawned = [], []
    result = run(request_, FakeProcess(lines), seen.append, spawned=spawned)
    assert result.scale == 2 and result.output_size == (20, 20) and result.warnings == ("w",)
    assert seen == [svc.UpscaleProgress("upscale", 1, 4)] and seen[0].fraction == 0.25
    command, env = spawned[0]
    assert env["IMAGE_TRIAGE_AI_PROTOCOL"] == str(svc.UPSCALE_PROTOCOL_VERSION)
    job_file = Path(command[-1])
    assert not job_file.exists()  # temp job removed


def test_job_file_contents(request_):
    captured = {}

    def spawn(command, env):
        captured.update(json.loads(Path(command[-1]).read_text()))
        return FakeProcess(["RESULT " + json.dumps(RESULT)])

    svc.run_upscale(request_, spawn=spawn, runtime_resolver=resolver, command_builder=build)
    assert captured["modelKey"] == "realesrgan-x2plus" and captured["protocolVersion"] == svc.UPSCALE_PROTOCOL_VERSION
    assert captured["outputPath"].endswith("o.png") and captured["overwrite"] is False


def test_error_line_is_mapped(request_):
    err = {"category": "too_large", "message": "big", "remediation": "crop"}
    with pytest.raises(svc.UpscaleServiceError) as exc:
        run(request_, FakeProcess(["ERROR " + json.dumps(err)], code=1))
    assert (exc.value.category, exc.value.remediation) == ("too_large", "crop")


def test_nonzero_exit_without_error_includes_tail(request_):
    with pytest.raises(svc.UpscaleServiceError) as exc:
        run(request_, FakeProcess(["Traceback...", "boom"], code=3))
    assert exc.value.category == "worker_failed" and "boom" in str(exc.value)


def test_zero_exit_without_result_is_failure(request_):
    with pytest.raises(svc.UpscaleServiceError) as exc:
        run(request_, FakeProcess(["hello"], code=0))
    assert exc.value.category == "worker_failed"


def test_runtime_unavailable_is_mapped(request_):
    def bad(*a, **k):
        raise AIRuntimeUnavailable("not installed", category="runtime_missing", remediation="Set up")

    with pytest.raises(svc.UpscaleServiceError) as exc:
        svc.run_upscale(request_, spawn=lambda c, e: None, runtime_resolver=bad, command_builder=build)
    assert (exc.value.category, exc.value.remediation) == ("runtime_missing", "Set up")


def test_missing_worker_script_is_mapped(request_):
    def missing(path):
        raise FileNotFoundError("worker")

    with pytest.raises(svc.UpscaleServiceError) as exc:
        svc.run_upscale(request_, spawn=lambda c, e: None, runtime_resolver=resolver, command_builder=missing)
    assert exc.value.category == "worker_missing"


def test_unknown_model_and_missing_input(request_, tmp_path):
    with pytest.raises(svc.UpscaleServiceError) as exc:
        run(svc.UpscaleRequest(input_path=request_.input_path, model_key="nope"), FakeProcess([]))
    assert exc.value.category == "unknown_model"
    with pytest.raises(svc.UpscaleServiceError) as exc:
        run(svc.UpscaleRequest(input_path=tmp_path / "missing.png"), FakeProcess([]))
    assert exc.value.category == "bad_image"


def test_cancel_terminates_process_and_removes_partial(request_, tmp_path):
    gate = threading.Event()
    process = FakeProcess(['PROGRESS {"stage": "upscale", "done": 1, "total": 9}'], block=gate)
    partial = tmp_path / "o.png.partial"
    partial.write_bytes(b"half")
    cancel = threading.Event()
    threading.Timer(0.2, cancel.set).start()
    with pytest.raises(svc.UpscaleServiceError) as exc:
        run(request_, process, cancel=cancel)
    assert exc.value.category == "cancelled" and process.terminated and not partial.exists()
    assert cancel.is_set()  # the caller's event is left as the caller set it


def test_cancel_event_is_not_set_by_a_normal_finish(request_):
    cancel = threading.Event()
    run(request_, FakeProcess(["RESULT " + json.dumps(RESULT)]), cancel=cancel)
    time.sleep(0.15)
    assert not cancel.is_set()


def test_availability(tmp_path):
    def ok(*a, **k):
        return None

    def bad(*a, **k):
        raise AIRuntimeUnavailable("none", remediation="Run Set Up AI.")

    a = svc.check_availability(tmp_path, runtime_resolver=ok)
    assert a.runtime_ok and not a.available and set(a.models) == set(core.MODEL_SPECS)
    b = svc.check_availability(tmp_path, runtime_resolver=bad)
    assert not b.runtime_ok and "Set Up AI" in b.runtime_reason
    spec = core.MODEL_SPECS["realesrgan-x2plus"]
    (tmp_path / spec.filename).write_bytes(b"\0" * spec.size)
    c = svc.check_availability(tmp_path, runtime_resolver=ok)
    assert c.available and c.models["realesrgan-x2plus"] == "" and c.models["realesrgan-x4plus"]


def test_model_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv(svc.MODEL_DIR_ENV, str(tmp_path))
    assert svc.upscale_model_dir() == tmp_path


def test_upscale_capability_is_registered_but_not_part_of_set_up_ai():
    from image_triage import ai_manifest

    capability = ai_manifest.CAPABILITIES["upscale"]
    assert capability.requires_onnx and not capability.model_bundles
    assert capability.worker_module == "image_triage.upscale_worker"
    # Set Up AI must not try to install a feature whose weights are not hosted yet.
    assert "upscale" not in ai_manifest.BASE_CAPABILITIES
    assert "upscale" not in ai_manifest.DEFAULT_CAPABILITY_ORDER
    assert "upscale" not in ai_manifest.setup_capabilities(include_torch=True)
