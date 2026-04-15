from __future__ import annotations

import runpy
import sys
from pathlib import Path


def _prepend_ai_site_packages(script_path: Path) -> None:
    candidate_roots = [
        Path(sys.executable).resolve().parent,
        script_path.parent.parent.parent,
        Path.cwd(),
    ]
    for root in candidate_roots:
        site_packages_dir = root / "ai_site_packages"
        if site_packages_dir.exists():
            site_packages_text = str(site_packages_dir)
            if site_packages_text not in sys.path:
                sys.path.insert(0, site_packages_text)


def _prepend_ai_stdlib(script_path: Path) -> None:
    candidate_roots = [
        Path(sys.executable).resolve().parent,
        script_path.parent.parent.parent,
        Path.cwd(),
    ]
    for root in candidate_roots:
        stdlib_dir = root / "ai_stdlib"
        if stdlib_dir.exists():
            stdlib_text = str(stdlib_dir)
            if stdlib_text not in sys.path:
                sys.path.insert(0, stdlib_text)


def _prepend_engine_root(script_path: Path) -> None:
    engine_root = script_path.parent.parent
    if (engine_root / "app").exists():
        engine_root_text = str(engine_root)
        if engine_root_text not in sys.path:
            sys.path.insert(0, engine_root_text)

    cwd_text = str(Path.cwd())
    if cwd_text not in sys.path:
        sys.path.insert(0, cwd_text)


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
    _prepend_ai_site_packages(script_path)
    _prepend_engine_root(script_path)
    sys.argv = [str(script_path), *sys.argv[2:]]
    runpy.run_path(str(script_path), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
