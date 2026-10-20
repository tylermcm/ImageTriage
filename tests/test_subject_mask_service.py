from __future__ import annotations

import threading
from pathlib import Path

import pytest

from image_triage import subject_mask_service as svc
from image_triage.ai_env import AIRuntimeUnavailable


class Process:
    def __init__(self, lines, code=0):
        self.stdout = iter(line + "\n" for line in lines)
        self.code = code
        self.terminated = False

    def poll(self):
        return self.code

    def wait(self, timeout=None):
        return self.code

    def terminate(self):
        self.terminated = True


def run(tmp_path, lines, code=0, write_output=True, cancel=None, progress=None, resolver=lambda *a, **k: object()):
    source = tmp_path / "in.png"
    source.write_bytes(b"png")
    output = tmp_path / "out.png"
    if write_output:
        output.write_bytes(b"mask")
    return svc.run_subject_mask(
        source,
        output,
        progress,
        cancel,
        spawn=lambda command, env: Process(lines, code),
        runtime_resolver=resolver,
        command_builder=lambda job: ["python", "worker.py", "--job", str(job)],
        model_dir=tmp_path,
    ), output


def test_a_good_run_reports_progress_and_returns_the_map(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "build_worker_env", lambda selection, **k: {})
    steps = []
    result, output = run(
        tmp_path,
        ["noise", "PROGRESS Loading subject model...", 'PROGRESS Finding subject...', 'RESULT {"width": 900, "height": 600, "device": "cuda"}'],
        progress=steps.append,
    )
    assert steps == ["Loading subject model...", "Finding subject..."]
    assert (result.output_path, result.width, result.height, result.device) == (output, 900, 600, "cuda")


def test_errors_and_missing_output_are_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "build_worker_env", lambda selection, **k: {})
    with pytest.raises(svc.SubjectMaskError) as raised:
        run(tmp_path, ['ERROR {"category": "worker_failed", "message": "boom", "remediation": "retry"}'], code=1)
    assert (raised.value.category, str(raised.value), raised.value.remediation) == ("worker_failed", "boom", "retry")
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    with pytest.raises(svc.SubjectMaskError, match="exited unexpectedly"):
        run(fresh, ['RESULT {"width": 1, "height": 1, "device": "cpu"}'], write_output=False)


def test_a_cancel_stops_the_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "build_worker_env", lambda selection, **k: {})
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(svc.SubjectMaskError) as raised:
        run(tmp_path, ["PROGRESS Finding subject..."], cancel=cancel)
    assert raised.value.category == "cancelled"


def test_no_runtime_is_a_clear_error(tmp_path):
    def none(*a, **k):
        raise AIRuntimeUnavailable("No AI runtime.", category="runtime_missing", remediation="Run Set Up AI.")

    with pytest.raises(svc.SubjectMaskError) as raised:
        run(tmp_path, [], resolver=none)
    assert raised.value.remediation == "Run Set Up AI."


def test_a_missing_picture_is_refused(tmp_path):
    with pytest.raises(svc.SubjectMaskError, match="not found"):
        svc.run_subject_mask(tmp_path / "nope.png", tmp_path / "out.png", model_dir=tmp_path)
