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
        "astropy",
        "astropy.io.fits",
        "astropy.visualization",
        "PIL",
        "pip",
        "pip._internal",
        "pillow_heif",
        "uuid",
    ],
    "include_files": freeze_assets.include_files,
    "excludes": [
        "tkinter",
        "test",
        "tests",
        "unittest",
        "benchmarks",
        "setuptools",
        "wheel",
        *AI_FREEZE_EXCLUDES,
    ],
}

msi_data = {
    "Directory": [
        ("ProgramMenuFolder", "TARGETDIR", "."),
    ],
}

bdist_msi_options = {
    "add_to_path": False,
    "all_users": True,
    "data": msi_data,
    "initial_target_dir": r"[ProgramFiles64Folder]\ImageTriage",
    "install_icon": str(APP_ICON_WINDOWS_PATH),
    "launch_on_finish": False,
    "product_name": "Image Triage",
    "upgrade_code": "{2C50910A-F1EA-4C94-A730-2D39264677E1}",
}

executables = [
    Executable(
        script="image_triage/main.py",
        base="gui",
        target_name="ImageTriage.exe",
        icon=str(APP_ICON_WINDOWS_PATH),
        shortcut_name="Image Triage",
        shortcut_dir="ProgramMenuFolder",
    ),
    Executable(
        script="packaging/ai_python_runner.py",
        base=None,
        target_name="ai_python_runner.exe",
        icon=str(APP_ICON_WINDOWS_PATH),
    ),
    Executable(
        script="packaging/ai_runtime_installer.py",
        base=None,
        target_name="ai_runtime_installer.exe",
        icon=str(APP_ICON_WINDOWS_PATH),
    ),
    Executable(
        script="packaging/image_triage_cleanup.py",
        base=None,
        target_name="image_triage_cleanup.exe",
        icon=str(APP_ICON_WINDOWS_PATH),
    ),
]

if __name__ == "__main__":
    setup(
        name="ImageTriage",
        version=read_project_version(),
        description="Image Triage",
        options={
            "build_exe": build_exe_options,
            "bdist_msi": bdist_msi_options,
        },
        executables=executables,
    )
