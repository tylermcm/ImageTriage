from __future__ import annotations

import os
import sysconfig
from pathlib import Path

from cx_Freeze import Executable, setup


ROOT = Path(__file__).resolve().parent


def _discover_ai_source_root() -> Path:
    for env_name in ("IMAGE_TRIAGE_AI_SOURCE", "AICULLING_ENGINE_ROOT"):
        raw_value = os.environ.get(env_name)
        if raw_value:
            candidate = Path(raw_value).expanduser()
            if candidate.exists():
                return candidate.resolve()

    candidates = [
        ROOT / "AICullingPipeline",
        Path.home() / "Documents" / "GitHub" / "AICullingPipeline",
        Path.home() / "GitHub" / "AICullingPipeline",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve()


def _discover_linux_dynload_root() -> Path:
    destshared = sysconfig.get_config_var("DESTSHARED")
    if destshared:
        candidate = Path(destshared).expanduser()
        if candidate.exists():
            return candidate.resolve()

    candidate = Path(sysconfig.get_paths()["platstdlib"]) / "lib-dynload"
    return candidate.expanduser().resolve()


os.environ.setdefault("IMAGE_TRIAGE_AI_SOURCE", str(_discover_ai_source_root()))
os.environ.setdefault("IMAGE_TRIAGE_AI_SITE_PACKAGES", sysconfig.get_paths()["purelib"])
os.environ.setdefault("IMAGE_TRIAGE_AI_STDLIB", sysconfig.get_paths()["stdlib"])
os.environ.setdefault("IMAGE_TRIAGE_AI_DLLS", str(_discover_linux_dynload_root()))

from setup_msi import AI_DLLS_STAGE_ROOT, AI_FREEZE_EXCLUDES, AI_SITE_PACKAGES_STAGE_ROOT, AI_STAGE_ROOT, AI_STDLIB_STAGE_ROOT, _read_project_version  # noqa: E402


APP_ICON_PATH = ROOT / "build_assets" / "icons" / "image_triage.png"

build_exe_options = {
    "includes": [
        "uuid",
    ],
    "include_files": [
        (str(AI_STAGE_ROOT.parent), "ai_runtime"),
        (str(AI_SITE_PACKAGES_STAGE_ROOT), "ai_site_packages"),
        (str(AI_STDLIB_STAGE_ROOT), "ai_stdlib"),
        (str(AI_DLLS_STAGE_ROOT), "lib"),
    ],
    "excludes": [
        "tkinter",
        "test",
        "tests",
        "unittest",
        "benchmarks",
        "pip",
        "setuptools",
        "wheel",
        *AI_FREEZE_EXCLUDES,
    ],
}

bdist_appimage_options = {
    "target_name": "ImageTriage",
    "target_version": _read_project_version(),
}

executables = [
    Executable(
        script="image_triage/main.py",
        base="gui",
        target_name="ImageTriage",
        icon=str(APP_ICON_PATH),
    ),
    Executable(
        script="packaging/ai_python_runner.py",
        base=None,
        target_name="ai_python_runner",
        icon=str(APP_ICON_PATH),
    ),
]

if __name__ == "__main__":
    setup(
        name="ImageTriage",
        version=_read_project_version(),
        description="Image Triage desktop installer",
        options={
            "build_exe": build_exe_options,
            "bdist_appimage": bdist_appimage_options,
        },
        executables=executables,
    )
