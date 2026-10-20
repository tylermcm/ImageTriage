"""The Windows prerequisite hint: an old Visual C++ runtime makes PyTorch and ONNX Runtime fail with an unhelpful DLL error."""

from __future__ import annotations

import pytest

from image_triage import ai_platform
from image_triage.cascade.rankers.client import _worker_failure
from image_triage.cascade.rankers.spec import MANIQA

REAL_MESSAGE = ('OSError: [WinError 1114] A dynamic link library (DLL) initialization routine failed. Error loading '
                '"C:/x/torch/lib/c10.dll" or one of its dependencies.')


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(ai_platform, "is_windows", lambda: True)


def test_the_real_failure_messages_are_recognised():
    for message in (REAL_MESSAGE, "DLL load failed while importing onnxruntime_pybind11_state: A dynamic link library initialization routine failed.",
                    "OSError: [WinError 126] The specified module could not be found"):
        assert ai_platform.looks_like_dll_failure(message)
    assert not ai_platform.looks_like_dll_failure("ValueError: bad image")
    assert not ai_platform.looks_like_dll_failure("")


def test_an_old_runtime_is_named_with_its_version_and_the_download(windows, monkeypatch):
    monkeypatch.setattr(ai_platform, "vc_runtime_version", lambda: (14, 36, 32532))
    hint = ai_platform.dll_failure_hint(REAL_MESSAGE)
    assert "14.36.32532" in hint and "14.40 or newer" in hint and ai_platform.VC_REDIST_URL in hint


def test_a_current_runtime_still_gets_a_repair_suggestion(windows, monkeypatch):
    monkeypatch.setattr(ai_platform, "vc_runtime_version", lambda: (14, 44, 35112))
    hint = ai_platform.dll_failure_hint(REAL_MESSAGE)
    assert "14.4" not in hint and "repairing" in hint and ai_platform.VC_REDIST_URL in hint


def test_an_unknown_version_is_not_guessed_at(windows, monkeypatch):
    monkeypatch.setattr(ai_platform, "vc_runtime_version", lambda: None)
    assert "repairing" in ai_platform.dll_failure_hint(REAL_MESSAGE)


def test_nothing_is_said_off_windows_or_for_other_errors(monkeypatch):
    monkeypatch.setattr(ai_platform, "is_windows", lambda: False)
    assert ai_platform.dll_failure_hint(REAL_MESSAGE) == "" and ai_platform.vc_runtime_version() is None
    monkeypatch.setattr(ai_platform, "is_windows", lambda: True)
    assert ai_platform.dll_failure_hint("ValueError: bad image") == ""


def test_this_machines_version_reads_as_three_numbers_or_none():
    version = ai_platform.vc_runtime_version()
    assert version is None or (len(version) == 3 and all(isinstance(part, int) for part in version))


def test_a_ranker_worker_failure_carries_the_hint(windows, monkeypatch):
    monkeypatch.setattr(ai_platform, "vc_runtime_version", lambda: (14, 36, 32532))
    error = _worker_failure(MANIQA, {"category": "worker_failed", "message": REAL_MESSAGE})
    assert "14.36.32532" in str(error) and ai_platform.VC_REDIST_URL in error.remediation


def test_other_worker_failures_are_unchanged(windows):
    error = _worker_failure(MANIQA, {"category": "worker_failed", "message": "ValueError: boom"})
    assert str(error) == "MANIQA failed: ValueError: boom" and error.remediation == ""


def test_the_runtime_install_message_carries_the_hint(windows, monkeypatch, tmp_path):
    import subprocess

    from image_triage import ai_runtime_packages as rp

    monkeypatch.setattr(ai_platform, "vc_runtime_version", lambda: (14, 36, 32532))
    done = subprocess.CompletedProcess([], 1, stdout="", stderr="Traceback...\n" + REAL_MESSAGE)
    monkeypatch.setattr(rp.subprocess, "run", lambda *a, **k: done)
    (tmp_path / "site-packages").mkdir()
    with pytest.raises(RuntimeError) as raised:
        rp._validate_profile_in_subprocess(tmp_path / "site-packages", "cpu", False)
    assert "14.36.32532" in str(raised.value) and "The existing runtime was not changed." in str(raised.value)
