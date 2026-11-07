"""Keep retired desktop frontends out of the application and its builds."""
import ast
import os
import re
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_application_has_no_widgets_imports_or_retired_screen_modules():
    retired = ["window.py", "preview.py", "grid.py", "settings_dialog.py", "qml_shell/popout.py", "pocketdrop/panel.py"]
    assert not [name for name in retired if (ROOT / "image_triage" / name).exists()]
    assert not list((ROOT / "image_triage/ui").rglob("*.py"))
    violations = []
    for path in (ROOT / "image_triage").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            modules = ([node.module or ""] if isinstance(node, ast.ImportFrom)
                       else [name.name for name in node.names] if isinstance(node, ast.Import) else [])
            from_widgets = isinstance(node, ast.ImportFrom) and node.module == "PySide6" and any(name.name == "QtWidgets" for name in node.names)
            if from_widgets or any(module.startswith("PySide6.QtWidgets") or module == "image_triage.ui" or module.startswith("image_triage.ui.") for module in modules):
                violations.append(str(path.relative_to(ROOT)))
    assert not violations, violations


def test_static_qml_image_assets_exist():
    missing = []
    for path in (ROOT / "image_triage/qml_shell/qml").rglob("*.qml"):
        for target in re.findall(r'Qt\.resolvedUrl\("([^"{}]+)"\)', path.read_text(encoding="utf-8")):
            if Path(target).suffix in {".png", ".svg", ".ico", ".jpg"} and not (path.parent / target).is_file():
                missing.append((path.name, target))
    assert not missing, missing


def test_qml_application_runs_with_widgets_imports_blocked(tmp_path):
    from PIL import Image
    folder = tmp_path / "synthetic"
    folder.mkdir()
    Image.new("RGB", (120, 80), "blue").save(folder / "frame.jpg")
    capture = tmp_path / "qml-only.png"
    code = '''
import importlib.abc, sys
class NoWidgets(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "PySide6.QtWidgets":
            raise AssertionError("The QML application tried to import Widgets")
sys.meta_path.insert(0, NoWidgets())
from image_triage.qml_shell.app import main
raise SystemExit(main(sys.argv[1:]))
'''
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QSG_RHI_BACKEND="software")
    env.pop("IMAGE_TRIAGE_CLI_CULLER_RUNTIME", None)
    env.pop("IMAGE_TRIAGE_CLI_CULLER_VALIDATION", None)
    result = subprocess.run([sys.executable, "-c", code, "--folder", str(folder), "--size", "1100x800",
                             "--grab", str(capture), "--grab-wait", "0.5"], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert capture.is_file() and capture.stat().st_size > 1000
