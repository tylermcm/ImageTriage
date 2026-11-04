from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

from PIL import Image, ImageDraw
from PIL.TiffImagePlugin import IFDRational
import pytest

from image_triage.cli_culler_results import NativeCullResults

ROOT = Path(__file__).resolve().parents[1]


def test_native_results_keep_nulls_unknown_fields_and_distinct_identities():
    row = {"decision": "keep", "capture": "c", "group": "bracket",
           "scene": 3, "comparison_set": "scene-3", "score": None,
           "rank_in_set": 1, "outputs": {"future_model": [1, None]}}
    document = {"schema": "aiculler-decisions/1", "future": {"x": 2},
                "files": {"C:/拍摄/A.NEF": row, "C:/拍摄/B.NEF": row.copy()}}
    native = NativeCullResults(document)
    assert native.to_document() == document
    assert native.bucket("C:/拍摄/A.NEF") == "keeper"
    assert native.bucket("C:/拍摄/B.NEF") == "keeper"
    document["files"]["C:/拍摄/A.NEF"]["decision"] = "reject"
    native.file("C:/拍摄/A.NEF")["outputs"]["future_model"].append(3)
    assert native.file("C:/拍摄/A.NEF")["decision"] == "keep"
    assert native.file("C:/拍摄/A.NEF")["outputs"]["future_model"] == [1, None]
    assert NativeCullResults(json.loads(json.dumps(native.to_document()))).to_document() == native.to_document()


@pytest.mark.parametrize("document", [
    {"schema": "aiculler-decisions/2", "files": {}},
    {"schema": "aiculler-decisions/1", "files": []},
    {"schema": "aiculler-decisions/1", "files": {"a": {"decision": "winner"}}},
])
def test_rejects_unsupported_or_invalid_native_documents(document):
    with pytest.raises(ValueError):
        NativeCullResults(document)


@pytest.mark.parametrize("decision,bucket", [
    ("keep", "keeper"), ("reject", "reject"), ("review", "needs_review"),
])
def test_native_bucket_does_not_promote_rank_one_to_winner(decision, bucket):
    results = NativeCullResults({"schema": "aiculler-decisions/1",
        "files": {"a": {"decision": decision, "rank_in_set": 1, "score": None}}})
    assert results.bucket("a") == bucket


