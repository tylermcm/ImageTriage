from __future__ import annotations

import ctypes
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

try:
    import winreg
except ImportError:  # pragma: no cover - Windows only
    winreg = None

from .formats import FITS_SUFFIXES, PSD_SUFFIXES, RAW_SUFFIXES, STANDARD_IMAGE_SUFFIXES
from .shell_actions import open_with_dialog


APP_EXE_NAME = "ImageTriage.exe"
APP_FRIENDLY_NAME = "Image Triage"
APP_PROG_ID = "ImageTriage.SupportedImage"
_CLASSES_ROOT = r"Software\Classes"
_APPLICATIONS_KEY = rf"{_CLASSES_ROOT}\Applications\{APP_EXE_NAME}"
_PROG_ID_KEY = rf"{_CLASSES_ROOT}\{APP_PROG_ID}"
_FILE_EXTS_ROOT = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts"
_ASSOCIATION_CHANGED = 0x08000000
_OUR_PROGIDS = {APP_PROG_ID.casefold(), rf"Applications\{APP_EXE_NAME}".casefold()}
_INDIRECT_STRING_BUFFER_SIZE = 2048
_PRIORITIZED_ASSOCIATION_SUFFIXES = (
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".dng",
    ".nef",
    ".cr2",
    ".cr3",
    ".arw",
    ".raf",
    ".orf",
    ".rw2",
    ".fits",
    ".fit",
    ".fts",
    ".psd",
)
_PRIORITIZED_ASSOCIATION_SUFFIX_INDEX = {
    suffix: index for index, suffix in enumerate(_PRIORITIZED_ASSOCIATION_SUFFIXES)
}

_SUPPORTED_ASSOCIATION_SUFFIXES = tuple(
    sorted(
        (
            suffix
            for suffix in (
                set(STANDARD_IMAGE_SUFFIXES)
                | set(RAW_SUFFIXES)
                | set(PSD_SUFFIXES)
                | {".fit", ".fits", ".fts"}
            )
            if suffix.startswith(".") and suffix.count(".") == 1
        ),
        key=lambda suffix: (_PRIORITIZED_ASSOCIATION_SUFFIX_INDEX.get(suffix, len(_PRIORITIZED_ASSOCIATION_SUFFIXES)), suffix),
    )
)


@dataclass(slots=True)
class FileAssociationStatus:
    command: str
    supported_suffixes: tuple[str, ...]
    registered_suffixes: tuple[str, ...]
    app_registered: bool
    windows_supported: bool


@dataclass(slots=True)
class ExtensionAssociationState:
    suffix: str
    registered: bool
    is_default: bool
    default_progid: str


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


def query_windows_file_association_states() -> tuple[ExtensionAssociationState, ...]:
    supported = supported_file_association_suffixes()
    if os.name != "nt" or winreg is None:
        return tuple(
            ExtensionAssociationState(
                suffix=suffix,
                registered=False,
                is_default=False,
                default_progid="",
            )
            for suffix in supported
        )
    return tuple(
        ExtensionAssociationState(
            suffix=suffix,
            registered=_extension_has_progid(suffix),
            is_default=_is_our_progid(default_progid := _default_progid_for_extension(suffix)),
            default_progid=default_progid,
        )
        for suffix in supported
    )


