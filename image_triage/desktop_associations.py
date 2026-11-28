"""File associations on macOS and Linux: which photo types open in Image Triage, and making it their default (Windows is
``file_associations``, through the registry and Windows' own chooser).

* Linux: the desktop reads ``image-triage.desktop`` (its ``MimeType=`` line says which types Image Triage opens, ``document_types``) and
  ``xdg-mime`` reads and sets the default app per MIME type, with no confirmation asked, so every type can be made default in one go.
  An AppImage run on its own has no desktop entry: "Make available" writes one (marked as written here, so Remove takes it away).
* macOS: the app's Info.plist declares the types (``setup_macos.py``); LaunchServices says which app opens each type and sets the
  default (``LSSetDefaultRoleHandlerForContentType``), called through ctypes. Only a built app (with a bundle identifier) can be made
  the default.
"""
from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .document_types import LINUX_DESKTOP_FILE, linux_mime_for_suffix, linux_mime_type_line

WRITTEN_MARK = "X-Image-Triage-Written=true"


@dataclass(slots=True, frozen=True)
class TypeState:
    state: str          # "default", "available" (Image Triage is offered for it), "other" (another app is the default) or "none"
    app: str = ""       # the other app's name


def backend():
    """The backend for this system: ``LinuxAssociations`` or ``MacAssociations``, or None (Windows, or nothing to work with)."""
    if sys.platform == "darwin":
        return MacAssociations()
    if sys.platform.startswith("linux") and shutil.which("xdg-mime"):
        return LinuxAssociations()
    return None


# ---- Linux ------------------------------------------------------------------------------------------------------------------------------
def _data_dirs() -> list[Path]:
    home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    rest = [Path(p) for p in (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":") if p]
    return [home, *rest]


