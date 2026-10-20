"""Which AI status the chip shows."""
from __future__ import annotations

from image_triage.ai_status import AIState, compute_status


def test_ready_is_quiet_and_everything_else_needs_attention() -> None:
    ready = compute_status(core_installed=True)
    assert ready.state is AIState.READY and ready.label == "AI ready" and not ready.needs_attention
    for status in (
        compute_status(core_installed=False, default_download_mb=2868),
        compute_status(core_installed=True, installing=True),
        compute_status(core_installed=True, missing_mb=547),
        compute_status(core_installed=True, problems=1),
    ):
        assert status.needs_attention


def test_a_missing_core_shows_the_size_of_the_default_install() -> None:
    laptop = compute_status(core_installed=False, default_download_mb=2868)
    assert (laptop.state, laptop.label, laptop.small) == (AIState.NEEDS_SETUP, "Set up AI", "2.9 GB")
    gpu = compute_status(core_installed=False, default_download_mb=10412)
    assert gpu.small == "10.4 GB"
    assert compute_status(core_installed=False).small == ""


def test_precedence_install_then_repair_then_setup_then_update() -> None:
    assert compute_status(core_installed=False, installing=True, problems=2, missing_mb=9).state is AIState.INSTALLING
    assert compute_status(core_installed=False, problems=2, missing_mb=9).state is AIState.REPAIR
    assert compute_status(core_installed=False, missing_mb=9, default_download_mb=5).state is AIState.NEEDS_SETUP
    assert compute_status(core_installed=True, missing_mb=547).state is AIState.UPDATE
    assert compute_status(core_installed=True, missing_mb=547).small == "547 MB"


def test_problem_counts_are_pluralised() -> None:
    assert compute_status(core_installed=True, problems=1).small == "1 problem"
    assert compute_status(core_installed=True, problems=3).small == "3 problems"


def test_an_install_message_becomes_the_detail() -> None:
    status = compute_status(core_installed=True, installing=True, install_message="Downloading the AI runtime")
    assert status.detail == "Downloading the AI runtime"
    assert compute_status(core_installed=True, installing=True).detail
