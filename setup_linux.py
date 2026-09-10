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
        # The capability probe runs inside the frozen ai_runtime_installer.exe
        # and is imported lazily, so name it (and the manifest it reads)
        # explicitly rather than relying on static import analysis.
        "aiculler.topiq_onnx",
        "image_triage.ai_manifest",
        "image_triage.ai_paths",
        "image_triage.ai_probe",
        "image_triage.birefnet_worker",
        "image_triage.depth_worker",
        "image_triage.oneformer_worker",
        "image_triage.sam_worker",
        "onnxruntime",
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
    Executable(
        script="packaging/ai_runtime_installer.py",
        base=None,
        target_name="ai_runtime_installer",
        icon=str(APP_ICON_LINUX_PATH),
    ),
    Executable(
        script="packaging/image_triage_cleanup.py",
        base=None,
        target_name="image_triage_cleanup",
        icon=str(APP_ICON_LINUX_PATH),
    ),
]

if __name__ == "__main__":
    setup(
        name="ImageTriage",
        version=read_project_version(),
        description="Image Triage",
        options={
            "build_exe": build_exe_options,
            "bdist_appimage": bdist_appimage_options,
        },
        executables=executables,
    )
