from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

try:
    import winreg
except ImportError:  # pragma: no cover - Windows only
    winreg = None


APP_NAME = "Image Triage"
APP_EXE_NAME = "ImageTriage.exe"
APP_PROG_ID = "ImageTriage.SupportedImage"
# What an older build of this tool knew. Only used when the application's own list cannot be imported (the tool
# run by itself, away from the application), so that it still removes something instead of nothing.
_FALLBACK_SUFFIXES = (
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".bmp",
    ".gif",
    ".webp",
    ".heic",
    ".heif",
    ".dng",
    ".nef",
    ".cr2",
    ".cr3",
    ".arw",
    ".raf",
    ".orf",
    ".rw2",
    ".fit",
    ".fits",
    ".fts",
    ".psd",
)


def _association_suffixes() -> tuple[str, ...]:
    """Every file type the application registers itself for, from the module that does the registering: a second
    list kept here went stale, and the types it lacked were left pointing at the uninstalled program."""
    if not getattr(sys, "frozen", False):
        root = str(Path(__file__).resolve().parent.parent)
        if root not in sys.path:
            sys.path.append(root)
    try:
        from image_triage.file_associations import supported_file_association_suffixes

        return tuple(supported_file_association_suffixes())
    except Exception:
        return _FALLBACK_SUFFIXES


SUPPORTED_SUFFIXES = _association_suffixes()


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    if not value:
        return None
    return Path(value).expanduser()


def _home_path() -> Path | None:
    for name in ("USERPROFILE", "HOME"):
        path = _env_path(name)
        if path is not None:
            return path
    try:
        return Path.home()
    except RuntimeError:
        return None


def _cleanup_paths(*, include_ai_cache: bool = True, registrations_only: bool = False) -> list[Path]:
    """The folders to remove. ``registrations_only`` (what the uninstaller asks for) leaves everything the user
    made or chose: the application data folders hold the decisions database, the catalog, the restore records of
    deleted photos and the settings, and none of that is a cache."""
    paths: list[Path] = []
    appdata = _env_path("APPDATA")
    local_appdata = _env_path("LOCALAPPDATA")
    temp_root = Path(tempfile.gettempdir())

    if include_ai_cache:
        if local_appdata is not None:
            paths.append(local_appdata / "image_triage_ai_cache")
        paths.append(temp_root / "image_triage_ai_cache")

    if registrations_only:
        paths.append(temp_root / "image-triage-association-probes")
        return _dedupe_paths(paths)

    if appdata is not None:
        paths.extend(
            [
                appdata / "ImageTriage",
                appdata / "Codex" / APP_NAME,
                # Current identity post-WI-3.6 (QStandardPaths.AppDataLocation
                # under organisation "Image Triage" doubles the segment, same
                # as the pre-existing registry identity below).
                appdata / APP_NAME / APP_NAME,
            ]
        )
    if local_appdata is not None:
        paths.extend(
            [
                local_appdata / "ImageTriage",
                local_appdata / "Codex" / APP_NAME,
                local_appdata / APP_NAME / APP_NAME,
            ]
        )
    home = _home_path()
    if home is not None:
        paths.append(home / ".image-triage")
    paths.append(temp_root / "image-triage-association-probes")
    return _dedupe_paths(paths)


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    deduped: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            resolved = path.expanduser().resolve(strict=False)
        except OSError:
            resolved = path.expanduser().absolute()
        key = os.path.normcase(str(resolved))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(resolved)
    return deduped


def _is_safe_cleanup_path(path: Path) -> bool:
    parts = {part.casefold() for part in path.parts}
    allowed_names = {
        "image_triage_ai_cache",
        "imagetriage",
        ".image-triage",
        "image triage",
        "image-triage-association-probes",
    }
    return bool(parts & allowed_names)


