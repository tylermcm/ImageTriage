from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

_DLL_DIRECTORY_HANDLES: list[object] = []


def _candidate_runtime_roots(script_path: Path | None = None) -> list[Path]:
    candidates = [
        Path(sys.executable).resolve().parent,
        Path.cwd(),
    ]
    if script_path is not None:
        candidates.insert(1, script_path.parent.parent.parent)
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


def _prepend_app_package_roots(script_path: Path | None = None) -> None:
    candidates = [
        Path(sys.executable).resolve().parent,
        Path.cwd(),
    ]
    if script_path is not None:
        candidates.extend(script_path.resolve().parents)

    roots: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        if not ((candidate / "aiculler").exists() or (candidate / "image_triage").exists()):
            continue
        key = str(candidate.resolve(strict=False))
        if key in seen:
            continue
        seen.add(key)
        roots.append(candidate)

    for root in reversed(roots):
        _prepend_path_entry(root)


def _prepend_ai_site_packages(script_path: Path | None = None) -> None:
    """Put AI packages on ``sys.path``, managed profile first.

    Order matters and is not obvious from reading top to bottom: every helper
    inserts at position 0, so the *last* directory added wins. Bundled
    ``ai_site_packages`` is therefore added first and the managed profile
    second, which leaves the managed profile ahead of it. ``_assert_runtime_precedence``
    checks that invariant instead of leaving it to the reader.
    """
    device = _requested_device_from_argv()
    bundled: list[Path] = []
    for root in _candidate_runtime_roots(script_path):
        site_packages_dir = root / "ai_site_packages"
        if site_packages_dir.exists():
            _prepend_path_entry(site_packages_dir)
            bundled.append(site_packages_dir)
    managed: list[Path] = []
    for site_packages_dir in _cached_runtime_site_packages(device=device):
        if site_packages_dir.exists():
            _prepend_path_entry(site_packages_dir)
            managed.append(site_packages_dir)
    _assert_runtime_precedence(managed, bundled)


def _assert_runtime_precedence(managed: list[Path], bundled: list[Path]) -> None:
    """Fail loudly if a bundled staging directory would shadow the managed runtime."""
    if not managed or not bundled:
        return
    order = {value: index for index, value in enumerate(sys.path)}
    worst_managed = max(order.get(str(path), -1) for path in managed)
    best_bundled = min(order.get(str(path), len(sys.path)) for path in bundled)
    if worst_managed > best_bundled:
        _report_runtime_problem(
            "the bundled ai_site_packages directory is shadowing the managed AI runtime "
            f"({bundled[0]} before {managed[0]}). AI results would come from the wrong packages."
        )
        raise RuntimeError("Bundled AI packages shadow the managed AI runtime.")


def _prepend_ai_stdlib(script_path: Path | None = None) -> None:
    if not getattr(sys, "frozen", False):
        return
    for root in _candidate_runtime_roots(script_path):
        stdlib_dir = root / "ai_stdlib"
        if stdlib_dir.exists():
            _prepend_path_entry(stdlib_dir)


def _prepend_ai_binary_modules(script_path: Path | None = None) -> None:
    device = _requested_device_from_argv()
    for root in _candidate_runtime_roots(script_path):
        candidate_dirs = [root / "lib", root / "ai_python_dlls"]
        site_packages_dir = root / "ai_site_packages"
        if site_packages_dir.exists():
            candidate_dirs.append(site_packages_dir / "torch" / "lib")
            candidate_dirs.extend(path for path in site_packages_dir.glob("*.libs"))
        for directory in candidate_dirs:
            _register_binary_search_path(directory)
    for site_packages_dir in _cached_runtime_site_packages(device=device):
        if site_packages_dir.exists():
            _register_binary_search_path(site_packages_dir / "torch" / "lib")
            for libs_dir in site_packages_dir.glob("*.libs"):
                _register_binary_search_path(libs_dir)


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


def _requested_device_from_argv() -> str:
    """The device this process must use.

    The parent's pinned selection wins over the command line so that parent and
    child can never resolve to different profiles for one job.
    """
    pinned = (os.environ.get("IMAGE_TRIAGE_AI_SELECTED_DEVICE", "") or "").strip().lower()
    if pinned:
        return pinned
    args = sys.argv[2:]
    for index, value in enumerate(args):
        if value == "--device" and index + 1 < len(args):
            return str(args[index + 1]).strip().lower() or "auto"
    return "auto"


class ManagedRuntimeError(RuntimeError):
    """The managed AI runtime could not be resolved for this process."""