class LinuxAssociations:
    platform = "linux"
    can_register = True

    def __init__(self, run=None) -> None:
        self._run = run or self._xdg_mime
        self._names: dict[str, str] = {}

    @staticmethod
    def _xdg_mime(*args: str) -> str:
        try:
            done = subprocess.run(["xdg-mime", *args], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return done.stdout.strip()

    @staticmethod
    def user_desktop_file() -> Path:
        return _data_dirs()[0] / "applications" / LINUX_DESKTOP_FILE

    def desktop_file(self) -> Path | None:
        """The installed ``image-triage.desktop`` the desktop uses (the person's own first, as the desktop looks)."""
        for root in _data_dirs():
            candidate = root / "applications" / LINUX_DESKTOP_FILE
            if candidate.is_file():
                return candidate
        return None

    def _offered(self) -> set[str]:
        """The MIME types the installed desktop entry says Image Triage opens."""
        path = self.desktop_file()
        if path is None:
            return set()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return set()
        match = re.search(r"^MimeType=(.*)$", text, re.M)
        return {m for m in (match.group(1).split(";") if match else []) if m}

    def _app_name(self, desktop_id: str) -> str:
        if desktop_id not in self._names:
            name = desktop_id.removesuffix(".desktop")
            for root in _data_dirs():
                path = root / "applications" / desktop_id
                try:
                    found = re.search(r"^Name=(.*)$", path.read_text(encoding="utf-8", errors="replace"), re.M)
                except OSError:
                    continue
                if found:
                    name = found.group(1).strip()
                    break
            self._names[desktop_id] = name
        return self._names[desktop_id]

    def states(self, suffixes) -> dict[str, TypeState]:
        offered = self._offered()
        defaults: dict[str, str] = {}
        out: dict[str, TypeState] = {}
        for suffix in suffixes:
            mime = linux_mime_for_suffix(suffix)
            if not mime:
                continue
            if mime not in defaults:
                defaults[mime] = self._run("query", "default", mime)
            current = defaults[mime]
            if current == LINUX_DESKTOP_FILE:
                out[suffix] = TypeState("default")
            elif current:
                out[suffix] = TypeState("other", self._app_name(current))
            else:
                out[suffix] = TypeState("available" if mime in offered else "none")
        return out

    def make_default(self, suffixes) -> str:
        """Image Triage as the default for ``suffixes`` (all at once); "" or why it could not be."""
        if self.desktop_file() is None:
            problem = self.register()
            if problem:
                return problem
        mimes = list(dict.fromkeys(m for m in (linux_mime_for_suffix(s) for s in suffixes) if m))
        if not mimes:
            return "These types have no MIME type the desktop knows."
        self._run("default", LINUX_DESKTOP_FILE, *mimes)
        return ""

    @staticmethod
    def launch_command() -> str:
        """What the desktop entry runs: the AppImage (it says where it is), the frozen app, or the module in a checkout."""
        appimage = os.environ.get("APPIMAGE", "")
        if appimage:
            return f'"{appimage}"'
        if getattr(sys, "frozen", False):
            return f'"{sys.executable}"'
        return f'"{sys.executable}" -m image_triage'

    def register(self) -> str:
        """Write the person's own desktop entry (when there is none), so the desktop offers Image Triage for photos."""
        if self.desktop_file() is not None:
            return ""
        path = self.user_desktop_file()
        icon = Path(__file__).resolve().parent / "assets" / "app_icon-v5.png"
        lines = ["[Desktop Entry]", "Type=Application", "Name=Image Triage", f"Exec={self.launch_command()} %F",
                 *([f"Icon={icon}"] if icon.is_file() else []), "Comment=Browse, cull, and score large image folders",
                 "Categories=Graphics;Photography;", linux_mime_type_line(), "Terminal=false", "StartupNotify=true",
                 "StartupWMClass=image-triage", WRITTEN_MARK]
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as error:
            return str(error)
        _refresh_desktop_database(path.parent)
        return ""

    def can_remove(self) -> bool:
        path = self.desktop_file()
        try:
            return path is not None and WRITTEN_MARK in path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return False

    def remove(self) -> str:
        """Take away the desktop entry written here (one the installer wrote is the installer's to remove)."""
        if not self.can_remove():
            return "The desktop entry was installed with the app; uninstall it to remove it."
        path = self.desktop_file()
        try:
            path.unlink()
        except OSError as error:
            return str(error)
        _refresh_desktop_database(path.parent)
        return ""


def _refresh_desktop_database(folder: Path) -> None:
    if shutil.which("update-desktop-database"):
        try:
            subprocess.run(["update-desktop-database", str(folder)], capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            pass


# ---- macOS ------------------------------------------------------------------------------------------------------------------------------
_UTF8 = 0x08000100                      # kCFStringEncodingUTF8
_ROLES_ALL = 0xFFFFFFFF                 # kLSRolesAll


class MacAssociations:
    platform = "mac"
    can_register = False                # the types are declared in the app's Info.plist; nothing to add or remove

    def __init__(self, services=None) -> None:
        self._services = services       # tests: an object with uti_for(ext), handler(uti), set_handler(uti, bundle) and bundle_id()

    def _ls(self):
        if self._services is None:
            self._services = _LaunchServices()
        return self._services

    def bundle_id(self) -> str:
        try:
            return self._ls().bundle_id()
        except Exception:
            return ""

    def states(self, suffixes) -> dict[str, TypeState]:
        ours = self.bundle_id().lower()
        out: dict[str, TypeState] = {}
        try:
            services = self._ls()
        except Exception:
            return out
        for suffix in suffixes:
            uti = services.uti_for(suffix.lstrip("."))
            if not uti:
                continue
            handler = services.handler(uti) or ""
            if ours and handler.lower() == ours:
                out[suffix] = TypeState("default")
            elif handler:
                out[suffix] = TypeState("other", _app_name_from_bundle_id(handler))
            else:
                out[suffix] = TypeState("available")
        return out

    def make_default(self, suffixes) -> str:
        ours = self.bundle_id()
        if not ours:
            return "Only the installed app can be made the default (this copy has no bundle identifier)."
        services = self._ls()
        failed = []
        for uti in dict.fromkeys(u for u in (services.uti_for(s.lstrip(".")) for s in suffixes) if u):
            status = services.set_handler(uti, ours)
            if status != 0:
                failed.append(f"{uti} ({status})")
        return ("macOS did not take the default for " + ", ".join(failed[:3])) if failed else ""

    def can_remove(self) -> bool:
        return False


def _app_name_from_bundle_id(bundle_id: str) -> str:
    """"com.apple.Preview" -> "Preview", "com.adobe.Photoshop" -> "Photoshop": a readable name without asking Finder."""
    last = bundle_id.rsplit(".", 1)[-1]
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", last) or bundle_id


class _LaunchServices:
    """The few LaunchServices and Core Foundation calls this needs, through ctypes (no PyObjC in the app)."""

    def __init__(self) -> None:
        self.cf = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.cs = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreServices.framework/CoreServices")
        cf, cs = self.cf, self.cs
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        cf.CFBundleGetMainBundle.restype = ctypes.c_void_p
        cf.CFBundleGetIdentifier.restype = ctypes.c_void_p
        cf.CFBundleGetIdentifier.argtypes = [ctypes.c_void_p]
        cs.UTTypeCreatePreferredIdentifierForTag.restype = ctypes.c_void_p
        cs.UTTypeCreatePreferredIdentifierForTag.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        cs.LSCopyDefaultRoleHandlerForContentType.restype = ctypes.c_void_p
        cs.LSCopyDefaultRoleHandlerForContentType.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        cs.LSSetDefaultRoleHandlerForContentType.restype = ctypes.c_int32
        cs.LSSetDefaultRoleHandlerForContentType.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]

    def _cf(self, text: str):
        return self.cf.CFStringCreateWithCString(None, text.encode("utf-8"), _UTF8)

    def _py(self, ref, release: bool = True) -> str:
        if not ref:
            return ""
        buffer = ctypes.create_string_buffer(1024)
        ok = self.cf.CFStringGetCString(ref, buffer, len(buffer), _UTF8)
        if release:
            self.cf.CFRelease(ref)
        return buffer.value.decode("utf-8") if ok else ""

    def bundle_id(self) -> str:
        bundle = self.cf.CFBundleGetMainBundle()
        return self._py(self.cf.CFBundleGetIdentifier(bundle), release=False) if bundle else ""

    def uti_for(self, extension: str) -> str:
        tag_class, tag = self._cf("public.filename-extension"), self._cf(extension)
        try:
            uti = self._py(self.cs.UTTypeCreatePreferredIdentifierForTag(tag_class, tag, None))
        finally:
            self.cf.CFRelease(tag_class)
            self.cf.CFRelease(tag)
        return uti

    def handler(self, uti: str) -> str:
        ref = self._cf(uti)
        try:
            return self._py(self.cs.LSCopyDefaultRoleHandlerForContentType(ref, _ROLES_ALL))
        finally:
            self.cf.CFRelease(ref)

    def set_handler(self, uti: str, bundle_id: str) -> int:
        content, bundle = self._cf(uti), self._cf(bundle_id)
        try:
            return int(self.cs.LSSetDefaultRoleHandlerForContentType(content, _ROLES_ALL, bundle))
        finally:
            self.cf.CFRelease(content)
            self.cf.CFRelease(bundle)
