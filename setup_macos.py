"""The macOS app bundle: ``python setup_macos.py bdist_mac`` on a Mac. packaging/build_macos.sh does all of it, the disk image included.

One build per architecture: run it with an arm64 Python for Apple Silicon and an x86_64 one (an Intel Mac, or ``arch -x86_64`` under
Rosetta) for Intel Macs. The two cannot be one universal app: most of the wheels the app freezes (NumPy, rawpy, onnxruntime) ship one
architecture each. PySide6 6.12 needs macOS 14, so the bundle says so (LSMinimumSystemVersion).
"""
from __future__ import annotations

from cx_Freeze import Executable, setup

from freeze_support import (
    AI_FREEZE_EXCLUDES,
    APP_FREEZE_EXCLUDES,
    APP_FREEZE_INCLUDES,
    APP_ICON_MAC_PATH,
    licence_bundle_files,
    photocraft_bundle_files,
    prepare_ai_build_assets,
    raw_preview_bundle_files,
    read_project_version,
)

VERSION = read_project_version()
BUNDLE_ID = "io.github.tylermcm.ImageTriage"
MINIMUM_MACOS = "14.0"          # PySide6 6.12's wheels are built for macOS 14 (both architectures)

freeze_assets = prepare_ai_build_assets()

build_exe_options = {
    "includes": list(APP_FREEZE_INCLUDES),
    # The editor (<bundle>/Contents/Resources/photocraft/photocraft, reached through the Contents/MacOS/photocraft link bdist_mac makes for
    # every folder) runs in a window of its own on macOS (--hosted-window). The RAW preview worker lands in Contents/Resources, where
    # qml_shell/raw_preview.executable looks for it on macOS.
    "include_files": [*freeze_assets.include_files, *photocraft_bundle_files()] + raw_preview_bundle_files() + licence_bundle_files(),
    "excludes": [*APP_FREEZE_EXCLUDES, *AI_FREEZE_EXCLUDES],
}

bdist_mac_options = {
    "bundle_name": "Image Triage",
    "iconfile": str(APP_ICON_MAC_PATH),
    "plist_items": [
        ("CFBundleName", "Image Triage"),
        ("CFBundleDisplayName", "Image Triage"),
        ("CFBundleIdentifier", BUNDLE_ID),
        ("CFBundleShortVersionString", VERSION),
        ("CFBundleVersion", VERSION),
        ("LSMinimumSystemVersion", MINIMUM_MACOS),
        ("LSApplicationCategoryType", "public.app-category.photography"),
        ("NSHighResolutionCapable", True),
        # Dark mode follows the system (Qt draws its own window chrome in both).
        ("NSRequiresAquaSystemAppearance", False),
        # What macOS shows when it asks the person to let the app into a folder it guards (asked once per folder, and only when the
        # person opens one: the Library does not look inside them on its own).
        ("NSDesktopFolderUsageDescription", "Image Triage shows and culls the photos you keep on your Desktop."),
        ("NSDocumentsFolderUsageDescription", "Image Triage shows and culls the photos you keep in Documents."),
        ("NSDownloadsFolderUsageDescription", "Image Triage shows and culls the photos you keep in Downloads."),
        ("NSRemovableVolumesUsageDescription", "Image Triage shows and culls the photos on your memory cards and external drives."),
        ("NSNetworkVolumesUsageDescription", "Image Triage shows and culls the photos on your network drives."),
    ],
}

executables = [
    Executable(script="image_triage/main.py", base="gui", target_name="ImageTriage", icon=str(APP_ICON_MAC_PATH)),
    Executable(script="packaging/ai_python_runner.py", base=None, target_name="ai_python_runner"),
    Executable(script="packaging/ai_runtime_installer.py", base=None, target_name="ai_runtime_installer"),
    Executable(script="packaging/image_triage_cleanup.py", base=None, target_name="image_triage_cleanup"),
]

if __name__ == "__main__":
    setup(
        name="ImageTriage",
        version=VERSION,
        description="Image Triage",
        options={
            "build_exe": build_exe_options,
            "bdist_mac": bdist_mac_options,
        },
        executables=executables,
    )
