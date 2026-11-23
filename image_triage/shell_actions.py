from __future__ import annotations

import os
import shutil
import subprocess
import sys
from functools import lru_cache
from glob import glob
from pathlib import Path

try:
    import winreg
except ImportError:  # pragma: no cover - Windows only
    winreg = None


def reveal_in_file_explorer(path: str) -> None:
    normalized = os.path.normpath(path)
    if os.name == "nt":
        # One command line, the path in its own quotes: given "/select,<path>" as one argument, Python quotes the whole of it when the
        # path has a space, Explorer does not read that, and opens its default folder (this program's) instead of the photo's.
        subprocess.Popen(f'explorer /select,"{normalized}"')
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", normalized])                 # Finder, with the file selected
        return
    _open_path(os.path.dirname(normalized) or normalized)


def open_in_file_explorer(path: str) -> None:
    normalized = os.path.normpath(path)
    if os.name == "nt":
        os.startfile(normalized)
        return
    _open_path(normalized)


def open_with_default(path: str) -> None:
    if os.name == "nt":
        os.startfile(path)
        return
    _open_path(path)


def open_with_dialog(path: str) -> None:
    if os.name == "nt":
        subprocess.Popen(["rundll32.exe", "shell32.dll,OpenAs_RunDLL", path])
        return
    _open_path(path)


def open_in_photoshop(path: str) -> None:
    executable = detect_photoshop_executable()
    if executable:
        subprocess.Popen([executable, path])
    elif os.name != "nt":
        open_with_default(path)


def _open_path(path: str) -> None:
    command = _best_open_command(path)
    if command is None:
        raise FileNotFoundError(f"No desktop open command is available for: {path}")
    subprocess.Popen(command)


def _best_open_command(path: str) -> list[str] | None:
    if sys.platform == "darwin":
        return ["open", path]
    for candidate in ("xdg-open", "gio", "gnome-open", "kde-open"):
        executable = shutil.which(candidate)
        if not executable:
            continue
        if candidate == "gio":
            return [executable, "open", path]
        return [executable, path]
    return None


@lru_cache(maxsize=1)
def detect_photoshop_executable() -> str | None:
    direct = shutil.which("Photoshop.exe")
    if direct:
        return direct

    from_registry = _photoshop_from_registry()
    if from_registry:
        return from_registry

    for root in filter(None, [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")]):
        matches = sorted(glob(str(Path(root) / "Adobe" / "Adobe Photoshop*" / "Photoshop.exe")), reverse=True)
        if matches:
            return matches[0]
    return None


# The editor's file name: photocraft.exe on Windows, photocraft elsewhere (freeze_support.PHOTOCRAFT_EXE_NAME bundles it so).
PHOTOCRAFT_EXE_NAME = "photocraft" + (".exe" if os.name == "nt" else "")


def bundled_photocraft_executable() -> str | None:
    """The editor inside the installed program (``<install dir>/photocraft/photocraft[.exe]``). Only the frozen app has one."""
    if not getattr(sys, "frozen", False):
        return None
    candidate = Path(sys.executable).resolve().parent / "photocraft" / PHOTOCRAFT_EXE_NAME
    return str(candidate) if candidate.is_file() else None


@lru_cache(maxsize=1)
def detect_photocraft_executable() -> str | None:
    # The installed program carries its own editor, built from the same release: always that one. IMAGE_TRIAGE_PHOTOCRAFT_EXE is for
    # running from source; left set on a machine, it made the installed app save every edit with an old debug build of the editor
    # (whose colour fit turned sunlit rock bright red), while the preview worker showed the photo right.
    bundled = bundled_photocraft_executable()
    if bundled:
        return bundled

    override = os.environ.get("IMAGE_TRIAGE_PHOTOCRAFT_EXE")
    if override and os.path.exists(override):
        checkout = Path(__file__).resolve().parents[2] / "photocraft"
        ordinary = [checkout / "target" / profile / PHOTOCRAFT_EXE_NAME for profile in ("release", "debug")]
        if Path(override).resolve() in [path.resolve() for path in ordinary]:
            hosted = companion_photocraft_executables()
            if hosted:
                return hosted[0]
        return override

    # The integration's companion build must precede unrelated installed/PATH
    # copies: those may implement app.open but lack hosted stash/restore.
    hosted = companion_photocraft_executables()
    if hosted:
        return hosted[0]

    direct = shutil.which("photocraft") or shutil.which("photocraft.exe")
    if direct:
        return direct

    for root in filter(None, [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")]):
        matches = sorted(glob(str(Path(root) / "PhotoCraft*" / "photocraft.exe")), reverse=True)
        if matches:
            return matches[0]
    checkout = Path(__file__).resolve().parents[2] / "photocraft"
    if (checkout / "Cargo.toml").is_file():
        for profile in ("release", "debug"):
            executable = checkout / "target" / profile / PHOTOCRAFT_EXE_NAME
            if executable.is_file():
                return str(executable)
    return None


def companion_photocraft_executables() -> list[str]:
    """Editors built in a PhotoCraft checkout beside this one, newest first: for running from source. The installed program uses
    its own editor only, never a development build that happens to be on the machine."""
    if bundled_photocraft_executable():
        return []
    checkouts = [Path(__file__).resolve().parents[2] / "photocraft", Path.home() / "Documents" / "photocraft"]
    hosted = [
        path
        for checkout in checkouts
        if (checkout / "Cargo.toml").is_file()
        for profile in ("release", "debug")
        for pattern in (PHOTOCRAFT_EXE_NAME, "photocraft-host*" + (".exe" if os.name == "nt" else ""))
        for path in (checkout / "target" / profile).glob(pattern)
        # Off Windows a program has no extension: `photocraft-host*` must not also find Cargo's `.d` dependency lists.
        if path.is_file() and (os.name == "nt" or not path.suffix)
    ]
    return [str(path) for path in sorted(hosted, key=lambda path: path.stat().st_mtime_ns, reverse=True)]


def _photoshop_from_registry() -> str | None:
    if winreg is None:
        return None
    keys = [
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Photoshop.exe",
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\Photoshop.exe",
    ]
    for key_path in keys:
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
                value, _ = winreg.QueryValueEx(key, None)
        except OSError:
            continue
        if value and os.path.exists(value):
            return value
    return None
