"""macOS, Apple Silicon and Intel: what can be checked without a Mac. The Mac builds themselves are made and tried on a Mac (docs/macos.md).

Intel Macs are held to what has wheels there for Python 3.13: no PyTorch (its last Intel macOS build, 2.2, predates 3.13), no MediaPipe,
ONNX Runtime 1.23.2. Apple Silicon has the whole runtime.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from image_triage import ai_runtime_packages as packages

ROOT = Path(__file__).resolve().parents[1]
LOCKS = ROOT / packages.LOCK_DIRECTORY


def _names(lock: Path) -> dict[str, str]:
    rows = [line.rstrip("\\").strip() for line in lock.read_text(encoding="utf-8").splitlines() if "==" in line and not line.startswith("#")]
    return {row.split("==", 1)[0].lower(): row.split("==", 1)[1] for row in rows}


@pytest.mark.parametrize("platform, torch", [("darwin-arm64", True), ("darwin-amd64", False), ("windows-amd64", True), ("linux-amd64", True)])
def test_pytorch_is_offered_where_it_has_a_build(platform, torch):
    assert packages.torch_supported(platform) is torch


def test_an_intel_mac_gets_the_compact_runtime_with_the_onnx_runtime_that_has_an_intel_build():
    requirements = packages._ai_runtime_pip_requirements_for_variant("cpu", include_torch=True, platform="darwin-amd64")
    names = {packages._distribution_name(r): r for r in requirements}
    assert "torch" not in names and "torchvision" not in names, "no PyTorch build for Intel Macs"
    assert "mediapipe" not in names, "no MediaPipe build for Intel Macs"
    assert names["onnxruntime"] == "onnxruntime==1.23.2"
    apple = {packages._distribution_name(r) for r in packages._ai_runtime_pip_requirements_for_variant("cpu", platform="darwin-arm64")}
    assert {"torch", "torchvision", "mediapipe", "onnxruntime"} <= apple


def test_an_intel_mac_does_not_check_for_what_it_cannot_have():
    with patch.object(packages, "runtime_platform", return_value="darwin-amd64"):
        assert "mediapipe" not in packages._required_module_names(include_torch=False)
        assert packages.unavailable_modules() == frozenset({"mediapipe"})
    with patch.object(packages, "runtime_platform", return_value="darwin-arm64"):
        assert "mediapipe" in packages._required_module_names(include_torch=False)


def test_an_install_on_an_intel_mac_never_asks_for_pytorch(tmp_path):
    with patch.object(packages, "runtime_platform", return_value="darwin-amd64"), \
            patch.object(packages, "resolve_lock_file", return_value=None):
        args = packages.build_ai_runtime_pip_install_args(variant="cpu", target_dir=tmp_path, include_torch=True)
    assert not [a for a in args if a.startswith(("torch==", "torchvision==", "mediapipe"))]
    assert "onnxruntime==1.23.2" in args


@pytest.mark.parametrize("lock, torch, onnx", [
    ("py313-darwin-arm64-cpu.txt", True, "1.27.0"),
    ("py313-darwin-arm64-cpu-base.txt", False, "1.27.0"),
    ("py313-darwin-amd64-cpu-base.txt", False, "1.23.2"),
])
def test_the_mac_locks_ship_and_hold_the_validated_versions(lock, torch, onnx):
    path = LOCKS / lock
    assert path.is_file(), path
    names = _names(path)
    text = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(text):
        if "==" in line and not line.startswith("#"):
            assert "--hash=sha256:" in text[index + 1], line
    assert ("torch" in names) is torch
    assert names["onnxruntime"] == onnx
    assert "onnxruntime-gpu" not in names and "opencv-python" not in names
    # every package Windows also installs is the version Windows was validated with (torch without its +cpu)
    windows = _names(LOCKS / lock.replace("darwin-arm64", "windows-amd64").replace("darwin-amd64", "windows-amd64"))
    differing = {name: (version, windows[name]) for name, version in names.items()
                 if name in windows and name != "onnxruntime" and version != windows[name].split("+", 1)[0]}
    assert differing == {}


def test_there_is_no_cuda_lock_for_a_mac_and_no_pytorch_lock_for_an_intel_one():
    assert not list(LOCKS.glob("py313-darwin-*-gpu*.txt"))
    assert not (LOCKS / "py313-darwin-amd64-cpu.txt").exists()


def test_the_ai_center_counts_a_runtime_without_pytorch_as_installed_on_an_intel_mac():
    from image_triage.qml_shell import ai_center

    with patch.object(packages, "runtime_platform", return_value="darwin-amd64"):
        assert ai_center._torch_supported() is False
    with patch.object(packages, "runtime_platform", return_value="darwin-arm64"):
        assert ai_center._torch_supported() is True
    assert "Intel Mac" in ai_center.INTEL_MAC_LIMITS


# ---- the platform spots -----------------------------------------------------------------------------------------------------------

MAC_MOUNT = """\
/dev/disk3s1s1 on / (apfs, sealed, local, read-only, journaled)
devfs on /dev (devfs, local, nobrowse)
/dev/disk3s5 on /System/Volumes/Data (apfs, local, journaled, nobrowse)
map auto_home on /System/Volumes/Data/home (autofs, automounted, nobrowse)
/dev/disk4s1 on /Volumes/NIKON Z 6 (msdos, local, nodev, nosuid, noowners)
/dev/disk5s2 on /Volumes/Archive SSD (apfs, local, nodev, nosuid, journaled, noowners)
//tyler@nas._smb._tcp.local/photos on /Volumes/photos (smbfs, nodev, nosuid, mounted by tyler)
"""


def test_the_macos_mount_table_is_read_from_mount():
    from image_triage import path_policy

    mounts = path_policy.parse_macos_mount(MAC_MOUNT)
    assert ("/Volumes/NIKON Z 6", "msdos", "/dev/disk4s1") in mounts                # a name with spaces
    assert ("/Volumes/photos", "smbfs", "//tyler@nas._smb._tcp.local/photos") in mounts


def test_the_drives_list_on_a_mac_is_root_and_the_volumes(monkeypatch):
    from image_triage import path_policy

    mounts = path_policy.parse_macos_mount(MAC_MOUNT)
    monkeypatch.setattr(path_policy, "classify_mount", lambda mount, **_: path_policy.DRIVE_REMOTE if mount[1] == "smbfs"
                        else path_policy.DRIVE_REMOVABLE)
    roots = path_policy.posix_drive_roots(mounts)
    assert [path for path, _kind in roots] == ["/", "/Volumes/NIKON Z 6/", "/Volumes/Archive SSD/", "/Volumes/photos/"]
    assert dict(roots)["/Volumes/photos/"] == path_policy.DRIVE_REMOTE


def test_a_mac_share_is_a_network_file_system_and_volumes_are_removable():
    from image_triage import path_policy

    assert {"smbfs", "afpfs", "webdav", "nfs"} <= path_policy._NETWORK_FILESYSTEMS
    assert "/Volumes/" in path_policy._REMOVABLE_MOUNT_PARENTS


def test_reveal_selects_the_file_in_finder(monkeypatch):
    from image_triage import shell_actions

    started = []
    monkeypatch.setattr(shell_actions.os, "name", "posix")
    monkeypatch.setattr(shell_actions.sys, "platform", "darwin")
    monkeypatch.setattr(shell_actions.subprocess, "Popen", lambda command, **_: started.append(command))
    shell_actions.reveal_in_file_explorer("/Users/tyler/Pictures/a.nef")
    assert started[-1][:2] == ["open", "-R"] and started[-1][2].endswith("a.nef")


def test_names_on_a_mac():
    import importlib

    from image_triage import platform_names

    with patch("sys.platform", "darwin"):
        names = importlib.reload(platform_names)
        assert names.FILE_MANAGER == "Finder" and names.TRASH == "Trash"
        assert names.show_in_file_manager() == "Show in Finder"
    importlib.reload(platform_names)


# ---- the app bundle and the builds ------------------------------------------------------------------------------------------------

def test_the_mac_bundle_freezes_what_the_other_packages_do_for_macos_14():
    text = (ROOT / "setup_macos.py").read_text(encoding="utf-8")
    assert '"includes": list(APP_FREEZE_INCLUDES)' in text and '"excludes": [*APP_FREEZE_EXCLUDES, *AI_FREEZE_EXCLUDES]' in text
    assert "photocraft_bundle_files()" in text and "raw_preview_bundle_files()" in text
    assert '("LSMinimumSystemVersion", MINIMUM_MACOS)' in text and 'MINIMUM_MACOS = "14.0"' in text
    import freeze_support

    assert freeze_support.APP_ICON_MAC_PATH.is_file() and freeze_support.APP_ICON_MAC_PATH.suffix == ".icns"


def test_every_mac_build_targets_the_same_oldest_macos():
    # PySide6 6.12 needs macOS 14: the bundle, the worker, PocketDrop and the AI locks all say 14
    assert "MACOSX_DEPLOYMENT_TARGET=14.0" in (ROOT / "packaging" / "build_macos.sh").read_text(encoding="utf-8")
    assert "MACOSX_DEPLOYMENT_TARGET:-14.0" in (ROOT / "scripts" / "build_raw_preview.sh").read_text(encoding="utf-8")
    assert "-mmacosx-version-min=14.0" in (ROOT / "native" / "pocketdrop" / "build_macos.sh").read_text(encoding="utf-8")
    lock_script = (ROOT / "scripts" / "refresh_ai_runtime_lock.py").read_text(encoding="utf-8")
    assert '"macosx_14_0_arm64"' in lock_script and '"macosx_14_0_x86_64"' in lock_script


def test_the_mac_build_script_builds_both_architectures_and_signs():
    text = (ROOT / "packaging" / "build_macos.sh").read_text(encoding="utf-8")
    assert "aarch64-apple-darwin" in text and "x86_64-apple-darwin" in text
    assert 'arch "-$ARCH"' in text, "the Intel build runs its Python under Rosetta on an Apple Silicon Mac"
    assert '"${RUN[@]}"' not in text, "macOS's bash 3.2 refuses an empty array under set -u"
    assert "--codesign-identity=-" in text and "macos.entitlements" in text
    entitlements = (ROOT / "packaging" / "macos.entitlements").read_text(encoding="utf-8")
    for key in ("allow-jit", "allow-unsigned-executable-memory", "disable-library-validation"):
        assert f"com.apple.security.cs.{key}" in entitlements


def test_the_raw_worker_is_found_in_the_bundles_resources(tmp_path, monkeypatch):
    import sys

    from image_triage.qml_shell import raw_preview

    macos = tmp_path / "Image Triage.app" / "Contents" / "MacOS"
    resources = macos.parent / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir()
    (macos / "ImageTriage").write_bytes(b"app")
    (resources / "image-triage-raw-preview").write_bytes(b"worker")
    monkeypatch.delenv("IMAGE_TRIAGE_RAW_PREVIEW_EXE", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(macos / "ImageTriage"))
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(raw_preview, "EXECUTABLE_NAME", "image-triage-raw-preview")
    assert raw_preview.executable() == str((resources / "image-triage-raw-preview").resolve())


def test_pocketdrop_has_a_mac_build_and_the_bundle_takes_it(monkeypatch):
    import freeze_support

    from image_triage.pocketdrop import _bridge

    assert (ROOT / "native" / "pocketdrop" / "build_macos.sh").is_file()
    assert "src/mac/platform_mac.mm" in (ROOT / "native" / "pocketdrop" / "build_macos.sh").read_text(encoding="utf-8")
    monkeypatch.setattr(freeze_support.sys, "platform", "darwin")
    with pytest.raises(FileNotFoundError, match="build_macos.sh"):     # not built here: the bundle stops and says how to build it
        freeze_support._pocketdrop_include_files()
    assert _bridge.BUILD_SCRIPT in {"build_windows.bat", "build_linux.sh", "build_macos.sh"}


def test_on_macos_the_window_keeps_the_systems_own_buttons():
    # Mac users expect the red, yellow and green buttons, drawn by macOS: the window reaches under a title bar with no background
    # instead of having none, the bar leaves them room and draws no minimize, maximize and close of its own, and macOS resizes.
    qml = ROOT / "image_triage" / "qml_shell" / "qml"
    main = (qml / "Main.qml").read_text(encoding="utf-8")
    assert "Qt.ExpandedClientAreaHint | Qt.NoTitleBarBackgroundHint" in main
    assert "(Qt.Window | Qt.FramelessWindowHint)" in main, "Windows and Linux keep the frameless window"
    assert "WindowEdges { z: 1000; anchors.fill: parent; visible: !win.macTitleBar }" in main
    bar = (qml / "TopBar.qml").read_text(encoding="utf-8")
    assert "visible: !top.macButtons" in bar
    # the logo moves to the right-hand corner, after the theme and inspector buttons; the menus start after macOS's buttons
    assert "x: top.macButtons ? top.width - width - 6 : 0" in bar
    assert "anchors.right: top.macButtons ? brand.left : winButtons.left" in bar
    assert "x: top.macButtons ? top.nativeButtonsW + 4" in bar
    assert "win.visibility !== Window.FullScreen ? 74 : 0" in bar, "no room kept in full screen, where macOS hides them"


def test_the_library_does_not_look_inside_the_folders_macos_guards(monkeypatch, tmp_path):
    # Looking inside Desktop, Documents or Downloads makes macOS ask for access: the tree does not do it just to draw an arrow.
    from image_triage.qml_shell import library_nav

    monkeypatch.setattr(library_nav.sys, "platform", "darwin")
    monkeypatch.setattr(library_nav.Path, "home", classmethod(lambda cls: tmp_path))
    for name in ("Desktop", "Documents", "Downloads"):
        assert library_nav._guarded(str(tmp_path / name))
    assert not library_nav._guarded(str(tmp_path / "Pictures"))
    assert not library_nav._guarded(str(tmp_path / "Desktop" / "100 Photos")), "inside one the person opened, nothing more is asked"
    monkeypatch.setattr(library_nav.sys, "platform", "win32")
    assert not library_nav._guarded(str(tmp_path / "Desktop"))


def test_on_macos_the_menus_are_in_the_menu_bar():
    # Mac apps keep their menus at the top of the screen: the same rows and commands as the window's own menus, which macOS leaves out.
    qml = ROOT / "image_triage" / "qml_shell" / "qml"
    assert "MacMenuBar 1.0 MacMenuBar.qml" in (qml / "qmldir").read_text(encoding="utf-8")
    main = (qml / "Main.qml").read_text(encoding="utf-8")
    assert "Instantiator { active: win.macTitleBar; delegate: MacMenuBar { source: topMenus } }" in main
    bar = (qml / "MacMenuBar.qml").read_text(encoding="utf-8")
    for name in ("file", "review", "view", "ai"):
        assert f'bar.source.build("{name}")' in bar
    assert "bar.source.run(row.id)" in bar and "reference.open" in bar
    assert "shortcut" not in bar.split("Platform.MenuBar {", 1)[1], "no key bindings on the rows: W, K and R would leave text fields"
    assert "visible: top.width > 900 && !top.macButtons" in (qml / "TopBar.qml").read_text(encoding="utf-8")


def test_the_mac_bundle_explains_the_folder_prompts_and_builds_can_keep_one_signature():
    text = (ROOT / "setup_macos.py").read_text(encoding="utf-8")
    for key in ("NSDesktopFolderUsageDescription", "NSDocumentsFolderUsageDescription", "NSDownloadsFolderUsageDescription",
                "NSRemovableVolumesUsageDescription", "NSNetworkVolumesUsageDescription"):
        assert key in text
    build = (ROOT / "packaging" / "build_macos.sh").read_text(encoding="utf-8")
    assert 'imagetriage-dev.keychain-db' in build and '--sign "Image Triage Development"' in build
    signing = (ROOT / "packaging" / "macos_dev_signing.sh").read_text(encoding="utf-8")
    assert "extendedKeyUsage = critical, codeSigning" in signing and "security create-keychain" in signing
