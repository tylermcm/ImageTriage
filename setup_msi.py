from __future__ import annotations

from cx_Freeze import Executable, setup

from freeze_support import (
    AI_FREEZE_EXCLUDES,
    APP_ICON_WINDOWS_PATH,
    prepare_ai_build_assets,
    read_project_version,
)


freeze_assets = prepare_ai_build_assets()

build_exe_options = {
    "include_msvcr": True,
    "includes": [
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

bdist_msi_options = {
    "add_to_path": False,
    "all_users": True,
    "initial_target_dir": r"[ProgramFiles64Folder]\ImageTriage",
    "install_icon": str(APP_ICON_WINDOWS_PATH),
    "launch_on_finish": True,
    "upgrade_code": "{D2ED08E1-991F-42CE-94A3-E95CD4D624AB}",
}

executables = [
    Executable(
        script="image_triage/main.py",
        base="gui",
        target_name="ImageTriage.exe",
        icon=str(APP_ICON_WINDOWS_PATH),
    ),
    Executable(
        script="packaging/ai_python_runner.py",
        base=None,
        target_name="ai_python_runner.exe",
        icon=str(APP_ICON_WINDOWS_PATH),
    ),
]

if __name__ == "__main__":
    setup(
        name="ImageTriage",
        version=read_project_version(),
        description="Image Triage desktop installer",
        options={
            "build_exe": build_exe_options,
            "bdist_msi": bdist_msi_options,
        },
        executables=executables,
    )
