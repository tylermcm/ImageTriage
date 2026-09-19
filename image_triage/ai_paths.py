"""One canonical location for every managed AI asset.

Before this module each consumer derived its own root from ``LOCALAPPDATA``,
``APPDATA`` or ``XDG_CACHE_HOME``. Under Windows Store Python those variables
point back into the package's virtualized cache::

    %LOCALAPPDATA%\\Packages\\PythonSoftwareFoundation.Python.3.13_*\\LocalCache\\Local\\...

which produced paths long enough to leave partially-installed packages that the
application could not read back. ``managed_ai_root`` resolves to a short,
non-virtualized directory and every managed subdirectory hangs off it.

See ``docs/ai_runtime_failure_map.md`` (root cause A).
"""

from __future__ import annotations

import ctypes
import os
import platform
import shutil
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


AI_ROOT_ENV = "IMAGE_TRIAGE_AI_ROOT"

RUNTIMES_DIRNAME = "rt"
MODELS_DIRNAME = "models"
CACHE_DIRNAME = "cache"
LOGS_DIRNAME = "logs"
STAGING_DIRNAME = "staging"

_WINDOWS_ROOT_PARTS = (".image-triage", "AI")
_POSIX_ROOT_PARTS = ("ImageTriage", "AI")

# Legacy cache root names that predate this module. Each is checked when a
# managed subdirectory is missing so an upgrade keeps a user's downloads.
_LEGACY_CACHE_DIRNAME = "image_triage_ai_cache"


@dataclass(frozen=True)
class StoragePreflight:
    """Result of checking that a managed directory can actually be used."""

    root: Path
    exists: bool
    writable: bool
    free_bytes: int
    required_bytes: int
    long_paths_enabled: bool
    path_length: int
    error: str = ""

    @property
    def has_free_space(self) -> bool:
        return self.required_bytes <= 0 or self.free_bytes >= self.required_bytes

    @property
    def ok(self) -> bool:
        return self.writable and self.has_free_space and not self.error

    def describe(self) -> str:
        """A short, user-facing reason this location cannot be used."""
        if self.error:
            return self.error
        if not self.writable:
            return f"Image Triage cannot write to {self.root}."
        if not self.has_free_space:
            need = self.required_bytes / (1024 * 1024 * 1024)
            have = self.free_bytes / (1024 * 1024 * 1024)
            return (
                f"Not enough free space on {self.root.anchor or self.root}: "
                f"{need:.1f} GB required, {have:.1f} GB available."
            )
        return ""


def devirtualized_local_appdata() -> Path:
    """``%LOCALAPPDATA%`` with Windows Store Python redirection undone.

    Store Python rewrites ``LOCALAPPDATA`` to its own package cache. Writing
    there is legal but produces very long paths, so fall back to the real
    profile-relative location whenever that redirection is detected.
    """
    if os.name != "nt":
        xdg_cache_home = os.environ.get("XDG_CACHE_HOME")
        if xdg_cache_home:
            return Path(xdg_cache_home)
        return _home() / ".cache"

    local_appdata = os.environ.get("LOCALAPPDATA")
    user_profile = os.environ.get("USERPROFILE")
    if local_appdata:
        normalized = str(Path(local_appdata)).replace("/", "\\").lower()
        is_store_virtualized = (
            "\\packages\\pythonsoftwarefoundation.python." in normalized
            and "\\localcache\\local" in normalized
        )
        if is_store_virtualized and user_profile:
            return Path(user_profile) / "AppData" / "Local"
        return Path(local_appdata)
    if user_profile:
        return Path(user_profile) / "AppData" / "Local"
    return _home() / "AppData" / "Local"


def _home() -> Path:
    try:
        return Path.home()
    except RuntimeError:
        return Path.cwd()


def default_managed_ai_root() -> Path:
    """The canonical managed root, ignoring any environment override."""
    if os.name == "nt":
        user_profile = os.environ.get("USERPROFILE")
        base = Path(user_profile) if user_profile else _home()
        return base.joinpath(*_WINDOWS_ROOT_PARTS)
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home else _home() / ".local" / "share"
    return base.joinpath(*_POSIX_ROOT_PARTS)


