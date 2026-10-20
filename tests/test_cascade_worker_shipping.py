from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

import freeze_support

ROOT = Path(__file__).resolve().parents[1]


def test_include_list_ships_the_worker_closure_as_plain_files() -> None:
    includes = dict(freeze_support.cascade_worker_include_files())
    tree = freeze_support.CASCADE_WORKER_TREE
    for rel in (
        "image_triage/__init__.py",
        "image_triage/cascade/__init__.py",
        "image_triage/cascade/blink.py",
        "image_triage/cascade/faces.py",
        "image_triage/cascade/rankers/__init__.py",
        "image_triage/cascade/rankers/worker.py",
        "image_triage/cascade/rankers/protocol.py",
        "image_triage/cascade/rankers/spec.py",
        "image_triage/cascade/rankers/bundles.py",
        "image_triage/cascade/rankers/_maniqa_net.py",
        "image_triage/ai_manifest.py",
        "image_triage/ai_model_store.py",
    ):
        assert includes.get(str(ROOT / rel)) == f"{tree}/{rel}", rel
    layout = freeze_support.FreezeAssetLayout(
        ai_site_packages_source=ROOT / "a", ai_stdlib_source=ROOT / "b", ai_binary_modules_source=ROOT / "c",
    )
    try:
        files = layout.include_files
    except FileNotFoundError:  # pocketdrop.dll not built on this machine
        return
    assert set(includes.items()) <= set(files)


def test_runner_starts_the_worker_module_from_the_shipped_tree(tmp_path: Path) -> None:
    for source, destination in freeze_support.cascade_worker_include_files():
        target = tmp_path / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    env = {k: v for k, v in os.environ.items() if not k.startswith("IMAGE_TRIAGE_AI_")}
    env["PYTHONPATH"] = str(tmp_path / freeze_support.CASCADE_WORKER_TREE)
    result = subprocess.run(
        [sys.executable, str(ROOT / "packaging" / "ai_python_runner.py"), "-m", freeze_support.CASCADE_WORKER_MODULE, "--help"],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "--model-dir" in result.stdout


def test_no_shipped_module_imports_pyside_at_module_level() -> None:
    for name, path in freeze_support.worker_import_closure().items():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:  # PySide6 inside a guarded function is not an import of the worker
            if isinstance(node, ast.Import):
                assert not any(a.name.split(".")[0] == "PySide6" for a in node.names), name
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] != "PySide6", name


def test_worker_imports_from_a_tree_holding_only_the_shipped_files(tmp_path: Path) -> None:
    shipped = tmp_path / freeze_support.CASCADE_WORKER_TREE
    for source, destination in freeze_support.cascade_worker_include_files():
        target = tmp_path / destination
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    assert (shipped / "image_triage" / "cascade" / "rankers" / "worker.py").is_file()
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = str(shipped)  # only the shipped tree; cwd is the temp dir, not a checkout
    code = (
        "import sys, image_triage\n"
        f"assert image_triage.__file__.startswith({str(shipped)!r}), image_triage.__file__\n"
        "import image_triage.cascade.rankers.worker as w\n"
        "assert not any(m == 'PySide6' or m.startswith('PySide6.') for m in sys.modules)\n"
        "print('ok', w.__name__)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], env=env, cwd=tmp_path, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("ok")


def test_client_points_pythonpath_at_the_shipped_tree_when_frozen(monkeypatch) -> None:
    from image_triage.cascade.rankers import client

    assert client._worker_package_root() == str(ROOT)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert client._worker_package_root() == str(Path(sys.executable).resolve().parent / "ai_workers" / "py")
    assert freeze_support.CASCADE_WORKER_TREE == "ai_workers/py"
