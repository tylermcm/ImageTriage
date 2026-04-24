from __future__ import annotations

from cx_Freeze import Executable, setup

from freeze_support import (
    AI_FREEZE_EXCLUDES,
    APP_ICON_LINUX_PATH,
    prepare_ai_build_assets,
    read_project_version,
)


freeze_assets = prepare_ai_build_assets()

build_exe_options = {
    "includes": [
        "astropy",
        "astropy.io.fits",
        "astropy.visualization",
        "uuid",
    ],
    "include_files": freeze_assets.include_files,
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
    "target_version": read_project_version(),
}

executables = [
    Executable(
        script="image_triage/main.py",
        base="gui",
        target_name="ImageTriage",
        icon=str(APP_ICON_LINUX_PATH),
    ),
    Executable(
        script="packaging/ai_python_runner.py",
        base=None,
        target_name="ai_python_runner",
        icon=str(APP_ICON_LINUX_PATH),
    ),
]

if __name__ == "__main__":
    setup(
        name="ImageTriage",
        version=read_project_version(),
        description="Image Triage desktop installer",
        options={
            "build_exe": build_exe_options,
            "bdist_appimage": bdist_appimage_options,
        },
        executables=executables,
    )
