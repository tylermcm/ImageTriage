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
SUPPORTED_SUFFIXES = (
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


def _cleanup_paths(*, include_ai_cache: bool = True) -> list[Path]:
    paths: list[Path] = []
    appdata = _env_path("APPDATA")
    local_appdata = _env_path("LOCALAPPDATA")
    temp_root = Path(tempfile.gettempdir())

    if include_ai_cache:
        if local_appdata is not None:
            paths.append(local_appdata / "image_triage_ai_cache")
        paths.append(temp_root / "image_triage_ai_cache")

    if appdata is not None:
        paths.extend(
            [
                appdata / "ImageTriage",
                appdata / "Codex" / APP_NAME,
            ]
        )
    if local_appdata is not None:
        paths.extend(
            [
                local_appdata / "ImageTriage",
                local_appdata / "Codex" / APP_NAME,
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


def _cleanup_registry(*, dry_run: bool, output: list[str]) -> None:
    if os.name != "nt" or winreg is None:
        return
    classes_root = r"Software\Classes"
    _delete_registry_tree(winreg.HKEY_CURRENT_USER, rf"{classes_root}\Applications\{APP_EXE_NAME}", dry_run=dry_run, output=output)
    _delete_registry_tree(winreg.HKEY_CURRENT_USER, rf"{classes_root}\{APP_PROG_ID}", dry_run=dry_run, output=output)
    _delete_registry_tree(winreg.HKEY_CURRENT_USER, rf"Software\Codex\{APP_NAME}", dry_run=dry_run, output=output)
    for suffix in SUPPORTED_SUFFIXES:
        _delete_registry_value(
            winreg.HKEY_CURRENT_USER,
            rf"{classes_root}\{suffix}\OpenWithProgids",
            APP_PROG_ID,
            dry_run=dry_run,
            output=output,
        )


def cleanup(*, dry_run: bool, include_ai_cache: bool) -> list[str]:
    output: list[str] = []
    for path in _cleanup_paths(include_ai_cache=include_ai_cache):
        _remove_path(path, dry_run=dry_run, output=output)
    _cleanup_registry(dry_run=dry_run, output=output)
    return output


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Clean Image Triage user data, caches, and registry entries.")
    parser.add_argument(
        "--mode",
        choices=("previous-install", "uninstall", "manual"),
        default="manual",
        help="Cleanup context for logs; all modes remove the same app-owned data by default.",
    )
    parser.add_argument("--yes", action="store_true", help="Actually delete data. Without this, only prints a dry run.")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be removed without deleting anything.")
    parser.add_argument("--keep-ai-cache", action="store_true", help="Leave downloaded AI models/runtime caches in place.")
    parser.add_argument("--quiet", action="store_true", help="Suppress normal output.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    dry_run = bool(args.dry_run or not args.yes)
    messages = cleanup(dry_run=dry_run, include_ai_cache=not args.keep_ai_cache)
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
