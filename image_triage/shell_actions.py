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
        subprocess.Popen(["explorer", f"/select,{normalized}"])
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
