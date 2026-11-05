"""Assemble a QML validation app with an isolated CPU or CUDA runtime."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import stat

ROOT = Path(__file__).resolve().parents[1]


def build(app: Path, runtime: Path, output: Path):
    if (app / "ImageTriage.exe").is_file() and not (app / "lib/image_triage/qml_shell/qml/Main.qml").is_file():
        raise ValueError("The validation app must contain the QML interface; refusing a retired Widgets-only build")
    manifest = json.loads((runtime / "runtime.json").read_text(encoding="utf-8"))
    if any(Path(manifest[key]).is_absolute() for key in ("python", "worker", "app_path")):
        raise ValueError("Build the runtime with --portable before bundling it")
    if output.exists():
        raise ValueError("Choose a new output directory; existing builds are preserved")
    shutil.copytree(app, output)
    target = output / "cli_culler"
    shutil.copytree(runtime, target)
    manifest["comparison_snapshot"] = True
    (target / "runtime.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    tools = target / "tools"
    tools.mkdir(exist_ok=True)
    shutil.copy2(ROOT / "image_triage/cli_culler_comparison.py", tools / "compare_run.py")
    (output / "CLI-Culler-settings.json").write_text(
        json.dumps(manifest["default_settings"], indent=2), encoding="utf-8")
    windows = (output / "ImageTriage.exe").exists()
    bootstrap = "import sys,runpy;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('aiculler.cli',run_name='__main__')"
    if windows:
        scripts = {
            "Start-ImageTriage-Validation.cmd": '@echo off\nsetlocal\nset "IMAGE_TRIAGE_CLI_CULLER_VALIDATION=1"\nset "IMAGE_TRIAGE_CLI_CULLER_RUNTIME=%~dp0cli_culler\\runtime.json"\nset "IMAGE_TRIAGE_NO_EDITOR_PRESTART=1"\n"%~dp0ImageTriage.exe" %*\n',
            "AI-Culler.cmd": '@echo off\n"%~dp0cli_culler\\python\\python.exe" -I -X utf8 -c "' + bootstrap + '" "%~dp0cli_culler\\worker" %*\n',
            "Compare-CLI-Culler.cmd": '@echo off\n"%~dp0cli_culler\\python\\python.exe" -I -X utf8 "%~dp0cli_culler\\tools\\compare_run.py" "%~dp0cli_culler\\runtime.json" %*\nset "result=%ERRORLEVEL%"\necho Comparison finished with exit code %result%.\npause\nexit /b %result%\n',
        }
    else:
        prefix = '#!/bin/sh\nset -eu\nroot=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\n'
        scripts = {
            "Start-ImageTriage-Validation": prefix + 'export IMAGE_TRIAGE_CLI_CULLER_VALIDATION=1\nexport IMAGE_TRIAGE_CLI_CULLER_RUNTIME="$root/cli_culler/runtime.json"\nexport IMAGE_TRIAGE_NO_EDITOR_PRESTART=1\nexec "$root/ImageTriage" "$@"\n',
            "AI-Culler": prefix + 'exec "$root/cli_culler/python/bin/python" -I -X utf8 -c "' + bootstrap + '" "$root/cli_culler/worker" "$@"\n',
            "Compare-CLI-Culler": prefix + 'exec "$root/cli_culler/python/bin/python" -I -X utf8 "$root/cli_culler/tools/compare_run.py" "$root/cli_culler/runtime.json" "$@"\n',
        }
    for name, text in scripts.items():
        path = output / name
        path.write_text(text.replace("\n", "\r\n") if windows else text, encoding="utf-8", newline="")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    (output / "VALIDATION-README.txt").write_text(
        "ImageTriage / CLI-Culler QML validation build\n\n"
        "1. Extract the entire build. Launch Start-ImageTriage-Validation.\n"
        "2. Open a representative shoot and Analyze. Missing requested models are reported in the result summary. "
        "Models use the app's existing Set Up AI installation; this package does not download weights.\n"
        "3. Inspect results, then compare BEFORE Apply or moving/editing photos. "
        "Pass SHOOT/.image_triage_ai/artifacts/cli_culler/active.json to Compare-CLI-Culler "
        "(on Windows, drag active.json onto Compare-CLI-Culler.cmd).\n"
        "The comparison restores independent copies of the exact starting score/preview caches, "
        "uses the saved run settings, invokes the unchanged ai-culler CLI, and writes comparison.json "
        "alongside a separate CLI decisions.json. All fields except timings and all index arrays must match.\n\n"
        "AI-Culler --help exposes the direct engine CLI. CLI-Culler-settings.json contains the initial profile; "
        "the comparison automatically uses any settings changed in ImageTriage.\n\n"
        "This portable validation app does not replace the installed ImageTriage. "
        "Its launcher enables native Analyze for this process; the normal app default is unchanged. "
        "GPU packaging is deferred. PhotoCraft is not bundled or prestarted.\n",
        encoding="utf-8")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path, required=True)
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    print(build(args.app_dir.resolve(), args.runtime_dir.resolve(), args.out_dir.resolve()))
