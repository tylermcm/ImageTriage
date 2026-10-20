from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

RUNNER = Path(__file__).resolve().parents[1] / "packaging" / "ai_python_runner.py"


def _run(args: list[str], *, pythonpath: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("IMAGE_TRIAGE_AI_")}
    env["PYTHONPATH"] = str(pythonpath) if pythonpath else ""
    return subprocess.run(
        [sys.executable, str(RUNNER), *args], env=env, capture_output=True, text=True, timeout=120,
    )


def _make_package(root: Path) -> None:
    pkg = root / "demo_pkg" / "sub"
    pkg.mkdir(parents=True)
    (root / "demo_pkg" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "helper.py").write_text("VALUE = 'from-helper'\n", encoding="utf-8")
    (pkg / "tool.py").write_text(
        "import sys\n"
        "from .helper import VALUE\n"
        "from .. import sub\n"
        "if __name__ == '__main__':\n"
        "    print(__name__, VALUE, sys.argv[1:], sys.argv[0].endswith('tool.py'))\n"
        "    sys.exit(int(sys.argv[1]))\n",
        encoding="utf-8",
    )


def test_module_mode_runs_module_with_relative_imports_args_and_exit_code(tmp_path: Path) -> None:
    _make_package(tmp_path)
    result = _run(["-m", "demo_pkg.sub.tool", "7", "--device", "cpu"], pythonpath=tmp_path)
    assert result.returncode == 7, result.stderr
    assert result.stdout.strip() == "__main__ from-helper ['7', '--device', 'cpu'] True"


def test_module_mode_zero_exit(tmp_path: Path) -> None:
    _make_package(tmp_path)
    assert _run(["-m", "demo_pkg.sub.tool", "0"], pythonpath=tmp_path).returncode == 0


def test_module_mode_requires_a_module_name() -> None:
    result = _run(["-m"])
    assert result.returncode == 2
    assert "Usage" in result.stderr


def test_module_mode_unknown_module_fails(tmp_path: Path) -> None:
    result = _run(["-m", "no_such_pkg.thing"], pythonpath=tmp_path)
    assert result.returncode != 0
    assert "no_such_pkg" in result.stderr


def test_script_mode_still_works(tmp_path: Path) -> None:
    script = tmp_path / "script.py"
    script.write_text("import sys\nprint('script', sys.argv[1:])\nsys.exit(4)\n", encoding="utf-8")
    result = _run([str(script), "a", "b"])
    assert result.returncode == 4, result.stderr
    assert result.stdout.strip() == "script ['a', 'b']"
