from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

_DLL_DIRECTORY_HANDLES: list[object] = []


def _candidate_runtime_roots(script_path: Path) -> list[Path]:
    candidates = [
        Path(sys.executable).resolve().parent,
        script_path.parent.parent.parent,
        Path.cwd(),
    ]
    roots: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        for root in (candidate, candidate / "build_assets"):
            key = str(root.resolve(strict=False))
            if key in seen:
                continue
            seen.add(key)
            roots.append(root)
    return roots


def _prepend_path_entry(path: Path) -> None:
    path_text = str(path)
    if path_text in sys.path:
        sys.path.remove(path_text)
    sys.path.insert(0, path_text)


def _prepend_ai_site_packages(script_path: Path) -> None:
    device = _requested_device_from_argv()
    for site_packages_dir in _cached_runtime_site_packages(script_path, device=device):
        if site_packages_dir.exists():
            _prepend_path_entry(site_packages_dir)
    for root in _candidate_runtime_roots(script_path):
        site_packages_dir = root / "ai_site_packages"
        if site_packages_dir.exists():
            _prepend_path_entry(site_packages_dir)


def _prepend_ai_stdlib(script_path: Path) -> None:
    for root in _candidate_runtime_roots(script_path):
        stdlib_dir = root / "ai_stdlib"
        if stdlib_dir.exists():
            _prepend_path_entry(stdlib_dir)


def _prepend_ai_binary_modules(script_path: Path) -> None:
    device = _requested_device_from_argv()
    for site_packages_dir in _cached_runtime_site_packages(script_path, device=device):
        if site_packages_dir.exists():
            _register_binary_search_path(site_packages_dir / "torch" / "lib")
            for libs_dir in site_packages_dir.glob("*.libs"):
                _register_binary_search_path(libs_dir)
    for root in _candidate_runtime_roots(script_path):
        candidate_dirs = [root / "lib", root / "ai_python_dlls"]
        site_packages_dir = root / "ai_site_packages"
        if site_packages_dir.exists():
            candidate_dirs.append(site_packages_dir / "torch" / "lib")
            candidate_dirs.extend(path for path in site_packages_dir.glob("*.libs"))
        for directory in candidate_dirs:
            _register_binary_search_path(directory)


def _register_binary_search_path(path: Path) -> None:
    if not path.exists():
        return
    path_text = str(path)
    existing_parts = os.environ.get("PATH", "").split(os.pathsep) if os.environ.get("PATH") else []
    if path_text not in existing_parts:
        os.environ["PATH"] = path_text if not existing_parts else path_text + os.pathsep + os.environ["PATH"]
    add_dll_directory = getattr(os, "add_dll_directory", None)
    if add_dll_directory is None:
        return
    try:
        handle = add_dll_directory(path_text)
    except OSError:
        return
    _DLL_DIRECTORY_HANDLES.append(handle)


def _prepend_engine_root(script_path: Path) -> None:
    engine_root = script_path.parent.parent
    if (engine_root / "app").exists():
        engine_root_text = str(engine_root)
        if engine_root_text not in sys.path:
            sys.path.insert(0, engine_root_text)

    cwd_text = str(Path.cwd())
    if cwd_text not in sys.path:
        sys.path.insert(0, cwd_text)


def _requested_device_from_argv() -> str:
    args = sys.argv[2:]
    for index, value in enumerate(args):
        if value == "--device" and index + 1 < len(args):
            return str(args[index + 1]).strip().lower() or "auto"
    return "auto"


def _cached_runtime_site_packages(script_path: Path, *, device: str) -> tuple[Path, ...]:
    _ = script_path
    candidate_roots = [Path(__file__).resolve().parents[1], Path.cwd()]
    for root in candidate_roots:
        root_text = str(root)
        if root_text not in sys.path:
            sys.path.insert(0, root_text)
    try:
        from image_triage.ai_runtime_packages import resolve_ai_runtime_site_packages
    except Exception:
        return ()
    try:
        return tuple(resolve_ai_runtime_site_packages(device=device))
    except Exception:
        return ()


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: ai_python_runner <script.py> [args...]", file=sys.stderr)
        return 2

    script_argument = sys.argv[1]
    script_path = Path(script_argument).expanduser()
    if not script_path.is_absolute():
        script_path = (Path.cwd() / script_path).resolve()
    else:
        script_path = script_path.resolve()

    if not script_path.exists():
        print(f"AI runner could not find script: {script_path}", file=sys.stderr)
        return 2

    # Emulate `python script.py ...` argument semantics.
    _prepend_ai_stdlib(script_path)
    _prepend_ai_binary_modules(script_path)
    _prepend_ai_site_packages(script_path)
    _prepend_engine_root(script_path)
    sys.argv = [str(script_path), *sys.argv[2:]]
    runpy.run_path(str(script_path), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