def managed_ai_root() -> Path:
    """Root of every managed AI asset, honouring ``IMAGE_TRIAGE_AI_ROOT``.

    The override exists for tests and support workflows; normal runs never set
    it. The value is resolved but deliberately *not* created here so that
    read-only callers stay side-effect free.
    """
    override = (os.environ.get(AI_ROOT_ENV, "") or "").strip()
    root = Path(override).expanduser() if override else default_managed_ai_root()
    try:
        return root.resolve()
    except OSError:
        return root


def managed_runtimes_root() -> Path:
    return managed_ai_root() / RUNTIMES_DIRNAME


def managed_models_root() -> Path:
    return managed_ai_root() / MODELS_DIRNAME


def managed_cache_root() -> Path:
    return managed_ai_root() / CACHE_DIRNAME


def managed_logs_root() -> Path:
    return managed_ai_root() / LOGS_DIRNAME


def managed_staging_root() -> Path:
    return managed_ai_root() / STAGING_DIRNAME


def managed_model_dir(*parts: str) -> Path:
    """Directory for one managed model bundle.

    Pure: resolving a path never moves a user's files. Migration is an explicit
    step (``migrate_managed_assets``) so it happens once, at a point where the
    caller is prepared for it, rather than as a side effect of a status check.
    """
    return managed_models_root().joinpath(*parts)


def managed_cache_dir(name: str) -> Path:
    """Directory for one managed derived-data cache (masks, depth maps, ...)."""
    return managed_cache_root() / name


def migrate_managed_assets(
    model_parts: Iterable[tuple[str, ...]] = (),
    cache_names: Iterable[str] = (),
) -> tuple[str, ...]:
    """Move pre-canonical model and cache directories under the managed root.

    Returns the destinations that were populated. Safe to call repeatedly: a
    destination that already exists is left alone.
    """
    moved: list[str] = []
    for parts in model_parts:
        target = managed_models_root().joinpath(*parts)
        if migrate_legacy_model_dir(target, tuple(parts)):
            moved.append(str(target))
    for name in cache_names:
        target = managed_cache_root() / name
        for legacy in legacy_cache_dirs(name):
            if _migrate_directory(legacy, target):
                moved.append(str(target))
                break
    return tuple(moved)


def legacy_cache_roots() -> tuple[Path, ...]:
    """Every pre-canonical root that may still hold managed assets."""
    candidates: list[Path] = []
    raw_local = os.environ.get("LOCALAPPDATA") if os.name == "nt" else os.environ.get("XDG_CACHE_HOME")
    if raw_local:
        candidates.append(Path(raw_local) / _LEGACY_CACHE_DIRNAME)
    candidates.append(devirtualized_local_appdata() / _LEGACY_CACHE_DIRNAME)
    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return tuple(unique)


def legacy_model_dirs(parts: tuple[str, ...]) -> tuple[Path, ...]:
    return tuple(root / MODELS_DIRNAME / Path(*parts) for root in legacy_cache_roots())


def legacy_cache_dirs(name: str) -> tuple[Path, ...]:
    return tuple(root / name for root in legacy_cache_roots())


def migrate_legacy_model_dir(target: Path, parts: tuple[str, ...]) -> bool:
    """Move a pre-canonical model directory into place. Returns True if moved."""
    for legacy in legacy_model_dirs(parts):
        if _migrate_directory(legacy, target):
            return True
    return False


def _migrate_directory(legacy: Path, target: Path) -> bool:
    if target.exists() or legacy == target:
        return False
    try:
        if not legacy.is_dir():
            return False
    except OSError:
        return False
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(legacy, target)
        return True
    except OSError:
        pass
    # ``os.replace`` fails across volumes; fall back to a copy that leaves the
    # original in place so a partial move can never lose the only copy.
    try:
        shutil.copytree(legacy, target, dirs_exist_ok=False)
    except OSError:
        _remove_partial(target)
        return False
    return True


def _remove_partial(path: Path) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


def free_bytes(path: Path) -> int:
    """Free bytes on the volume holding ``path`` (or its nearest parent)."""
    candidate = path
    for _ in range(len(candidate.parts) + 1):
        try:
            return shutil.disk_usage(candidate).free
        except OSError:
            parent = candidate.parent
            if parent == candidate:
                break
            candidate = parent
    return 0


def long_paths_enabled() -> bool:
    """Whether Windows long-path support is active for this process.

    Informational only: the canonical root is short enough that the answer must
    never change whether an install is attempted.
    """
    if os.name != "nt":
        return True
    try:
        import winreg  # noqa: PLC0415 - Windows-only import
    except ImportError:
        return False
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem"
        ) as key:
            value, _kind = winreg.QueryValueEx(key, "LongPathsEnabled")
        return bool(int(value))
    except OSError:
        return False