def register_windows_file_associations(suffixes: tuple[str, ...] | list[str] | set[str] | None = None) -> FileAssociationStatus:
    if os.name != "nt" or winreg is None:
        return query_windows_file_association_status()
    command = current_file_association_command()
    executable_path = str(Path(sys.executable).resolve())
    requested_suffixes = _normalize_requested_suffixes(suffixes)

    _set_default_value(winreg.HKEY_CURRENT_USER, _APPLICATIONS_KEY, APP_FRIENDLY_NAME)
    _set_string_value(winreg.HKEY_CURRENT_USER, _APPLICATIONS_KEY, "FriendlyAppName", APP_FRIENDLY_NAME)
    _set_default_value(winreg.HKEY_CURRENT_USER, rf"{_APPLICATIONS_KEY}\shell\open\command", command)
    for suffix in requested_suffixes:
        _set_string_value(winreg.HKEY_CURRENT_USER, rf"{_APPLICATIONS_KEY}\SupportedTypes", suffix, "")

    _set_default_value(winreg.HKEY_CURRENT_USER, _PROG_ID_KEY, APP_FRIENDLY_NAME)
    _set_string_value(winreg.HKEY_CURRENT_USER, _PROG_ID_KEY, "FriendlyTypeName", APP_FRIENDLY_NAME)
    _set_default_value(winreg.HKEY_CURRENT_USER, rf"{_PROG_ID_KEY}\shell\open\command", command)
    _set_default_value(winreg.HKEY_CURRENT_USER, rf"{_PROG_ID_KEY}\DefaultIcon", executable_path)

    for suffix in requested_suffixes:
        _set_string_value(winreg.HKEY_CURRENT_USER, rf"{_CLASSES_ROOT}\{suffix}\OpenWithProgids", APP_PROG_ID, "")

    _notify_windows_shell_of_association_change()
    return query_windows_file_association_status()


def remove_windows_file_associations(suffixes: tuple[str, ...] | list[str] | set[str] | None = None) -> FileAssociationStatus:
    if os.name != "nt" or winreg is None:
        return query_windows_file_association_status()
    requested_suffixes = _normalize_requested_suffixes(suffixes)
    for suffix in requested_suffixes:
        _delete_value(winreg.HKEY_CURRENT_USER, rf"{_CLASSES_ROOT}\{suffix}\OpenWithProgids", APP_PROG_ID)
        _delete_value(winreg.HKEY_CURRENT_USER, rf"{_APPLICATIONS_KEY}\SupportedTypes", suffix)
    remaining = tuple(state.suffix for state in query_windows_file_association_states() if state.registered)
    if not remaining:
        _delete_registry_tree(winreg.HKEY_CURRENT_USER, _PROG_ID_KEY)
        _delete_registry_tree(winreg.HKEY_CURRENT_USER, _APPLICATIONS_KEY)
    _notify_windows_shell_of_association_change()
    return query_windows_file_association_status()


def open_windows_default_apps_settings() -> None:
    if os.name != "nt":
        raise OSError("Windows Default Apps is only available on Windows.")
    os.startfile("ms-settings:defaultapps")


def open_windows_file_association_chooser(suffix: str) -> str:
    normalized = _normalize_suffix(suffix)
    if os.name != "nt":
        raise OSError("Windows file association chooser is only available on Windows.")
    if not normalized:
        raise ValueError("A valid extension is required.")
    register_windows_file_associations([normalized])
    probe_dir = Path(tempfile.gettempdir()) / "image-triage-association-probes"
    probe_dir.mkdir(parents=True, exist_ok=True)
    probe_path = probe_dir / f"image_triage_probe{normalized}"
    probe_path.write_text("Image Triage file association probe.\n", encoding="utf-8")
    open_with_dialog(str(probe_path))
    return str(probe_path)


def describe_windows_default_handler(state: ExtensionAssociationState) -> str:
    if state.is_default:
        return APP_FRIENDLY_NAME
    if state.default_progid:
        normalized = state.default_progid.strip()
        lower = normalized.casefold()
        if lower.startswith("applications\\"):
            return normalized.split("\\", 1)[-1]
        resolved = _friendly_name_for_progid(normalized)
        if resolved:
            return resolved
        if lower.startswith("appx"):
            return "Windows App"
        if "." in normalized:
            return normalized.rsplit(".", 1)[-1]
        return normalized
    if state.registered:
        return "Available"
    return "Not Registered"


def _friendly_name_for_progid(progid: str) -> str:
    resolved = _resolve_progid_display_name(progid)
    if resolved:
        return resolved
    lower = progid.casefold()
    if lower.startswith("applications\\"):
        return progid.split("\\", 1)[-1]
    if lower.startswith("appx"):
        return "Windows App"
    if "." in progid:
        return progid.rsplit(".", 1)[-1]
    return progid


