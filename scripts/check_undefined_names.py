"""Fail on names that are used but never defined (pyflakes UndefinedName / UndefinedLocal / UndefinedExport).

The first remediation deleted and moved code, and nothing in CI noticed when callers were left behind
(post-remediation audit F-01..F-05). pyflakes finds that class of mistake in seconds, so it is a gate.
Unused imports and variables are deliberately not enforced here.

    python scripts/check_undefined_names.py
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from pyflakes import checker, messages
except ImportError:
    sys.exit("pyflakes is required: python -m pip install pyflakes  (it is in the 'dev' extra)")

REPO = Path(__file__).resolve().parents[1]
ROOTS = ("image_triage", "aiculler", "scripts", "tests", "benchmarks", "packaging", "sandboxes")
ROOT_FILES = ("app.py", "setup_msi.py", "freeze_support.py")
FATAL = (messages.UndefinedName, messages.UndefinedLocal, messages.UndefinedExport)


def python_files() -> list[Path]:
    files = [p for root in ROOTS if (REPO / root).is_dir() for p in (REPO / root).rglob("*.py")]
    files += [REPO / name for name in ROOT_FILES if (REPO / name).exists()]
    return sorted(p for p in files if "__pycache__" not in p.parts)


def check(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = compile(source, str(path), "exec", flags=__import__("ast").PyCF_ONLY_AST)
    except SyntaxError as exc:
        return [f"{path.relative_to(REPO).as_posix()}:{exc.lineno}: syntax error: {exc.msg}"]
    result = checker.Checker(tree, filename=str(path))
    return [
        f"{path.relative_to(REPO).as_posix()}:{m.lineno}: {m.message % m.message_args}"
        for m in sorted(result.messages, key=lambda m: m.lineno)
        if isinstance(m, FATAL)
    ]


def main() -> int:
    problems = [line for path in python_files() for line in check(path)]
    for line in problems:
        print(line)
    print(f"{len(problems)} undefined name(s) in {len(python_files())} files")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
