"""Packaging and launch rules for the cascade's workers: the blink detector needs only the compact runtime plus MediaPipe, the locks carry exactly what it
needs, and a frame flagged by pass 1 but kept (alone, or with every sibling flagged too) goes to Needs review instead of being called a keeper."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from image_triage import ai_runtime_packages as rp
from image_triage.ai_env import AIRuntimeUnavailable
from image_triage.ai_results import AICullBucket, ai_cull_bucket_for_result, find_ai_result_for_record
from image_triage.cascade.folder import ROLE_FLAGGED, FolderFrame, run_folder
from image_triage.cascade.model import FrameFeatures
from image_triage.cascade.rankers import BLINK, MANIQA, QREALIGN
from image_triage.cascade.rankers.client import WorkerLaunch, default_launcher
from image_triage.cascade.rankers.errors import RankerUnavailable
from image_triage.cascade.task import Scorers
from tests.test_cascade_run import roles, run_task, save, scene

LOCKS = sorted((Path(__file__).resolve().parents[1] / rp.LOCK_DIRECTORY).glob("py313-windows-amd64-*.txt"))
MEDIAPIPE_CLOSURE = {"mediapipe", "absl-py", "flatbuffers", "matplotlib", "protobuf", "numpy", "certifi", "contourpy", "cycler", "fonttools", "kiwisolver",
                     "packaging", "pillow", "pyparsing", "python-dateutil", "six"}


# -- the runtime declares and locks what the blink worker needs -----------------------------------------------------------------------------


def test_the_compact_runtime_requires_mediapipe():
    assert "mediapipe==0.10.35" in rp.AI_RUNTIME_BASE_PIP_REQUIREMENTS
    assert "mediapipe" in rp.AI_RUNTIME_BASE_REQUIRED_MODULE_NAMES and "mediapipe" in rp.AI_RUNTIME_REQUIRED_MODULE_NAMES
    assert not any(r.startswith("mediapipe") for r in rp.AI_RUNTIME_TORCH_PIP_REQUIREMENTS)  # it lives in the base set, so a no-torch install has it


def test_the_lock_generator_keeps_the_conflicting_opencv_and_sounddevice_out():
    assert {"opencv-contrib-python", "opencv-python", "sounddevice"} <= rp.AI_RUNTIME_LOCK_EXCLUDED_DISTRIBUTIONS
    source = (Path(__file__).resolve().parents[1] / "scripts" / "refresh_ai_runtime_lock.py").read_text(encoding="utf-8")
    assert "AI_RUNTIME_LOCK_EXCLUDED_DISTRIBUTIONS" in source


@pytest.mark.parametrize("lock", LOCKS, ids=lambda p: p.name)
def test_every_lock_carries_mediapipe_and_what_it_imports(lock):
    text = lock.read_text(encoding="utf-8")
    pins = {m.group(1).lower().replace("_", "-"): m.group(2) for m in re.finditer(r"^([A-Za-z0-9_.-]+)==(\S+) \\$", text, re.M)}
    missing = MEDIAPIPE_CLOSURE - set(pins)
    assert not missing, f"{lock.name} lacks {sorted(missing)}"
    assert pins["mediapipe"] == "0.10.35"
    assert not (set(pins) & rp.AI_RUNTIME_LOCK_EXCLUDED_DISTRIBUTIONS)


@pytest.mark.parametrize("lock", LOCKS, ids=lambda p: p.name)
def test_every_locked_distribution_has_a_hash_and_the_file_stays_sorted(lock):
    text = lock.read_text(encoding="utf-8")
    names = [m.group(1).lower() for m in re.finditer(r"^([A-Za-z0-9_.-]+)==\S+ \\$", text, re.M)]
    assert names == sorted(names)
    entries = re.split(r"\n(?=[A-Za-z0-9_.-]+==)", text.split("\n\n", 1)[1].strip())
    assert all(re.search(r"--hash=sha256:[0-9a-f]{64}", entry) for entry in entries)


# -- the launcher asks for the right runtime ------------------------------------------------------------------------------------------------


def launch_requirement(monkeypatch, spec):
    seen = {}

    def fake_select(device, *, install_root=None, require_torch=False):
        seen["require_torch"] = require_torch
        raise AIRuntimeUnavailable("not installed", category="runtime_missing", remediation="Set Up AI")

    monkeypatch.setattr("image_triage.ai_env.select_runtime", fake_select)
    with pytest.raises(RankerUnavailable):
        default_launcher(WorkerLaunch(spec, Path("."), "cpu"))
    return seen["require_torch"]


def test_the_blink_worker_runs_in_the_compact_runtime_and_the_rankers_need_torch(monkeypatch):
    assert launch_requirement(monkeypatch, BLINK) is False
    assert launch_requirement(monkeypatch, MANIQA) is True
    assert launch_requirement(monkeypatch, QREALIGN) is True


# -- a flagged frame that is kept ---------------------------------------------------------------------------------------------------------


def feature(key, *, blink=False, distance=0):
    return FrameFeatures(key, (1 << distance) - 1, 100.0, 0.01, 0.01, blink)


def test_a_lone_blinking_frame_is_flagged_and_kept_for_review():
    outcome = run_folder([FolderFrame("a", feature("a", blink=True), 0.0)]).outcomes["a"]
    assert outcome.role == ROLE_FLAGGED and outcome.flags == ("blink",)
    assert outcome.summary == "Flagged: eyes closed. It is the only frame, so it is kept for you to decide."


def test_when_every_frame_in_a_stack_blinks_all_are_kept_flagged():
    frames = [FolderFrame("a", feature("a", blink=True), 0.0), FolderFrame("b", feature("b", blink=True, distance=9), 1.0)]
    outcomes = run_folder(frames).outcomes
    assert {o.role for o in outcomes.values()} == {ROLE_FLAGGED}
    assert "every frame in this stack has the same problem" in outcomes["a"].summary


def test_a_blinker_with_a_good_sibling_is_still_rejected_and_the_sibling_is_kept():
    frames = [FolderFrame("a", feature("a", blink=True), 0.0), FolderFrame("b", feature("b", distance=9), 1.0)]
    outcomes = run_folder(frames).outcomes
    assert outcomes["a"].role == "rejected" and outcomes["b"].role == "final"


def test_a_blown_out_frame_alone_is_not_called_flagged_because_blown_out_does_not_reject():
    blown = FrameFeatures("a", 0, 100.0, 0.5, 0.5, False)
    # Alone and blown out, the stack has no pick; the frame is NO_PICK, not a flagged keeper.
    assert run_folder([FolderFrame("a", blown, 0.0)]).outcomes["a"].role == "no_pick"


def test_the_bucket_and_summary_reach_the_results_for_a_flagged_kept_frame(tmp_path):
    save(tmp_path / "solo.jpg", scene(4), 0)

    def factory(cancel, progress):
        return Scorers(blink=lambda keys: {k: 0.9 for k in keys})

    _, _, paths, records = run_task(tmp_path, scorer_factory=factory)
    results, _ = roles(paths, records)
    result = results["solo.jpg"]
    assert result.cascade_role == "flagged" and ai_cull_bucket_for_result(result) is AICullBucket.NEEDS_REVIEW
    assert result.triggered_tags == "blink" and "only frame" in result.cascade_summary
    assert find_ai_result_for_record(None, records[0]) is None