def preflight_storage(path: Path, *, required_bytes: int = 0) -> StoragePreflight:
    """Prove a managed directory is usable before an install writes to it."""
    error = ""
    exists = False
    writable = False
    try:
        path.mkdir(parents=True, exist_ok=True)
        exists = True
    except OSError as exc:
        error = f"Image Triage could not create {path}: {exc.strerror or exc}."
    if exists:
        probe = path / f".write-probe-{os.getpid()}"
        try:
            probe.write_bytes(b"ok")
            writable = True
        except OSError as exc:
            error = error or f"Image Triage cannot write to {path}: {exc.strerror or exc}."
        finally:
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                pass
    return StoragePreflight(
        root=path,
        exists=exists,
        writable=writable,
        free_bytes=free_bytes(path),
        required_bytes=max(0, int(required_bytes)),
        long_paths_enabled=long_paths_enabled(),
        path_length=len(str(path)),
        error=error,
    )


def process_architecture() -> str:
    """Normalized machine architecture for the *running* interpreter."""
    machine = (platform.machine() or "").replace(" ", "_").lower()
    if not machine and os.name == "nt":
        machine = (
            os.environ.get("PROCESSOR_ARCHITEW6432")
            or os.environ.get("PROCESSOR_ARCHITECTURE")
            or ""
        ).replace(" ", "_").lower()
    if machine in {"amd64", "x86_64"}:
        return "amd64"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    if not machine:
        return "amd64" if platform.architecture()[0] == "64bit" else "unknown"
    return machine


def runtime_tag() -> str:
    """Identity of the interpreter a managed runtime profile is built for."""
    system = (platform.system() or "unknown").replace(" ", "_").lower()
    return f"py{sys.version_info.major}{sys.version_info.minor}-{system}-{process_architecture()}"


def is_windows_arm64() -> bool:
    return os.name == "nt" and process_architecture() == "arm64"


def redact(text: str) -> str:
    """Strip the current user's identity out of a diagnostic string.

    Applied to every path and process output that reaches a support bundle.
    """
    if not text:
        return text
    replacements: list[tuple[str, str]] = []
    user_profile = os.environ.get("USERPROFILE") or os.environ.get("HOME")
    if user_profile:
        replacements.append((str(Path(user_profile)), "<user-home>"))
    username = os.environ.get("USERNAME") or os.environ.get("USER")
    if username and len(username) >= 3:
        replacements.append((username, "<user>"))
    result = text
    for needle, token in replacements:
        if not needle:
            continue
        result = result.replace(needle, token)
        result = result.replace(needle.replace("\\", "/"), token)
        result = result.replace(needle.replace("\\", "\\\\"), token)
    return result


def volume_kind(path: Path) -> str:
    """Best-effort description of the volume holding ``path``.

    Used in diagnostics so a support log distinguishes a local disk from a
    network share or removable drive without asking the user.
    """
    if os.name != "nt":
        return "local"
    anchor = path.anchor or str(path)
    try:
        code = int(ctypes.windll.kernel32.GetDriveTypeW(anchor))  # type: ignore[attr-defined]
    except Exception:  # pragma: no cover - non-Windows or restricted host
        return "unknown"
    return {
        0: "unknown",
        1: "invalid",
        2: "removable",
        3: "local",
        4: "network",
        5: "cdrom",
        6: "ramdisk",
    }.get(code, "unknown")


__all__ = [
    "AI_ROOT_ENV",
    "StoragePreflight",
    "default_managed_ai_root",
    "devirtualized_local_appdata",
    "free_bytes",
    "is_windows_arm64",
    "legacy_cache_dirs",
    "legacy_cache_roots",
    "legacy_model_dirs",
    "long_paths_enabled",
    "managed_ai_root",
    "managed_cache_dir",
    "managed_cache_root",
    "managed_logs_root",
    "managed_model_dir",
    "managed_models_root",
    "managed_runtimes_root",
    "managed_staging_root",
    "migrate_legacy_model_dir",
    "migrate_managed_assets",
    "preflight_storage",
    "process_architecture",
    "redact",
    "runtime_tag",
    "volume_kind",
]
