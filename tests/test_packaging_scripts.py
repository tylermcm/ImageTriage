from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read_tree(path: str) -> ast.Module:
    return ast.parse((ROOT / path).read_text(encoding="utf-8"), filename=path)


def _string_constants(tree: ast.AST) -> set[str]:
    values: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            values.add(node.value)
    return values


def _dict_list_literals(tree: ast.AST, assignment_name: str, key_name: str) -> list[str]:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == assignment_name for target in node.targets):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for key, value in zip(node.value.keys, node.value.values):
            if not isinstance(key, ast.Constant) or key.value != key_name:
                continue
            if not isinstance(value, ast.List):
                return []
            return [
                item.value
                for item in value.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            ]
    return []


class PackagingScriptTests(unittest.TestCase):
    def test_frozen_apps_include_onnxruntime_for_in_process_masking(self) -> None:
        for setup_path in ("setup_msi.py", "setup_linux.py"):
            includes = _dict_list_literals(
                _read_tree(setup_path),
                "build_exe_options",
                "includes",
            )
            self.assertIn("onnxruntime", includes, setup_path)

    def test_linux_appimage_packages_current_frozen_helpers(self) -> None:
        constants = _string_constants(_read_tree("setup_linux.py"))

        self.assertIn("ImageTriage", constants)
        self.assertIn("ai_python_runner", constants)
        self.assertIn("ai_runtime_installer", constants)
        self.assertIn("image_triage_cleanup", constants)

    def test_linux_appimage_keeps_pip_for_frozen_runtime_installer(self) -> None:
        tree = _read_tree("setup_linux.py")
        constants = _string_constants(tree)

        self.assertIn("pip", constants)
        self.assertIn("pip._internal", constants)
        self.assertNotIn("pip", _dict_list_literals(tree, "build_exe_options", "excludes"))


if __name__ == "__main__":
    unittest.main()