def _report_runtime_problem(message: str) -> None:
    print(f"AI runner: {message}", file=sys.stderr, flush=True)


def _fail_runtime(message: str) -> None:
    """Abort before the script runs.

    This boundary used to log and continue, which let a bundled or globally
    installed package satisfy the import instead — producing plausible but
    wrong results, or the same late ``ModuleNotFoundError`` this work set out
    to remove (docs/ai_runtime_failure_map.md, root cause F).
    """
    _report_runtime_problem(message)
    raise ManagedRuntimeError(message)


def _cached_runtime_site_packages(*, device: str) -> tuple[Path, ...]:
    candidate_roots = [Path(__file__).resolve().parents[1]]
    for root in candidate_roots:
        root_text = str(root)
        if root_text not in sys.path:
            sys.path.insert(0, root_text)
    try:
        from image_triage.ai_runtime_packages import resolve_ai_runtime_site_packages
    except Exception as exc:
        _fail_runtime(
            f"could not load the managed runtime resolver ({type(exc).__name__}: {exc}). "
            "Reinstall Image Triage."
        )
        return ()
    try:
        resolved = tuple(resolve_ai_runtime_site_packages(device=device))
    except Exception as exc:
        _fail_runtime(
            f"could not resolve the managed AI runtime for device {device!r} "
            f"({type(exc).__name__}: {exc}). Open Settings and run Repair AI."
        )
        return ()
    if not resolved and _managed_runtime_required():
        _fail_runtime(
            f"no managed AI runtime profile is installed for device {device!r}. "
            "Open Settings and run Set Up AI."
        )
    return resolved


def _managed_runtime_required() -> bool:
    """Whether this process must have a managed runtime to be correct.

    A script that needs no third-party AI package (report export, for example)
    can run without one. The parent states the requirement explicitly by
    pinning a profile; anything launched through ``ai_env.build_worker_env``
    therefore hard-fails, and a bare invocation stays permissive.
    """
    return bool((os.environ.get("IMAGE_TRIAGE_AI_PROFILE", "") or "").strip())


def _configure_runtime_environment(script_path: Path | None = None) -> None:
    """Build this process's import environment.

    Each step inserts at position 0, so the resulting precedence is the reverse
    of the call order: engine root, managed AI site-packages, bundled
    ai_site_packages, ai_stdlib, then the application package roots.
    """
    _prepend_app_package_roots(script_path)
    _prepend_ai_stdlib(script_path)
    _prepend_ai_binary_modules(script_path)
    _prepend_ai_site_packages(script_path)
    if script_path is not None:
        _prepend_engine_root(script_path)


def _handle_forked_child_process() -> int | None:
    if len(sys.argv) < 2 or sys.argv[1] != "--multiprocessing-fork":
        return None
    try:
        _configure_runtime_environment()
    except ManagedRuntimeError:
        return 3
    fork_args = sys.argv[2:]
    if fork_args and all("=" in arg for arg in fork_args):
        from multiprocessing.spawn import freeze_support

        freeze_support()
        return 0
    if not fork_args:
        print("AI runner received no multiprocessing fork payload.", file=sys.stderr)
        return 2
    try:
        pipe_handle = int(fork_args[0])
    except ValueError:
        print(
            f"AI runner received unsupported multiprocessing fork payload: {' '.join(fork_args)}",
            file=sys.stderr,
        )
        return 2
    parent_pid: int | None = None
    for arg in fork_args[1:]:
        if not arg.startswith("parent_pid="):
            continue
        value = arg.partition("=")[2].strip()
        if not value or value == "None":
            parent_pid = None
            continue
        try:
            parent_pid = int(value)
        except ValueError:
            parent_pid = None
    from joblib.externals.loky.backend.popen_loky_win32 import main as loky_spawn_main

    loky_spawn_main(pipe_handle=pipe_handle, parent_pid=parent_pid)
    return 0


def main() -> int:
    fork_result = _handle_forked_child_process()
    if fork_result is not None:
        return fork_result
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
    try:
        _configure_runtime_environment(script_path)
    except ManagedRuntimeError:
        return 3
    if os.environ.get("IMAGE_TRIAGE_AI_TRACE_PATH") == "1":
        for index, entry in enumerate(sys.path[:8]):
            print(f"AI runner sys.path[{index}] = {entry}", file=sys.stderr, flush=True)
    sys.argv = [str(script_path), *sys.argv[2:]]
    runpy.run_path(str(script_path), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
