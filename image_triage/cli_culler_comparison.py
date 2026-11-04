"""Standalone CLI comparison for a validation run; no GUI or host engine imports."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import uuid


def snapshot_start_cache(cache: Path, destination: Path, cancelled=lambda: None):
    destination.mkdir(parents=True)
    database = cache / "scores.sqlite"
    if database.exists():
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as source:
            with sqlite3.connect(destination / "scores.sqlite") as target:
                source.backup(target, pages=256, progress=lambda *_: cancelled())
    previews = cache / "previews"
    if previews.exists():
        for source in previews.rglob("*"):
            cancelled()
            if source.is_file():
                target = destination / "previews" / source.relative_to(previews)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    cancelled()
    (destination / "complete.json").write_text('{"schema":"cli-culler-start-cache/1"}', encoding="utf-8")


def differences(left, right, prefix=""):
    if type(left) is not type(right):
        return [prefix]
    if isinstance(left, dict):
        found = []
        for key in sorted(left.keys() | right.keys()):
            path = f"{prefix}/{key}"
            if key not in left or key not in right:
                found.append(path)
            else:
                found.extend(differences(left[key], right[key], path))
        return found
    if isinstance(left, list):
        if len(left) != len(right):
            return [prefix]
        return [path for i, (a, b) in enumerate(zip(left, right))
                for path in differences(a, b, f"{prefix}/{i}")]
    return [] if left == right else [prefix]


def compare_run(runtime_path: Path, active_path: Path) -> Path:
    runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    for key in ("python", "worker", "app_path"):
        value = Path(runtime[key])
        runtime[key] = str(value if value.is_absolute() else runtime_path.resolve().parent / value)
    active = json.loads(active_path.read_text(encoding="utf-8"))
    if active.get("schema") != "image-triage-culler-run/1":
        raise ValueError("Select the active.json saved by this validation build")
    for output in active["outputs"].values():
        if output:
            with Path(output["path"]).open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != output["sha256"]:
                    raise ValueError("Saved ImageTriage outputs have changed")
    decisions = Path(active["outputs"]["decisions"]["path"])
    run_root = decisions.parent.parent
    request = json.loads((run_root / "request.json").read_text(encoding="utf-8"))
    starting = run_root / "comparison-start"
    if not (starting / "complete.json").is_file():
        raise ValueError("This run has no starting-cache snapshot; run Analyze using the validation launcher first")
    output = run_root / ("cli-comparison-" + uuid.uuid4().hex)
    output.mkdir()
    for source in starting.iterdir():
        if source.is_dir():
            shutil.copytree(source, output / source.name)
        elif source.name != "complete.json":
            shutil.copy2(source, output / source.name)
    settings = output / "settings.json"
    settings.write_text(json.dumps(request["settings"], ensure_ascii=False), encoding="utf-8")
    command = [runtime["python"], "-I", "-X", "utf8", "-m", "aiculler.cli", "--db", str(output / "scores.sqlite"),
               "cull", *request["folders"], "--settings", str(settings),
               "--models", runtime["app_path"], "--device", request["device"],
               "--cache", str(output / "previews"), "--out", str(output / "decisions.json"), "--quiet"]
    if request.get("workers") is not None:
        command.extend(["--workers", str(request["workers"])])
    with (output / "cli.log").open("w", encoding="utf-8") as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True)
    actual = json.loads(decisions.read_text(encoding="utf-8"))
    expected = json.loads((output / "decisions.json").read_text(encoding="utf-8"))
    actual.pop("timings", None)
    expected.pop("timings", None)
    found = differences(actual, expected)
    gui_index = active["outputs"].get("index")
    cli_index = output / "index.npz"
    if bool(gui_index) != cli_index.exists():
        found.append("/index/presence")
    elif gui_index:
        import numpy as np
        with np.load(gui_index["path"], allow_pickle=False) as a, np.load(cli_index, allow_pickle=False) as b:
            if set(a.files) != set(b.files):
                found.append("/index/arrays")
            else:
                for key in a.files:
                    equal_nan = a[key].dtype.kind in "fc"
                    if not np.array_equal(a[key], b[key], equal_nan=equal_nan):
                        found.append("/index/" + key)
    report = output / "comparison.json"
    report.write_text(json.dumps({"match": not found, "differences": found,
                                  "image_triage": str(decisions),
                                  "cli": str(output / "decisions.json"),
                                  "starting_cache": str(starting),
                                  "ignored_fields": ["timings"]}, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runtime", type=Path)
    parser.add_argument("active", type=Path, help="Shoot/.image_triage_ai/artifacts/cli_culler/active.json")
    args = parser.parse_args()
    result = compare_run(args.runtime, args.active)
    print(result)
    raise SystemExit(0 if json.loads(result.read_text(encoding="utf-8"))["match"] else 1)
