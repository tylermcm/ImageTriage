from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import winreg
except ImportError:  # pragma: no cover - Windows only
    winreg = None

from .formats import FITS_SUFFIXES, PSD_SUFFIXES, RAW_SUFFIXES, STANDARD_IMAGE_SUFFIXES


APP_EXE_NAME = "ImageTriage.exe"
APP_FRIENDLY_NAME = "Image Triage"
APP_PROG_ID = "ImageTriage.SupportedImage"
_CLASSES_ROOT = r"Software\Classes"
_APPLICATIONS_KEY = rf"{_CLASSES_ROOT}\Applications\{APP_EXE_NAME}"
_PROG_ID_KEY = rf"{_CLASSES_ROOT}\{APP_PROG_ID}"
_ASSOCIATION_CHANGED = 0x08000000

_SUPPORTED_ASSOCIATION_SUFFIXES = tuple(
    sorted(
        {
            suffix
            for suffix in (
                set(STANDARD_IMAGE_SUFFIXES)
                | set(RAW_SUFFIXES)
                | set(PSD_SUFFIXES)
                | {".fit", ".fits", ".fts"}
            )
            if suffix.startswith(".") and suffix.count(".") == 1
        }
    )
)


@dataclass(slots=True)
class FileAssociationStatus:
    command: str
    supported_suffixes: tuple[str, ...]
    registered_suffixes: tuple[str, ...]
    app_registered: bool
    windows_supported: bool


def supported_file_association_suffixes() -> tuple[str, ...]:
    return _SUPPORTED_ASSOCIATION_SUFFIXES


def current_file_association_command() -> str:
    executable = str(Path(sys.executable).resolve())
    if getattr(sys, "frozen", False):
        return f'"{executable}" "%1"'
    return f'"{executable}" -m image_triage "%1"'


def query_windows_file_association_status() -> FileAssociationStatus:
    command = current_file_association_command()
    supported = supported_file_association_suffixes()
    if os.name != "nt" or winreg is None:
        return FileAssociationStatus(
            command=command,
            supported_suffixes=supported,
            registered_suffixes=(),
            app_registered=False,
            windows_supported=False,
        )
    app_registered = _registry_key_exists(winreg.HKEY_CURRENT_USER, _APPLICATIONS_KEY)
    registered = tuple(suffix for suffix in supported if _extension_has_progid(suffix))
    return FileAssociationStatus(
        command=command,
        supported_suffixes=supported,
        registered_suffixes=registered,
        app_registered=app_registered,
        windows_supported=True,
    )


def register_windows_file_associations() -> FileAssociationStatus:
    if os.name != "nt" or winreg is None:
        return query_windows_file_association_status()
    command = current_file_association_command()
    executable_path = str(Path(sys.executable).resolve())

    _set_default_value(winreg.HKEY_CURRENT_USER, _APPLICATIONS_KEY, APP_FRIENDLY_NAME)
    _set_string_value(winreg.HKEY_CURRENT_USER, _APPLICATIONS_KEY, "FriendlyAppName", APP_FRIENDLY_NAME)
    _set_default_value(winreg.HKEY_CURRENT_USER, rf"{_APPLICATIONS_KEY}\shell\open\command", command)
    for suffix in supported_file_association_suffixes():
        _set_string_value(winreg.HKEY_CURRENT_USER, rf"{_APPLICATIONS_KEY}\SupportedTypes", suffix, "")

    _set_default_value(winreg.HKEY_CURRENT_USER, _PROG_ID_KEY, APP_FRIENDLY_NAME)
    _set_string_value(winreg.HKEY_CURRENT_USER, _PROG_ID_KEY, "FriendlyTypeName", APP_FRIENDLY_NAME)
    _set_default_value(winreg.HKEY_CURRENT_USER, rf"{_PROG_ID_KEY}\shell\open\command", command)
    _set_default_value(winreg.HKEY_CURRENT_USER, rf"{_PROG_ID_KEY}\DefaultIcon", executable_path)

    for suffix in supported_file_association_suffixes():
        _set_string_value(winreg.HKEY_CURRENT_USER, rf"{_CLASSES_ROOT}\{suffix}\OpenWithProgids", APP_PROG_ID, "")

    _notify_windows_shell_of_association_change()
    return query_windows_file_association_status()


def remove_windows_file_associations() -> FileAssociationStatus:
    if os.name != "nt" or winreg is None:
        return query_windows_file_association_status()
    for suffix in supported_file_association_suffixes():
        _delete_value(winreg.HKEY_CURRENT_USER, rf"{_CLASSES_ROOT}\{suffix}\OpenWithProgids", APP_PROG_ID)
    _delete_registry_tree(winreg.HKEY_CURRENT_USER, _PROG_ID_KEY)
    _delete_registry_tree(winreg.HKEY_CURRENT_USER, _APPLICATIONS_KEY)
    _notify_windows_shell_of_association_change()
    return query_windows_file_association_status()


def open_windows_default_apps_settings() -> None:
    if os.name != "nt":
        raise OSError("Windows Default Apps is only available on Windows.")
    os.startfile("ms-settings:defaultapps")


def _extension_has_progid(suffix: str) -> bool:
    key_path = rf"{_CLASSES_ROOT}\{suffix}\OpenWithProgids"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.QueryValueEx(key, APP_PROG_ID)
        return True
    except OSError:
        return False


def _registry_key_exists(root: int, key_path: str) -> bool:
    try:
        with winreg.OpenKey(root, key_path):
            return True
    except OSError:
        return False


def _set_default_value(root: int, key_path: str, value: str) -> None:
    with winreg.CreateKeyEx(root, key_path, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, None, 0, winreg.REG_SZ, value)


def _set_string_value(root: int, key_path: str, name: str, value: str) -> None:
    with winreg.CreateKeyEx(root, key_path, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def _delete_registry_tree(root: int, key_path: str) -> None:
    if winreg is None:
        return
    try:
        with winreg.OpenKey(root, key_path, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
            while True:
                try:
                    child_name = winreg.EnumKey(key, 0)
                except OSError:
                    break
                _delete_registry_tree(root, rf"{key_path}\{child_name}")
    except OSError:
        return
    try:
        winreg.DeleteKey(root, key_path)
    except OSError:
        return


def _delete_value(root: int, key_path: str, name: str) -> None:
    if winreg is None:
        return
    try:
        with winreg.OpenKey(root, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, name)
    except OSError:
        return


def _notify_windows_shell_of_association_change() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SHChangeNotify(_ASSOCIATION_CHANGED, 0, None, None)
    except Exception:
        return