def _extension_has_progid(suffix: str) -> bool:
    key_path = rf"{_CLASSES_ROOT}\{suffix}\OpenWithProgids"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.QueryValueEx(key, APP_PROG_ID)
        return True
    except OSError:
        return False


def _default_progid_for_extension(suffix: str) -> str:
    if winreg is None:
        return ""
    user_choice_key = rf"{_FILE_EXTS_ROOT}\{suffix}\UserChoice"
    for root, key_path in (
        (winreg.HKEY_CURRENT_USER, user_choice_key),
        (winreg.HKEY_CURRENT_USER, rf"{_CLASSES_ROOT}\{suffix}"),
        (winreg.HKEY_CLASSES_ROOT, suffix),
    ):
        try:
            with winreg.OpenKey(root, key_path) as key:
                value_name = "ProgId" if key_path.endswith("UserChoice") else None
                value, _ = winreg.QueryValueEx(key, value_name)
        except OSError:
            continue
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _resolve_progid_display_name(progid: str) -> str:
    if winreg is None or not progid:
        return ""
    candidates = (
        ("FriendlyAppName",),
        ("FriendlyTypeName",),
        ("Application", "ApplicationName"),
        ("Application", "FriendlyAppName"),
        (None,),
    )
    for root, key_path in (
        (winreg.HKEY_CURRENT_USER, rf"{_CLASSES_ROOT}\{progid}"),
        (winreg.HKEY_CLASSES_ROOT, progid),
    ):
        for candidate in candidates:
            try:
                if candidate[0] is None:
                    with winreg.OpenKey(root, key_path) as key:
                        value, _ = winreg.QueryValueEx(key, None)
                elif len(candidate) == 1:
                    with winreg.OpenKey(root, key_path) as key:
                        value, _ = winreg.QueryValueEx(key, candidate[0])
                else:
                    with winreg.OpenKey(root, rf"{key_path}\{candidate[0]}") as key:
                        value, _ = winreg.QueryValueEx(key, candidate[1])
            except OSError:
                continue
            if not isinstance(value, str):
                continue
            cleaned = value.strip()
            if not cleaned:
                continue
            resolved = _resolve_indirect_string(cleaned)
            if resolved:
                return resolved
    return ""


def _resolve_indirect_string(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return ""
    if not cleaned.startswith("@"):
        return cleaned
    if os.name != "nt":
        return cleaned
    try:
        buffer = ctypes.create_unicode_buffer(_INDIRECT_STRING_BUFFER_SIZE)
        result = ctypes.windll.shlwapi.SHLoadIndirectString(
            ctypes.c_wchar_p(cleaned),
            buffer,
            _INDIRECT_STRING_BUFFER_SIZE,
            None,
        )
        if result == 0:
            resolved = buffer.value.strip()
            if resolved:
                return resolved
    except Exception:
        return cleaned
    return cleaned


def _is_our_progid(progid: str) -> bool:
    return bool(progid) and progid.casefold() in _OUR_PROGIDS


def _normalize_requested_suffixes(suffixes: tuple[str, ...] | list[str] | set[str] | None) -> tuple[str, ...]:
    supported = set(supported_file_association_suffixes())
    if suffixes is None:
        return supported_file_association_suffixes()
    normalized = []
    seen: set[str] = set()
    for suffix in suffixes:
        normalized_suffix = _normalize_suffix(str(suffix))
        if normalized_suffix not in supported or normalized_suffix in seen:
            continue
        seen.add(normalized_suffix)
        normalized.append(normalized_suffix)
    return tuple(normalized)


def _normalize_suffix(suffix: str) -> str:
    normalized = suffix.strip().casefold()
    if not normalized:
        return ""
    if not normalized.startswith("."):
        normalized = f".{normalized}"
    return normalized


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