def _remove_path(path: Path, *, dry_run: bool, output: list[str]) -> None:
    if not _is_safe_cleanup_path(path):
        output.append(f"Skipped unsafe path: {path}")
        return
    if not path.exists():
        output.append(f"Missing: {path}")
        return
    output.append(f"Remove: {path}")
    if dry_run:
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _delete_registry_tree(root, key_path: str, *, dry_run: bool, output: list[str]) -> None:
    if winreg is None:
        return
    output.append(f"Registry tree: HKCU\\{key_path}")
    if dry_run:
        return
    try:
        with winreg.OpenKey(root, key_path, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
            while True:
                try:
                    child = winreg.EnumKey(key, 0)
                except OSError:
                    break
                _delete_registry_tree(root, rf"{key_path}\{child}", dry_run=dry_run, output=output)
    except OSError:
        return
    try:
        winreg.DeleteKey(root, key_path)
    except OSError:
        return


def _delete_registry_value(root, key_path: str, value_name: str, *, dry_run: bool, output: list[str]) -> None:
    if winreg is None:
        return
    output.append(f"Registry value: HKCU\\{key_path} [{value_name}]")
    if dry_run:
        return
    try:
        with winreg.OpenKey(root, key_path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, value_name)
    except OSError:
        return


_CLASSES_ROOT = r"Software\Classes"


def _registry_targets(*, registrations_only: bool = False) -> tuple[list[str], list[tuple[str, str]]]:
    """What is removed under HKEY_CURRENT_USER: (key trees, (key, value name) pairs).

    The file-type registrations always go. The settings trees go only when the user asked for a full clean-up
    (``registrations_only`` false): an uninstall keeps the user's settings.
    """
    trees = [rf"{_CLASSES_ROOT}\Applications\{APP_EXE_NAME}", rf"{_CLASSES_ROOT}\{APP_PROG_ID}"]
    if not registrations_only:
        trees += [rf"Software\{APP_NAME}", rf"Software\Codex\{APP_NAME}", r"Software\ImageTriage"]
    values = [(rf"{_CLASSES_ROOT}\{suffix}\OpenWithProgids", APP_PROG_ID) for suffix in SUPPORTED_SUFFIXES]
    return trees, values


def _cleanup_registry(*, dry_run: bool, output: list[str], registrations_only: bool = False) -> None:
    if os.name != "nt" or winreg is None:
        return
    trees, values = _registry_targets(registrations_only=registrations_only)
    for key_path in trees:
        _delete_registry_tree(winreg.HKEY_CURRENT_USER, key_path, dry_run=dry_run, output=output)
    for key_path, value_name in values:
        _delete_registry_value(winreg.HKEY_CURRENT_USER, key_path, value_name, dry_run=dry_run, output=output)
    if not dry_run:
        _notify_shell()


def _notify_shell() -> None:
    """Tell Explorer the associations changed, so "Open with" stops offering the removed program straight away."""
    try:
        import ctypes

        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED
    except Exception:
        return


def cleanup(*, dry_run: bool, include_ai_cache: bool, registrations_only: bool = False) -> list[str]:
    output: list[str] = []
    for path in _cleanup_paths(include_ai_cache=include_ai_cache, registrations_only=registrations_only):
        _remove_path(path, dry_run=dry_run, output=output)
    _cleanup_registry(dry_run=dry_run, output=output, registrations_only=registrations_only)
    return output


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Clean Image Triage user data, caches, and registry entries.")
    parser.add_argument(
        "--mode",
        choices=("previous-install", "uninstall", "manual"),
        default="manual",
        help=(
            "uninstall (what the installer runs when the program is removed) takes away only this user's file-type "
            "registrations and the downloaded AI caches, and keeps settings, decisions and everything else the user "
            "made. previous-install and manual remove all app-owned data, settings and decisions included."
        ),
    )
    parser.add_argument("--yes", action="store_true", help="Actually delete data. Without this, only prints a dry run.")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be removed without deleting anything.")
    parser.add_argument("--keep-ai-cache", action="store_true", help="Leave downloaded AI models/runtime caches in place.")
    parser.add_argument("--quiet", action="store_true", help="Suppress normal output.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    dry_run = bool(args.dry_run or not args.yes)
    messages = cleanup(
        dry_run=dry_run,
        include_ai_cache=not args.keep_ai_cache,
        registrations_only=args.mode == "uninstall",
    )
    if not args.quiet:
        action = "Would clean" if dry_run else "Cleaned"
        print(f"{action} Image Triage data ({args.mode}).")
        for message in messages:
            print(message)
        if dry_run:
            print("Pass --yes to delete these paths and registry entries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
