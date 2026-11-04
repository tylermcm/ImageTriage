"""Verify a relocated validation bundle using its shipped interpreter and worker."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def check(bundle: Path):
    from PIL import Image
    from PySide6.QtCore import QCoreApplication
    from image_triage.cli_culler_task import CliCullerRunTask
    from image_triage.cli_culler_runtime import load_native_runtime, verify_native_runtime

    app = QCoreApplication.instance() or QCoreApplication([])
    os.environ["IMAGE_TRIAGE_CLI_CULLER_RUNTIME"] = str(bundle / "cli_culler/runtime.json")
    runtime = load_native_runtime()
    verify_native_runtime(runtime)
    assert runtime["comparison_snapshot"]
    with tempfile.TemporaryDirectory(prefix="cli-culler-validation-") as directory:
        work = Path(directory)
        photos = work / "shoot"
        photos.mkdir()
        Image.new("RGB", (300, 200), "black").save(photos / "black.jpg")
        Image.new("RGB", (300, 200), (40, 100, 160)).save(photos / "frame.jpg")
        previous = os.environ.get("PATH")
        os.environ["PATH"] = str(work)  # No Git/exiftool; both runs use the same environment.
        try:
            task = CliCullerRunTask(folders=[photos], python=Path(runtime["python"]),
                                   worker=Path(runtime["worker"]), app_path=Path(runtime["app_path"]),
                                   settings={}, cache_namespace=runtime["namespace"], workers=1,
                                   runtime_manifest=runtime)
            errors, completed = [], []
            task.signals.failed.connect(lambda *args: errors.append(args))
            task.signals.finished.connect(lambda *args: completed.append(args))
            task.run()
            app.processEvents()
            assert completed and not errors, errors
            result = subprocess.run(
                [runtime["python"], "-I", "-X", "utf8", str(bundle / "cli_culler/tools/compare_run.py"),
                 str(bundle / "cli_culler/runtime.json"), str(task.paths.active)],
                cwd=work, capture_output=True, text=True, timeout=120)
            assert result.returncode == 0, result.stdout + result.stderr
            report = Path(result.stdout.strip().splitlines()[-1])
            assert json.loads(report.read_text(encoding="utf-8"))["match"]
        finally:
            if previous is None:
                os.environ.pop("PATH", None)
            else:
                os.environ["PATH"] = previous
    if runtime.get("model_runtime"):
        subprocess.run([runtime["python"], "-I", "-c",
                        "import torch, torchvision, timm, transformers, mediapipe, einops; print(torch.__version__)"],
                       check=True, timeout=120)
    print("Relocated shipped worker and direct CLI matched; no Git or source checkout needed by the runtime.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    check(parser.parse_args().bundle.resolve())
