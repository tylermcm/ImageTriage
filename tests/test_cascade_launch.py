"""Whether the rankers run for an Analyze (GPU: yes; CPU: only when switched on; no runtime: skipped with a note), and the settings switch for it."""

from __future__ import annotations

import sys

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from image_triage.cascade.launch import CPU_SECONDS_PER_FRAME_FAST, CPU_SECONDS_PER_FRAME_LAPTOP, decide_ranking, default_scorer_factory
from image_triage.models import DeleteMode, WinnerMode
from image_triage.settings_dialog import WorkflowSettingsDialog
from tests.harness import open_folder
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


def test_the_controller_passes_on_which_rankers_are_installed(main_window, monkeypatch):
    ctl = main_window._ai_run
    fake_runtime(main_window, monkeypatch, "cuda")
    decision = ctl.cascade_ranking_decision()
    assert decision.use_quality and decision.use_appeal
    mark_rankers_installed(monkeypatch, "qrealign")
    decision = ctl.cascade_ranking_decision()
    assert decision.use_rankers and decision.use_appeal and not decision.use_quality
    mark_rankers_installed(monkeypatch, "nothing")  # neither ranker has its files
    decision = ctl.cascade_ranking_decision()
    assert not decision.use_rankers and "Culling models are not installed" in decision.note


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


def test_the_settings_dialog_round_trips_the_cpu_ranking_switch():
    _ensure_app()
    dialog = WorkflowSettingsDialog(
        sessions=["Default"], current_session="Default", winner_mode=WinnerMode.COPY, delete_mode=DeleteMode.SAFE_TRASH, ai_cascade_rank_on_cpu=True
    )
    try:
        assert dialog.ai_cascade_rank_on_cpu_checkbox.isChecked() and dialog.result_settings().ai_cascade_rank_on_cpu
        dialog.ai_cascade_rank_on_cpu_checkbox.setChecked(False)
        assert not dialog.result_settings().ai_cascade_rank_on_cpu
    finally:
        dialog.deleteLater()


def test_the_default_is_off_so_a_cpu_machine_is_never_slowed_without_asking():
    _ensure_app()
    dialog = WorkflowSettingsDialog(sessions=["Default"], current_session="Default", winner_mode=WinnerMode.COPY, delete_mode=DeleteMode.SAFE_TRASH)
    try:
        assert not dialog.result_settings().ai_cascade_rank_on_cpu
    finally:
        dialog.deleteLater()


# -- the controller ------------------------------------------------------------------------------------------------------------------------


@pytest.fixture
def one_scene_folder(tmp_path):
    save(tmp_path / "a.jpg", scene(3), 0)
    return tmp_path


import image_triage.ai_model_store as _model_store

_REAL_BUNDLE_STATUS = _model_store.bundle_status


def fake_runtime(main_window, monkeypatch, device: str | None):
    class Runtime:
        def __init__(self, device):
            self.device = device

        def validate(self):
            if self.device is None:
                raise RuntimeError("not installed")

    monkeypatch.setattr(main_window._settings_ctl, "configured_aiculler_runtime", lambda **_k: Runtime(device))
    monkeypatch.setattr(main_window._ai_setup, "refresh_ai_runtime_preferences", lambda: None)
    mark_rankers_installed(monkeypatch)


def mark_rankers_installed(monkeypatch, *keys: str) -> None:
    """The two rankers have their files (all of them by default); everything else asks the real model store."""
    import image_triage.ai_model_store as store

    ready = keys or ("maniqa", "qrealign")
    real = _REAL_BUNDLE_STATUS

    class _Ready:
        is_ready = True

    monkeypatch.setattr(store, "bundle_status", lambda key: _Ready() if key in ready else real(key))


def test_the_controller_reads_the_runtime_device_and_the_setting(main_window, monkeypatch):
    ctl = main_window._ai_run
    fake_runtime(main_window, monkeypatch, "cuda")
    assert ctl.cascade_ranking_decision().use_rankers
    fake_runtime(main_window, monkeypatch, "cpu")
    main_window._settings.setValue(ctl.CASCADE_CPU_RANKING_KEY, False)
    assert not ctl.cascade_ranking_decision().use_rankers
    main_window._settings.setValue(ctl.CASCADE_CPU_RANKING_KEY, True)
    try:
        assert ctl.cascade_ranking_decision().ask_cpu_confirmation
        fake_runtime(main_window, monkeypatch, None)
        assert "not installed" in ctl.cascade_ranking_decision().note
    finally:
        main_window._settings.remove(ctl.CASCADE_CPU_RANKING_KEY)


def test_declining_the_cpu_prompt_runs_the_cleanup_without_rankers(main_window, one_scene_folder, monkeypatch):
    ctl = main_window._ai_run
    fake_runtime(main_window, monkeypatch, "cpu")
    main_window._settings.setValue(ctl.CASCADE_CPU_RANKING_KEY, True)
    asked: list[str] = []
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: asked.append(a[1]) or QMessageBox.StandardButton.No))
    started: list[dict] = []
    built: list[dict] = []
    import image_triage.cascade.launch as launch_module

    monkeypatch.setattr(launch_module, "default_scorer_factory", lambda device, **kw: built.append({"device": device, **kw}) or (lambda *a: None))
    monkeypatch.setattr(ctl, "_start_ai_task", lambda task, start, logger, **kw: started.append({"factory": task.scorer_factory, "note": task.ranking_note, **kw}))
    open_folder(main_window, one_scene_folder, 1)
    try:
        ctl.run_ai_pipeline()
    finally:
        main_window._settings.remove(ctl.CASCADE_CPU_RANKING_KEY)
    assert asked == ["Rank on the CPU?"]
    assert started and "chose not" in started[0]["note"] and started[0]["backend"] == "cascade"
    assert built == [{"device": "cpu", "rankers": False, "blink": True, "quality": True, "appeal": True}]  # ranking declined, but the cheap eyes-closed check still runs


def test_cancelling_the_cpu_prompt_starts_nothing(main_window, one_scene_folder, monkeypatch):
    ctl = main_window._ai_run
    fake_runtime(main_window, monkeypatch, "cpu")
    main_window._settings.setValue(ctl.CASCADE_CPU_RANKING_KEY, True)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Cancel))
    started: list[object] = []
    monkeypatch.setattr(ctl, "_start_ai_task", lambda *a, **k: started.append(1))
    open_folder(main_window, one_scene_folder, 1)
    try:
        ctl.run_ai_pipeline()
    finally:
        main_window._settings.remove(ctl.CASCADE_CPU_RANKING_KEY)
    assert started == [] and main_window._active_ai_task is None


def test_a_gpu_starts_the_cascade_with_rankers_and_the_legacy_setting_restores_the_old_engine(main_window, one_scene_folder, monkeypatch):
    ctl = main_window._ai_run
    fake_runtime(main_window, monkeypatch, "cuda")
    started: list[dict] = []
    monkeypatch.setattr(ctl, "_start_ai_task", lambda task, start, logger, **kw: started.append({"factory": task.scorer_factory, **kw}))
    open_folder(main_window, one_scene_folder, 1)
    ctl.run_ai_pipeline()
    assert started and started[0]["factory"] is not None and started[0]["stage_total"] == 4

    legacy: list[str] = []
    monkeypatch.setattr(ctl, "use_legacy_culler", lambda: True)
    monkeypatch.setattr(ctl, "confirm_cpu_clip_run", lambda runtime: legacy.append("clip") or False)
    ctl.run_ai_pipeline()
    assert legacy == ["clip"] and len(started) == 1  # the legacy path ran its own checks and did not start the cascade
