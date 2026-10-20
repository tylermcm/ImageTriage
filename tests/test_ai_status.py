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


def test_an_install_shows_its_percent() -> None:
    status = compute_status(core_installed=True, installing=True, percent=42)
    assert status.small == "42%" and status.percent == 42
    assert compute_status(core_installed=True, installing=True).small == ""


def test_update_says_whether_models_changed_or_are_simply_missing() -> None:
    changed = compute_status(core_installed=True, changed=(("Face landmarker", 4), ("MANIQA image quality ranker", 543)))
    assert changed.state is AIState.UPDATE and changed.small == "547 MB"
    assert changed.detail == "2 AI models changed in this version and need downloading again."
    assert changed.items == (("Face landmarker", "4 MB"), ("MANIQA image quality ranker", "543 MB"))
    one = compute_status(core_installed=True, changed=(("Face landmarker", 4),))
    assert one.detail == "1 AI model changed in this version and needs downloading again."
    absent = compute_status(core_installed=True, absent=(("Depth Anything V2 (small)", 100),))
    assert absent.detail == "1 model this version uses is not installed yet." and absent.small == "100 MB"
    both = compute_status(core_installed=True, changed=(("A", 10),), absent=(("B", 20),))
    assert both.small == "30 MB" and both.detail.startswith("1 AI model changed")
    assert len(both.items) == 2


def test_nothing_pending_is_ready() -> None:
    assert compute_status(core_installed=True, changed=(), absent=()).state is AIState.READY
