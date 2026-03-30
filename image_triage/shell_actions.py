from __future__ import annotations

import os
import shutil
import subprocess
from functools import lru_cache
from glob import glob
from pathlib import Path

try:
    import winreg
except ImportError:  # pragma: no cover - Windows only
    winreg = None


def reveal_in_file_explorer(path: str) -> None:
    normalized = os.path.normpath(path)
    subprocess.Popen(["explorer", f"/select,{normalized}"])


def open_in_file_explorer(path: str) -> None:
    normalized = os.path.normpath(path)
    os.startfile(normalized)


def open_with_default(path: str) -> None:
    os.startfile(path)


def open_with_dialog(path: str) -> None:
    subprocess.Popen(["rundll32.exe", "shell32.dll,OpenAs_RunDLL", path])


def open_in_photoshop(path: str) -> None:
    executable = detect_photoshop_executable()
    if executable:
        subprocess.Popen([executable, path])


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
