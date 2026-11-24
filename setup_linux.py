from __future__ import annotations

from cx_Freeze import Executable, setup

from freeze_support import (
    AI_FREEZE_EXCLUDES,
    APP_FREEZE_EXCLUDES,
    APP_FREEZE_INCLUDES,
    APP_ICON_LINUX_PATH,
    licence_bundle_files,
    photocraft_bundle_files,
    prepare_ai_build_assets,
    raw_preview_bundle_files,
    read_project_version,
)


freeze_assets = prepare_ai_build_assets()

build_exe_options = {
    "includes": list(APP_FREEZE_INCLUDES),
    # The editor is part of the program: <AppDir>/photocraft/photocraft, found there by shell_actions.bundled_photocraft_executable. It
    # runs in a window of its own on Linux (`--hosted-window`). The RAW preview worker goes beside ImageTriage, where
    # qml_shell/raw_preview.executable looks for it.
    "include_files": [*freeze_assets.include_files, *photocraft_bundle_files()] + raw_preview_bundle_files() + licence_bundle_files(),
    "excludes": [*APP_FREEZE_EXCLUDES, *AI_FREEZE_EXCLUDES],
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

# The menu entry inside the AppImage, which AppImage integrators (Gear Lever, AppImageLauncher) install. cx_Freeze writes its own from a
# template ("ImageTriage", filed under Development, an icon path the desktop cannot resolve); this one replaces it. StartupWMClass
# matches the app's desktop file name (QGuiApplication.setDesktopFileName in qml_shell/app.py), which is how Wayland ties the window to it.
DESKTOP_ENTRY = f"""[Desktop Entry]
Type=Application
Name=Image Triage
GenericName=Photo Culling
Comment=Browse, cull, and score large image folders
Exec=ImageTriage %F
Icon=ImageTriage
Categories=Graphics;Photography;
Keywords=photo;raw;cull;triage;
Terminal=false
StartupNotify=true
StartupWMClass=image-triage
X-AppImage-Name=ImageTriage
X-AppImage-Version={read_project_version()}
"""


def bdist_appimage_with_desktop_entry():
    from cx_Freeze.command.bdist_appimage import bdist_appimage

    class BdistAppImage(bdist_appimage):
        def save_as_file(self, data, outfile, mode="r"):
            if str(outfile).endswith(".desktop"):
                data = DESKTOP_ENTRY
            return super().save_as_file(data, outfile, mode)

    return BdistAppImage


if __name__ == "__main__":
    setup(
        cmdclass={"bdist_appimage": bdist_appimage_with_desktop_entry()},
        name="ImageTriage",
        version=read_project_version(),
        description="Image Triage",
        options={
            "build_exe": build_exe_options,
            "bdist_appimage": bdist_appimage_options,
        },
        executables=executables,
    )
