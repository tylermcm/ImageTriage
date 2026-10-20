"""One percent and three steps for a Set Up AI run, from the runtime installer's lines and the model downloads' messages."""
from __future__ import annotations

from image_triage.ai_setup_progress import STEP_CHECK, STEP_DOWNLOAD, STEP_INSTALL, STEPS, SetupProgress


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_it_starts_at_zero_on_the_first_step() -> None:
    progress = SetupProgress(runtime_mb=2600, bundles={"MANIQA": 543}, clock=Clock())
    assert progress.percent() == 0 and progress.step == STEP_DOWNLOAD and progress.step_label() == "Downloading"
    assert STEPS == ("Downloading", "Installing", "Checking that it works")
    assert progress.eta_text() == "" and progress.eta_seconds() is None


def test_pip_downloads_move_the_runtime_share_and_installing_comes_after() -> None:
    progress = SetupProgress(runtime_mb=1000, clock=Clock())
    progress.runtime_line("Collecting torch")
    progress.runtime_line("  Downloading torch-2.6.0-cp313-cp313-win_amd64.whl (500.0 MB)")
    assert 35 <= progress.percent() <= 40 and progress.step == STEP_DOWNLOAD  # half the download is 40% of the runtime's slice, less the final check
    progress.runtime_line("Downloading numpy-2.0.whl (12.5 MB)")
    progress.runtime_line("Downloading tqdm-4.6.whl (1500 kB)")
    before = progress.percent()
    progress.runtime_line("Installing collected packages: numpy, torch")
    assert progress.step == STEP_INSTALL and progress.percent() > before
    assert progress.percent() == round(0.85 * 0.96 * 100)
    progress.runtime_line("Successfully installed numpy-2.0 torch-2.6.0")
    assert progress.percent() == 96


def test_the_download_never_passes_its_slice_even_if_the_estimate_was_low() -> None:
    progress = SetupProgress(runtime_mb=100, clock=Clock())
    progress.runtime_line("Downloading huge.whl (900 MB)")
    assert progress.percent() <= 77 and progress.step == STEP_DOWNLOAD


def test_a_model_download_moves_the_bar_by_its_size() -> None:
    progress = SetupProgress(runtime_mb=0, bundles={"Face landmarker": 4, "Q-ReAlign Mini": 2000}, clock=Clock())
    progress.bundle_message("Downloading Face landmarker...")
    progress.bundle_message("Face landmarker: face_landmarker.task 4 / 4 MB")
    small = progress.percent()
    assert small <= 1  # 4 MB of 2004
    progress.bundle_message("Downloading Q-ReAlign Mini...")
    progress.bundle_message("Q-ReAlign Mini: model.safetensors 1000 / 2000 MB")
    assert 45 <= progress.percent() <= 49
    progress.bundle_done("Q-ReAlign Mini")
    progress.bundle_done("Face landmarker")
    assert progress.percent() == 96


def test_messages_about_unknown_bundles_are_ignored_and_progress_never_goes_backwards() -> None:
    progress = SetupProgress(runtime_mb=0, bundles={"MANIQA": 500}, clock=Clock())
    progress.bundle_message("Something else: file 5 / 6 MB")
    assert progress.percent() == 0
    progress.bundle_message("MANIQA: ckpt.pt 400 / 500 MB")
    high = progress.percent()
    progress.bundle_message("MANIQA: ckpt.pt 100 / 500 MB")  # a second file starting over
    assert progress.percent() == high
    assert progress.step == STEP_INSTALL


def test_checking_holds_at_96_and_finishing_is_100() -> None:
    progress = SetupProgress(runtime_mb=500, bundles={"MANIQA": 500}, clock=Clock())
    progress.start_check()
    assert progress.step == STEP_CHECK and progress.percent() == 96 and not progress.finished
    progress.finish()
    assert progress.percent() == 100 and progress.finished and progress.message() == "AI is ready."


def test_a_run_with_nothing_to_download_still_checks() -> None:
    progress = SetupProgress(clock=Clock())
    assert progress.percent() == 0
    progress.start_check()
    assert progress.percent() == 96
    progress.finish()
    assert progress.percent() == 100


def test_the_eta_follows_the_pace_and_is_withheld_early() -> None:
    clock = Clock()
    progress = SetupProgress(runtime_mb=0, bundles={"Model": 1000}, clock=clock)
    progress.bundle_message("Model: f 5 / 1000 MB")
    clock.now += 10
    assert progress.eta_seconds() is None  # under 3%
    progress.bundle_message("Model: f 250 / 1000 MB")
    clock.now += 50
    seconds = progress.eta_seconds()
    assert seconds is not None and 120 <= seconds <= 220  # a quarter in a minute: about three more
    assert progress.eta_text() == "about 3 min left"
    assert "Installing, 24%" in progress.message()
