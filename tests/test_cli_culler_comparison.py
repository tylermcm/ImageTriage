import json
import os
from pathlib import Path

from PIL import Image
import pytest

from image_triage.cli_culler_comparison import differences, compare_run
from image_triage.cli_culler_task import CliCullerRunTask

ROOT = Path(__file__).resolve().parents[1]


def test_comparison_reports_changed_verdict_and_unknown_fields():
    assert differences({"files": {"a": {"decision": "keep"}}},
                       {"files": {"a": {"decision": "reject"}}}) == ["/files/a/decision"]
    assert differences({"future": None}, {"future": 1}) == ["/future"]


def test_cli_comparison_uses_independent_cold_and_warm_starting_caches(tmp_path):
    python = os.environ.get("CLI_CULLER_PYTHON")
    if not python:
        pytest.skip("Set CLI_CULLER_PYTHON to the pinned engine interpreter")
    photos = tmp_path / "shoot"
    photos.mkdir()
    Image.new("RGB", (200, 100), "black").save(photos / "a.jpg")
    runtime_path = tmp_path / "runtime.json"
    runtime_path.write_text(json.dumps({"python": python, "worker": str(ROOT / "image_triage/cli_culler_worker.py"),
                                       "app_path": str(ROOT)}))
    for _ in range(2):
        task = CliCullerRunTask(folders=[photos], python=Path(python),
                               worker=ROOT / "image_triage/cli_culler_worker.py",
                               app_path=ROOT, settings={}, cache_namespace="comparison-test", workers=1,
                               comparison_snapshot=True)
        completed = []
        task.signals.finished.connect(lambda *args: completed.append(args))
        task.run()
        assert completed
        report_path = compare_run(runtime_path, task.paths.active)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["match"], report
        assert report["ignored_fields"] == ["timings"]
        assert Path(report["starting_cache"], "complete.json").exists()
