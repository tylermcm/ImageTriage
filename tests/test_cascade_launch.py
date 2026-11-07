"""Whether the rankers run for an Analyze (GPU: yes; CPU: only when switched on; no runtime: skipped with a note), and the settings switch for it."""

from __future__ import annotations

import sys

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from image_triage.cascade.launch import CPU_SECONDS_PER_FRAME_FAST, CPU_SECONDS_PER_FRAME_LAPTOP, decide_ranking, default_scorer_factory
from image_triage.models import DeleteMode, WinnerMode
from tests.test_cascade_run import save, scene


def test_a_gpu_runs_the_rankers_without_asking():
    for device in ("cuda", "cuda:1", "gpu"):
        decision = decide_ranking(device, False)
        assert decision.use_rankers and decision.use_blink and decision.device == "gpu" and not decision.ask_cpu_confirmation and decision.note == ""


def test_a_cpu_skips_ranking_unless_it_was_switched_on():
    off = decide_ranking("cpu", False)
    assert not off.use_rankers and off.use_blink and "no usable GPU" in off.note
    on = decide_ranking("cpu", True)
    assert on.use_rankers and on.device == "cpu" and on.ask_cpu_confirmation
    # The prompt gives the measured range (fast workstation to laptop), never one optimistic number.
    assert f"{CPU_SECONDS_PER_FRAME_FAST} seconds per frame" in on.confirmation_text and f"{CPU_SECONDS_PER_FRAME_LAPTOP} seconds" in on.confirmation_text
    assert "minute" in on.confirmation_text and "hours" in on.confirmation_text


def test_no_runtime_skips_ranking_with_a_note_that_says_why():
    decision = decide_ranking(None, True)
    assert not decision.use_rankers and not decision.use_blink and "not installed" in decision.note


def test_declining_the_cpu_prompt_drops_ranking_but_keeps_the_cleanup():
    declined = decide_ranking("cpu", True).without_ranking("Ranking skipped: you chose not to rank on the CPU.")
    assert not declined.use_rankers and not declined.ask_cpu_confirmation and "chose not" in declined.note


def test_either_ranker_can_be_left_out():
    both = decide_ranking("cuda", False)
    assert both.use_quality and both.use_appeal
    quality_only = decide_ranking("cuda", False, quality_installed=True, appeal_installed=False)
    assert quality_only.use_rankers and quality_only.use_quality and not quality_only.use_appeal
    appeal_only = decide_ranking("cuda", False, quality_installed=False, appeal_installed=True)
    assert appeal_only.use_rankers and appeal_only.use_appeal and not appeal_only.use_quality
    neither = decide_ranking("cuda", False, quality_installed=False, appeal_installed=False)
    assert not neither.use_rankers and neither.use_blink and "not installed" in neither.note
    # The CPU prompt only appears when there is something to run.
    assert not decide_ranking("cpu", True, quality_installed=False, appeal_installed=False).ask_cpu_confirmation


def test_the_factory_builds_only_the_rankers_asked_for():
    for quality, appeal, names in ((True, False, {"maniqa"}), (False, True, {"q-realign aesthetic"}), (False, False, set())):
        scorers = default_scorer_factory("gpu", quality=quality, appeal=appeal)(lambda: False, lambda *_: None)
        try:
            assert set(scorers.names) == names
            assert (scorers.maniqa is not None) is quality and (scorers.aesthetic is not None) is appeal
        finally:
            scorers.close()




def test_building_the_rankers_never_imports_torch():
    before = {name for name in sys.modules if name.split(".")[0] in {"torch", "transformers", "timm"}}
    scorers = default_scorer_factory("gpu")(lambda: False, lambda *_: None)
    try:
        assert scorers.maniqa is not None and scorers.aesthetic is not None and set(scorers.names) == {"maniqa", "q-realign aesthetic"}
    finally:
        scorers.close()
    assert {name for name in sys.modules if name.split(".")[0] in {"torch", "transformers", "timm"}} == before


def _ensure_app() -> QApplication:
    return QApplication.instance() or QApplication([])






# -- the controller ------------------------------------------------------------------------------------------------------------------------


@pytest.fixture
def one_scene_folder(tmp_path):
    save(tmp_path / "a.jpg", scene(3), 0)
    return tmp_path


import image_triage.ai_model_store as _model_store

_REAL_BUNDLE_STATUS = _model_store.bundle_status




def mark_rankers_installed(monkeypatch, *keys: str) -> None:
    """The two rankers have their files (all of them by default); everything else asks the real model store."""
    import image_triage.ai_model_store as store

    ready = keys or ("maniqa", "qrealign")
    real = _REAL_BUNDLE_STATUS

    class _Ready:
        is_ready = True

    monkeypatch.setattr(store, "bundle_status", lambda key: _Ready() if key in ready else real(key))
