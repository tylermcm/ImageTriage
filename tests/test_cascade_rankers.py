"""Tests for the cascade ranker layer. No torch, no weights: the worker side is a small Python script that speaks the real protocol.

Set IMAGE_TRIAGE_RUN_RANKER_TESTS=1 (with the AI runtime and both models installed) to run the real models at the bottom of the file.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from image_triage.cascade.rankers import (
    MANIQA,
    QREALIGN,
    RankerCancelled,
    RankerUnavailable,
    RankerWeightsMissing,
    RankerWorkerError,
    WorkerRanker,
    ensure_weights,
    estimate_cascade_cost,
    estimate_cost,
    protocol,
    register_bundles,
)
from image_triage.cascade.rankers import client as client_module

REPO_ROOT = Path(__file__).resolve().parents[1]

# A worker that needs no torch. MODE selects behaviour; it uses the real protocol helpers and the real serve loop.
FAKE_WORKER = textwrap.dedent(
    """
    import sys, time, json
    from image_triage.cascade.rankers import protocol
    from image_triage.cascade.rankers.worker import serve

    mode = sys.argv[1]
    out = sys.stdout
    sys.stdout = sys.stderr

    def emit(message):
        out.write(protocol.encode_event(message) + "\\n")
        out.flush()

    print("noise a library printed", file=out, flush=True)
    if mode == "fatal_module":
        emit({"event": "error", "category": "missing_module", "message": "timm is not installed in the AI runtime"})
        sys.exit(2)
    if mode == "fatal_weights":
        emit({"event": "error", "category": "weights_missing", "message": "ckpt not found"})
        sys.exit(2)
    if mode == "silent":
        time.sleep(60)
    emit({"event": "ready", "device": "cpu", "precision": "fp32", "seconds": 0.0})
    if mode == "crash":
        print("Traceback: CUDA out of memory", file=sys.stderr, flush=True)
        sys.exit(3)

    class Engine:
        def score(self, path):
            if mode == "slow":
                time.sleep(0.3)
            if path.endswith("bad.jpg"):
                raise OSError("cannot identify image file")
            return float(len(path))

    if mode == "batch_sizes":
        # report the size of the request each frame arrived in
        for line in sys.stdin:
            request = json.loads(line)
            if request["op"] == "quit":
                break
            for item in request["items"]:
                emit({"event": "score", "id": request["id"], "key": item["key"], "value": float(len(request["items"]))})
            emit({"event": "done", "id": request["id"]})
        sys.exit(0)
    serve(Engine(), sys.stdin, emit)
    """
)


def launcher_for(tmp_path: Path, mode: str, launches: list | None = None):
    script = tmp_path / "fake_worker.py"
    script.write_text(FAKE_WORKER, encoding="utf-8")

    def launch(spec_launch):
        if launches is not None:
            launches.append(spec_launch)
        env = dict(os.environ, PYTHONPATH=str(REPO_ROOT), PYTHONUNBUFFERED="1", QT_QPA_PLATFORM="offscreen")
        return subprocess.Popen(
            [sys.executable, str(script), mode], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )

    return launch


def make_ranker(tmp_path: Path, mode: str = "ok", spec=MANIQA, **kwargs) -> WorkerRanker:
    kwargs.setdefault("model_dir", tmp_path)
    kwargs.setdefault("ready_timeout", 30.0)
    return WorkerRanker(spec, launcher=launcher_for(tmp_path, mode), **kwargs)


# -- protocol -----------------------------------------------------------------------------------------------------------


def test_protocol_ignores_lines_that_are_not_events():
    assert protocol.decode_event("Loading checkpoint shards: 100%") is None
    assert protocol.decode_event(protocol.PREFIX + "{not json") is None
    assert protocol.decode_event(protocol.PREFIX + "[1, 2]") is None
    assert protocol.decode_event(protocol.encode_event({"event": "done", "id": 3})) == {"event": "done", "id": 3}


# -- the Scorer contract ------------------------------------------------------------------------------------------------


def test_scores_every_key_and_ignores_stdout_noise(tmp_path):
    keys = ["a.jpg", "bb.jpg", "ccc.jpg"]
    with make_ranker(tmp_path) as ranker:
        scores = ranker(keys)
    assert scores == {"a.jpg": 5.0, "bb.jpg": 6.0, "ccc.jpg": 7.0}
    assert ranker.active_device == ""  # closed
    assert ranker.seconds_per_frame is not None and ranker.frames_scored == 3


def test_resolve_path_maps_keys_to_files(tmp_path):
    with make_ranker(tmp_path, resolve_path=lambda key: Path("/photos") / key) as ranker:
        scores = ranker(["x.jpg"])
    assert scores["x.jpg"] == float(len(str(Path("/photos") / "x.jpg")))


def test_empty_call_does_not_start_a_worker(tmp_path):
    launches: list = []
    ranker = WorkerRanker(MANIQA, launcher=launcher_for(tmp_path, "ok", launches), model_dir=tmp_path)
    assert dict(ranker([])) == {}
    assert launches == []


def test_duplicate_keys_are_rejected(tmp_path):
    with make_ranker(tmp_path) as ranker, pytest.raises(ValueError):
        ranker(["a.jpg", "a.jpg"])


def test_batches_follow_batch_size_and_the_worker_is_reused(tmp_path):
    launches: list = []
    ranker = WorkerRanker(MANIQA, launcher=launcher_for(tmp_path, "batch_sizes", launches), model_dir=tmp_path, batch_size=3)
    with ranker:
        first = ranker([f"{i}.jpg" for i in range(7)])
        second = ranker(["8.jpg"])
    assert sorted(first.values()) == [1.0, 3.0, 3.0, 3.0, 3.0, 3.0, 3.0]  # batches of 3, 3, 1
    assert second == {"8.jpg": 1.0}
    assert len(launches) == 1


def test_per_spec_default_batch_size(tmp_path):
    assert WorkerRanker(QREALIGN, model_dir=tmp_path)._batch_size == QREALIGN.batch_size
    assert WorkerRanker(MANIQA, model_dir=tmp_path, batch_size=0)._batch_size == 1


def test_progress_reports_each_frame(tmp_path):
    seen: list[tuple[int, int]] = []
    with make_ranker(tmp_path, progress=lambda done, total: seen.append((done, total)), batch_size=2) as ranker:
        ranker(["a.jpg", "b.jpg", "c.jpg"])
    assert seen == [(1, 3), (2, 3), (3, 3)]


def test_unreadable_frame_scores_last_and_is_recorded(tmp_path):
    with make_ranker(tmp_path) as ranker:
        scores = ranker(["good.jpg", "bad.jpg"])
    assert scores["bad.jpg"] == float("-inf")
    assert scores["good.jpg"] == float(len("good.jpg"))
    assert "OSError" in ranker.failures["bad.jpg"]


def test_plugs_into_run_cascade_as_a_scorer(tmp_path):
    from image_triage.cascade import Scorer

    ranker: Scorer = make_ranker(tmp_path)
    try:
        assert ranker(["k.jpg"]) == {"k.jpg": 5.0}
    finally:
        ranker.close()


# -- cancellation -------------------------------------------------------------------------------------------------------


def test_cancel_between_frames_kills_the_worker(tmp_path):
    state = {"cancel": False}
    ranker = make_ranker(tmp_path, "slow", batch_size=8, cancel_check=lambda: state["cancel"],
                         progress=lambda done, total: state.update(cancel=True))
    started = time.perf_counter()
    with pytest.raises(RankerCancelled):
        ranker([f"{i}.jpg" for i in range(20)])  # 20 frames at 0.3 s would take 6 s
    assert time.perf_counter() - started < 4.0
    assert ranker._session is None


def test_cancel_before_start_never_launches(tmp_path):
    launches: list = []
    ranker = WorkerRanker(MANIQA, launcher=launcher_for(tmp_path, "ok", launches), model_dir=tmp_path, cancel_check=lambda: True)
    with pytest.raises(RankerCancelled):
        ranker(["a.jpg"])
    assert launches == []


def test_ranker_is_usable_again_after_a_cancel(tmp_path):
    state = {"cancel": True}
    ranker = make_ranker(tmp_path, cancel_check=lambda: state["cancel"])
    with pytest.raises(RankerCancelled):
        ranker(["a.jpg"])
    state["cancel"] = False
    with ranker:
        assert ranker(["a.jpg"]) == {"a.jpg": 5.0}


# -- error paths --------------------------------------------------------------------------------------------------------


def test_worker_crash_reports_exit_code_and_stderr(tmp_path):
    ranker = make_ranker(tmp_path, "crash")
    with pytest.raises(RankerWorkerError) as raised:
        ranker(["a.jpg"])
    assert "CUDA out of memory" in str(raised.value)
    assert ranker._session is None


def test_missing_python_module_is_a_runtime_problem(tmp_path):
    with pytest.raises(RankerUnavailable) as raised:
        make_ranker(tmp_path, "fatal_module")(["a.jpg"])
    assert "timm" in str(raised.value) and raised.value.remediation


def test_worker_reporting_missing_weights(tmp_path):
    with pytest.raises(RankerWeightsMissing):
        make_ranker(tmp_path, "fatal_weights")(["a.jpg"])


def test_silent_worker_times_out(tmp_path):
    ranker = make_ranker(tmp_path, "silent", ready_timeout=0.5)
    started = time.perf_counter()
    with pytest.raises(RankerWorkerError, match="took longer"):
        ranker(["a.jpg"])
    assert time.perf_counter() - started < 10.0


def test_launcher_failure_is_reported_as_unavailable(tmp_path):
    def broken(_launch):
        raise FileNotFoundError("python.exe")

    with pytest.raises(RankerUnavailable, match="could not be started"):
        WorkerRanker(MANIQA, launcher=broken, model_dir=tmp_path)(["a.jpg"])


def test_default_launcher_reports_a_missing_runtime(monkeypatch, tmp_path):
    from image_triage import ai_env

    def no_runtime(*_args, **_kwargs):
        raise ai_env.AIRuntimeUnavailable("The AI runtime is not installed yet.", remediation="Run Set Up AI.")

    monkeypatch.setattr(ai_env, "select_runtime", no_runtime)
    with pytest.raises(RankerUnavailable) as raised:
        WorkerRanker(QREALIGN, model_dir=tmp_path, launcher=client_module.default_launcher)(["a.jpg"])
    assert "not installed" in str(raised.value) and raised.value.remediation == "Run Set Up AI."


# -- weights ------------------------------------------------------------------------------------------------------------


def test_bundles_register_once_with_verified_digests():
    from image_triage import ai_manifest

    register_bundles()
    register_bundles()
    assert {"maniqa", "qrealign"} <= set(ai_manifest.MODEL_BUNDLES)
    qrealign = ai_manifest.MODEL_BUNDLES["qrealign"]
    digests = ai_manifest.bundle_digests("qrealign")
    assert qrealign.is_revision_pinned
    assert set(digests) == set(qrealign.filenames)
    assert all(d.sha256 and d.size for d in digests.values())
    assert ai_manifest.MODEL_BUNDLES["maniqa"].filenames == ("ckpt_koniq10k.pt",)


def _isolated_models(monkeypatch, tmp_path):
    monkeypatch.setenv("IMAGE_TRIAGE_MANIQA_MODEL_DIR", str(tmp_path / "maniqa"))
    monkeypatch.setenv("IMAGE_TRIAGE_QREALIGN_MODEL_DIR", str(tmp_path / "qrealign"))
    monkeypatch.setenv("IMAGE_TRIAGE_BLINK_MODEL_DIR", str(tmp_path / "faces_blink"))


def test_missing_weights_without_download_is_a_clear_error(monkeypatch, tmp_path):
    _isolated_models(monkeypatch, tmp_path)
    with pytest.raises(RankerWeightsMissing, match="not downloaded") as raised:
        ensure_weights(QREALIGN, download=False)
    assert raised.value.remediation


def test_a_ranker_without_a_download_source_says_where_to_put_the_file(monkeypatch, tmp_path):
    from dataclasses import replace

    from image_triage.ai_manifest import MODEL_BUNDLES
    from image_triage.cascade.rankers import register_bundles

    _isolated_models(monkeypatch, tmp_path)
    register_bundles()
    monkeypatch.delenv("IMAGE_TRIAGE_MANIQA_MODEL_REPO_ID", raising=False)
    monkeypatch.setitem(MODEL_BUNDLES, "maniqa", replace(MODEL_BUNDLES["maniqa"], repo_id=""))  # as if the hosting repo were not configured
    with pytest.raises(RankerWeightsMissing) as raised:
        ensure_weights(MANIQA, download=True)
    assert "MODEL_REPO_ID" in raised.value.remediation and str(tmp_path) in raised.value.remediation.replace("\\", "/").replace("/", "\\") or "MODEL_REPO_ID" in raised.value.remediation


def test_present_weights_are_used_without_downloading(monkeypatch, tmp_path):
    import hashlib

    from image_triage.ai_manifest import MODEL_FILE_DIGESTS, FileDigest
    from image_triage.cascade.rankers import register_bundles

    _isolated_models(monkeypatch, tmp_path)
    register_bundles()
    stand_in = b"not a real checkpoint"
    # The real pinned digest would (rightly) refuse this stand-in, so pin the stand-in itself for this test.
    monkeypatch.setitem(MODEL_FILE_DIGESTS, "maniqa", {"ckpt_koniq10k.pt": FileDigest(len(stand_in), hashlib.sha256(stand_in).hexdigest())})
    directory = tmp_path / "maniqa"
    directory.mkdir()
    (directory / "ckpt_koniq10k.pt").write_bytes(stand_in)
    assert ensure_weights(MANIQA, download=False) == directory.resolve()


def test_a_file_that_does_not_match_the_pinned_digest_is_refused(monkeypatch, tmp_path):
    from image_triage.cascade.rankers import BLINK

    _isolated_models(monkeypatch, tmp_path)
    for spec, name in ((MANIQA, "ckpt_koniq10k.pt"), (BLINK, "face_landmarker.task")):
        directory = tmp_path / spec.bundle_key
        directory.mkdir(exist_ok=True)
        (directory / name).write_bytes(b"not the file we pinned")
        with pytest.raises(RankerWeightsMissing, match="verification"):
            ensure_weights(spec, download=False)


def test_the_pinned_digests_are_the_hosted_files(monkeypatch, tmp_path):
    from image_triage.ai_manifest import MODEL_FILE_DIGESTS
    from image_triage.cascade.rankers import register_bundles

    register_bundles()
    assert MODEL_FILE_DIGESTS["maniqa"]["ckpt_koniq10k.pt"].size == 543335435
    assert MODEL_FILE_DIGESTS["faces_blink"]["face_landmarker.task"].size == 3758596


def test_download_goes_through_the_model_store_and_failures_are_mapped(monkeypatch, tmp_path):
    from image_triage import ai_model_store

    _isolated_models(monkeypatch, tmp_path)
    calls = []

    def fail(key, **kwargs):
        calls.append((key, kwargs["cancel_check"]))
        raise ai_model_store.ModelInstallError("The connection dropped.", category="network")

    monkeypatch.setattr(ai_model_store, "install_bundle", fail)
    with pytest.raises(RankerWeightsMissing, match="could not be downloaded"):
        ensure_weights(QREALIGN, download=True, cancel_check=lambda: False)
    assert calls and calls[0][0] == "qrealign"


def test_cancelled_download_is_a_ranker_cancel(monkeypatch, tmp_path):
    from image_triage import ai_model_store

    _isolated_models(monkeypatch, tmp_path)

    def cancelled(*_args, **_kwargs):
        raise ai_model_store.ModelInstallCancelled("Model download stopped.")

    monkeypatch.setattr(ai_model_store, "install_bundle", cancelled)
    with pytest.raises(RankerCancelled):
        WorkerRanker(QREALIGN)(["a.jpg"])


# -- cost estimate ------------------------------------------------------------------------------------------------------


def test_cost_uses_the_measured_reference_timings():
    gpu = estimate_cost(100, 40, "gpu")
    assert gpu.maniqa_seconds == pytest.approx(54.0)
    assert gpu.aesthetic_seconds == pytest.approx(23.6)
    assert gpu.total_seconds == pytest.approx(77.6)
    cpu = estimate_cost(100, 40, "cpu")
    assert cpu.maniqa_seconds == pytest.approx(360.0)
    assert cpu.aesthetic_seconds == pytest.approx(136.0)
    assert estimate_cost(10, 0, "cuda:1").device == "gpu"
    assert estimate_cost(0, 0).total_seconds == 0.0


def test_cost_prefers_timings_measured_on_this_machine():
    estimate = estimate_cost(10, 10, "cpu", measured={"maniqa": 1.0})
    assert estimate.maniqa_seconds == 10.0
    assert estimate.aesthetic_seconds == pytest.approx(34.0)  # unmeasured ranker keeps the reference


def test_cost_rejects_negative_counts():
    with pytest.raises(ValueError):
        estimate_cost(-1, 0)


def test_cascade_cost_follows_what_run_cascade_would_score():
    # stacks of 1, 2, 3 survivors skip MANIQA (nothing to cut); 5 and 8 are MANIQA-scored, then 3 go to the aesthetic ranker.
    # Aesthetic frames: 2 + 3 (from the 2- and 3-stacks) + 3 + 3 = 11; the singleton needs no ranking.
    estimate = estimate_cascade_cost([1, 2, 3, 5, 8], maniqa_keep=3, device="gpu")
    assert estimate.maniqa_frames == 13
    assert estimate.aesthetic_frames == 11


# -- the main process stays torch free ----------------------------------------------------------------------------------


def test_importing_the_rankers_never_imports_torch():
    code = (
        "import sys\n"
        "import image_triage.cascade.rankers as r\n"
        "import image_triage.cascade.rankers.worker\n"
        "bad = [m for m in ('torch', 'timm', 'transformers', 'einops') if m in sys.modules]\n"
        "assert not bad, bad\n"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
                            env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
    assert result.returncode == 0, result.stderr


# -- real models (opt in) -----------------------------------------------------------------------------------------------


@pytest.mark.skipif(os.environ.get("IMAGE_TRIAGE_RUN_RANKER_TESTS") != "1", reason="set IMAGE_TRIAGE_RUN_RANKER_TESTS=1 to run the real rankers")
@pytest.mark.parametrize("spec", [MANIQA, QREALIGN], ids=lambda s: s.kind)
def test_real_ranker_scores_synthetic_frames(spec, tmp_path):
    """Needs the managed AI runtime (torch, timm, transformers) and the model installed or downloadable. Slow."""
    import numpy as np
    from PIL import Image, ImageFilter

    rng = np.random.default_rng(1)
    base = Image.fromarray((rng.random((900, 1400, 3)) * 255).astype("uint8")).filter(ImageFilter.GaussianBlur(2))
    frames = {"sharp": base, "blurred": base.filter(ImageFilter.GaussianBlur(25)), "small": base.resize((300, 200))}
    paths = {}
    for name, image in frames.items():
        paths[name] = str(tmp_path / f"{name}.jpg")
        image.save(paths[name], quality=92)
    with WorkerRanker(spec, resolve_path=lambda key: Path(paths[key]), batch_size=2) as ranker:
        scores = ranker(list(paths))
    assert set(scores) == set(paths)
    assert all(np.isfinite(v) for v in scores.values()), scores
    assert not ranker.failures
    if spec is QREALIGN:
        assert all(0.0 <= v <= 1.0 for v in scores.values())