@pytest.mark.parametrize("settings", [
    {},
    {"bursts_keep": 2, "brackets": "pick_one"},
    {"brackets": "ignore"},
    json.loads((ROOT / "packaging/cli_culler_default_settings.json").read_text()),
])
def test_plain_file_worker_matches_direct_engine_outside_checkout(tmp_path, settings):
    python = os.environ.get("CLI_CULLER_PYTHON")
    if not python:
        pytest.skip("Set CLI_CULLER_PYTHON to the isolated pinned engine interpreter")
    shoot = tmp_path / "拍摄"
    shoot.mkdir()
    # A burst, a three-frame exposure bracket, a lone frame and a black
    # misfire, with real EXIF for the engine's metadata/grouping path.
    for number, second in enumerate((0, 0.1, 0.2, 30, 30.3, 30.6, 90, 300)):
        image = Image.new("RGB", (480, 320), "black" if number == 7 else (40, 100, 160))
        if number != 7:
            ImageDraw.Draw(image).rectangle((40, 50, 330, 250), fill=(190, 140, 80))
        exif = Image.Exif()
        exif[0x010F], exif[0x0110] = "Canon", "Canon EOS R5"
        details = exif.get_ifd(0x8769)
        details[0x9003] = f"2024:05:01 12:{int(second) // 60:02d}:{int(second) % 60:02d}"
        details[0x9011] = "+02:00"
        details[0xA431] = "012345678901"
        details[0x920A] = IFDRational(24, 1)
        details[0x9291] = f"{round((second % 1) * 100):02d}"
        ev = (0, -1, 1)[number - 3] if 3 <= number <= 5 else 0
        details[0x829A] = IFDRational(1, round(250 / (2 ** ev)))
        details[0x829D] = IFDRational(8, 1)
        details[0x8827] = 100
        details[0x9204] = IFDRational(ev, 1)
        details[0xA402] = 2 if 3 <= number <= 5 else 0
        image.save(shoot / f"IMG_{number + 1:04d}.JPG", exif=exif)
    copies = shoot / "_picks"
    copies.mkdir()
    shutil.copyfile(shoot / "IMG_0001.JPG", copies / "IMG_0001.JPG")
    app = str(ROOT)
    request = {"protocol": "image-triage-cli-culler/1",
               "folders": [str(shoot)], "settings": settings, "app_path": app,
               "cache_root": str(tmp_path / "worker-cache"),
               "staging_root": str(tmp_path / "runs"), "workers": 1}
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    worker = tmp_path / "cli_culler_worker.py"
    shutil.copyfile(ROOT / "image_triage/cli_culler_worker.py", worker)
    completed = subprocess.run([python, "-I", str(worker), str(request_path)],
                               cwd=tmp_path, capture_output=True, text=True, timeout=90)
    assert completed.returncode == 0, completed.stderr
    events = [json.loads(line) for line in completed.stdout.splitlines()]
    assert events[0]["event"] == "runtime"
    # CI may install the isolated runtime below checkout/build. What matters
    # is importing its installed packages, never the host's legacy aiculler.
    purelib = Path(subprocess.check_output(
        [python, "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        cwd=tmp_path, text=True).strip()).resolve()
    assert Path(events[0]["engine_path"]).resolve().is_relative_to(purelib)
    assert Path(events[0]["bracketeering_path"]).resolve().is_relative_to(purelib)
    assert not Path(events[0]["engine_path"]).resolve().is_relative_to(ROOT / "aiculler")
    assert any(event["event"] == "progress" for event in events)
    assert events[-1]["event"] == "complete"
    actual = NativeCullResults.load(events[-1]["decisions"]).to_document()
    direct = tmp_path / "direct.json"
    code = """
import json, sys
from aiculler import cull, CullSettings, ModelRegistry
from aiculler.outputs import write_decisions
r = json.loads(sys.argv[1])
models = ModelRegistry.from_app_manifest(r['app_path'])
try:
    result = cull(r['folders'], CullSettings.from_dict(r['settings']), models=models,
                  cache_db=sys.argv[2], workers=1)
    write_decisions(result, sys.argv[3])
finally:
    models.close()
"""
    baseline = subprocess.run([python, "-I", "-c", code, json.dumps(request),
                               str(tmp_path / "direct.sqlite"), str(direct)],
                              cwd=tmp_path, capture_output=True, text=True, timeout=90)
    assert baseline.returncode == 0, baseline.stderr
    expected = json.loads(direct.read_text(encoding="utf-8"))
    # Independent fresh caches; only wall-clock timing may differ.
    actual.pop("timings")
    expected.pop("timings")
    assert actual == expected
    assert len(actual["files"]) == 9
    assert any(not row["primary"] for row in actual["files"].values())
    assert any(row["kind"] == "burst" for row in actual["files"].values())
    assert any(row["kind"] == "exposure" for row in actual["files"].values())
    assert events[-1]["warnings"] == actual["warnings"]
    assert events[-1]["models"] == actual["models"]
    assert not list(shoot.glob("*.xmp"))
    if not settings:
        rerun = subprocess.run([python, "-I", str(worker), str(request_path)],
                               cwd=tmp_path, capture_output=True, text=True, timeout=90)
        assert rerun.returncode == 0, rerun.stderr
        rerun_events = [json.loads(line) for line in rerun.stdout.splitlines()]
        assert rerun_events[-1]["decisions"] != events[-1]["decisions"]
        reused = NativeCullResults.load(rerun_events[-1]["decisions"]).to_document()
        assert reused["files"] == actual["files"]
        assert reused["stats"]["sharpness_computed"] == 0
        assert reused["stats"]["exposure_computed"] == 0


def test_worker_rejects_bad_request_without_success_event(tmp_path):
    python = os.environ.get("CLI_CULLER_PYTHON")
    if not python:
        pytest.skip("Set CLI_CULLER_PYTHON to the isolated pinned engine interpreter")
    request = tmp_path / "bad.json"
    request.write_text('{"protocol": "unknown"}')
    result = subprocess.run(
        [python, "-I", str(ROOT / "image_triage/cli_culler_worker.py"), str(request)],
        cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 1
    assert [json.loads(line)["event"] for line in result.stdout.splitlines()] == ["error"]
    assert "Expected request protocol" in result.stderr
