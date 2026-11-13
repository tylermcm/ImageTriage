from __future__ import annotations

from cx_Freeze import Executable, setup

from freeze_support import (
    AI_FREEZE_EXCLUDES,
    APP_ICON_WINDOWS_PATH,
    QT_WINDOWS_BINARY_EXCLUDES,
    photocraft_bundle_files,
    prepare_ai_build_assets,
    raw_preview_bundle_files,
    read_project_version,
)


freeze_assets = prepare_ai_build_assets()

build_exe_options = {
    "include_msvcr": True,
    # Qt 6.11 uses Windows' System32 ICU forwarders. Never freeze host ICU
    # binaries because tools such as Poppler may expose incompatible versions.
    "bin_excludes": list(QT_WINDOWS_BINARY_EXCLUDES),
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
        "image_triage.upscale_core",
        "image_triage.upscale_service",
        # The QML shell is the application. Its modules are imported inside main(); its .qml files ship as data (freeze_support).
        "image_triage.qml_shell",
        "image_triage.qml_shell.app",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        # The vendored editor engine (formerly the separately-packaged
        # cli_editor/photo_terminal). editor_render.py imports it lazily
        # inside a function, so name it explicitly rather than relying on
        # cx_Freeze's static import scan finding it.
        "image_triage.photo_terminal",
        "image_triage.photo_terminal.adjustments",
        "image_triage.photo_terminal.cli",
        "image_triage.photo_terminal.io",
        "image_triage.photo_terminal.masks",
        "image_triage.photo_terminal.preview",
        "image_triage.photo_terminal.session",
        "onnxruntime",
        "PIL",
        "pip",
        "pip._internal",
        "pillow_heif",
        "uuid",
    ],
    # The editor is part of the program: <install dir>\photocraft\photocraft.exe, found there by shell_actions.bundled_photocraft_executable.
    # The RAW preview worker goes beside ImageTriage.exe, where qml_shell/raw_preview.executable looks for it.
    "include_files": [*freeze_assets.include_files, *photocraft_bundle_files()] + raw_preview_bundle_files(),
    "excludes": [
        # QML is the only desktop UI. Shared helpers remain, but neither
        # retired Widgets application window is shipped in the frozen app.
        "image_triage.window",
        "image_triage.qml_shell.popout",
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

CLEANUP_EXE_NAME = "image_triage_cleanup.exe"
UNINSTALL_CLEANUP_ACTION = "A_IMAGE_TRIAGE_CLEANUP"
# Type 34 starts a program by command line with a Directory entry as its working folder; 64 waits for it and
# carries on whatever it returns, so a clean-up that fails never blocks the uninstall. The deferred (1024) and
# no-impersonation (2048) bits are deliberately absent: an immediate action runs as the user who started the
# uninstall, not as SYSTEM, and the file associations it removes live in that user's HKEY_CURRENT_USER. Another
# user's registry cannot be reached from here (docs/KNOWN_ISSUES.md).
UNINSTALL_CLEANUP_TYPE = 34 + 64
UNINSTALL_CLEANUP_COMMAND = f'"[TARGETDIR]{CLEANUP_EXE_NAME}" --mode uninstall --yes --quiet'
# Only when the product is really being removed: not on a repair, and not while a newer version replaces this one
# (an upgrade removes the old version first, and the registrations must survive it).
UNINSTALL_CLEANUP_CONDITION = 'REMOVE="ALL" AND NOT UPGRADINGPRODUCTCODE'
# After CostFinalize (1000), when [TARGETDIR] is known, and before RemoveFiles (3500), which is what takes the
# clean-up program away. 1450 is where cx_Freeze puts RemoveExistingProducts.
UNINSTALL_CLEANUP_SEQUENCE = 3490

msi_data = {
    "Directory": [
        ("ProgramMenuFolder", "TARGETDIR", "."),
    ],
    # Action, Type, Source, Target
    "CustomAction": [
        (UNINSTALL_CLEANUP_ACTION, UNINSTALL_CLEANUP_TYPE, "TARGETDIR", UNINSTALL_CLEANUP_COMMAND),
    ],
    # Action, Condition, Sequence
    "InstallExecuteSequence": [
        (UNINSTALL_CLEANUP_ACTION, UNINSTALL_CLEANUP_CONDITION, UNINSTALL_CLEANUP_SEQUENCE),
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
        target_name=CLEANUP_EXE_NAME,
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
