"""Name-based reachability report (WI-2.1).

    py -3.13 scripts/reachability_report.py            # print the report
    py -3.13 scripts/reachability_report.py --json out.json

This is *name-based* analysis: a symbol counts as used if its name appears
anywhere else (code, tests, or a string literal). It cannot see truly dynamic
access, so every finding is a *candidate* that a human verifies before deleting
(see docs/deletion_ledger.md). Anything listed in scripts/reachability_whitelist.txt
is treated as used. The whitelist also holds Qt override names.

Sections: orphan modules, unused imports, unreferenced methods (large classes),
unreferenced module-level defs, signals never connected or never emitted.
"""
from __future__ import annotations

import argparse
import ast
import collections
import fnmatch
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "image_triage"
WHITELIST_FILE = Path(__file__).with_name("reachability_whitelist.txt")

# Classes whose methods are audited (the god objects and big dialogs).
AUDITED_CLASSES = {
    "window.py": ["MainWindow"],
    "ui/photo_editor_panel.py": ["PhotoEditorPanel"],
    "preview.py": ["FullScreenPreview"],
    "grid.py": ["ThumbnailGridView"],
    "ui/docks.py": ["InspectorPanel", "WorkspaceDocks"],
    "settings_dialog.py": ["WorkflowSettingsDialog"],
    "library_store.py": ["LibraryStore"],
    "decision_store.py": ["DecisionStore"],
    "catalog/repository.py": ["CatalogRepository"],
    "ui/mask_overlay.py": ["MaskOverlay"],
}


def load_whitelist() -> list[str]:
    if not WHITELIST_FILE.exists():
        return []
    patterns = []
    for line in WHITELIST_FILE.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            patterns.append(line)
    return patterns


def whitelisted(identifier: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(identifier, pattern) for pattern in patterns)


def read_tree(base: Path, skip: tuple[str, ...]) -> dict[Path, str]:
    found: dict[Path, str] = {}
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for name in filenames:
            if name.endswith(".py"):
                path = Path(dirpath) / name
                found[path] = path.read_text(encoding="utf-8")
    return found


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def string_literals(text: str) -> set[str]:
    names: set[str] = set()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return names
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and len(node.value) < 80:
            names.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", node.value))
    return names


def analyse() -> dict[str, list[str]]:
    patterns = load_whitelist()
    source = read_tree(PKG, ("__pycache__", "assets"))
    tests = read_tree(ROOT / "tests", ("__pycache__",))
    trees = {path: ast.parse(text) for path, text in source.items()}
    all_text = "\n".join(source.values())
    test_text = "\n".join(tests.values())
    literals: set[str] = set()
    for text in list(source.values()) + list(tests.values()):
        literals |= string_literals(text)
    word = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
    file_counts = {path: collections.Counter(word.findall(text)) for path, text in source.items()}
    word_counts: collections.Counter = collections.Counter()
    for counts in file_counts.values():
        word_counts.update(counts)
    definition_counts: collections.Counter = collections.Counter()
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                definition_counts[node.name] += 1
    test_words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", test_text))
    report: dict[str, list[str]] = {}

    # ---- orphan modules
    imported: set[str] = set()
    for path, tree in trees.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[-1])
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[-1] for alias in node.names)
    orphans = []
    for path in sorted(source):
        stem = path.stem
        if stem in {"__init__", "__main__"} or stem in imported or stem in literals:
            continue
        identifier = f"{rel(path)}"
        if whitelisted(identifier, patterns):
            continue
        orphans.append(f"{identifier}  ({len(source[path].splitlines())} lines)")
    report["orphan_modules"] = orphans

    # ---- unused imports
    unused = []
    for path, tree in trees.items():
        if path.name == "__init__.py":
            continue
        imported_names: dict[str, int] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name != "*":
                        imported_names[alias.asname or alias.name] = node.lineno
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported_names[(alias.asname or alias.name).split(".")[0]] = node.lineno
        counts = file_counts[path]
        for name, line in sorted(imported_names.items(), key=lambda item: item[1]):
            identifier = f"{rel(path)}::{name}"
            if counts[name] <= 1 and name not in {"annotations"} and name not in literals:
                if not whitelisted(identifier, patterns):
                    unused.append(f"{identifier}  (line {line})")
    report["unused_imports"] = unused

    # ---- unreferenced methods in audited classes
    qt_overrides = {p for p in patterns if "::" not in p and "*" not in p}
    methods = []
    test_only = []
    for relative, class_names in AUDITED_CLASSES.items():
        path = PKG / relative
        if path not in trees:
            continue
        for node in ast.walk(trees[path]):
            if not (isinstance(node, ast.ClassDef) and node.name in class_names):
                continue
            for member in node.body:
                if not isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                name = member.name
                identifier = f"{rel(path)}::{node.name}.{name}"
                if name.startswith("__") or name in qt_overrides or name in literals:
                    continue
                if whitelisted(identifier, patterns):
                    continue
                own = file_counts[path][name]
                if (own - 1) + (word_counts[name] - own) > 0:
                    continue
                size = (member.end_lineno or member.lineno) - member.lineno + 1
                entry = f"{identifier}  ({size} lines, line {member.lineno})"
                (test_only if name in test_words else methods).append(entry)
    report["unreferenced_methods"] = methods
    report["methods_referenced_only_by_tests"] = test_only

    # ---- unreferenced module-level defs
    defs = []
    for path, tree in trees.items():
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                name = node.name
                identifier = f"{rel(path)}::{name}"
                if name.startswith("__") or name == "main" or name in literals or whitelisted(identifier, patterns):
                    continue
                if word_counts[name] - definition_counts[name] == 0:
                    size = (node.end_lineno or node.lineno) - node.lineno + 1
                    tag = " (tests only)" if name in test_words else ""
                    defs.append(f"{identifier}  ({size} lines){tag}")
    report["unreferenced_module_defs"] = defs

    # ---- MainWindow attributes that are assigned but never read anywhere
    write_only = []
    window_path = PKG / "window.py"
    for node in ast.walk(trees[window_path]):
        if not (isinstance(node, ast.ClassDef) and node.name == "MainWindow"):
            continue
        stored: dict[str, int] = {}
        loaded: set[str] = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) and sub.value.id == "self":
                if isinstance(sub.ctx, ast.Store):
                    stored.setdefault(sub.attr, sub.lineno)
                else:
                    loaded.add(sub.attr)
        for attr, line in sorted(stored.items(), key=lambda item: item[1]):
            identifier = f"window.py::MainWindow.{attr}"
            if attr in loaded or attr in literals or whitelisted(identifier, patterns):
                continue
            reads_elsewhere = sum(
                len(re.findall(r"\.%s\b" % re.escape(attr), text))
                for path, text in list(source.items()) + list(tests.items())
                if path != window_path
            )
            if reads_elsewhere == 0:
                write_only.append(f"{identifier}  (first assigned line {line})")
    report["write_only_window_attributes"] = write_only

    # ---- signals never connected / never emitted
    signals = []
    for path, tree in trees.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for stmt in node.body:
                if (
                    isinstance(stmt, ast.Assign)
                    and isinstance(stmt.value, ast.Call)
                    and getattr(stmt.value.func, "id", getattr(stmt.value.func, "attr", "")) == "Signal"
                ):
                    for target in stmt.targets:
                        if isinstance(target, ast.Name):
                            name = target.id
                            connects = len(re.findall(r"\.%s\.connect\(" % name, all_text))
                            emits = len(re.findall(r"\.%s\.emit\(" % name, all_text))
                            identifier = f"{rel(path)}::{node.name}.{name}"
                            if (connects == 0 or emits == 0) and not whitelisted(identifier, patterns):
                                signals.append(f"{identifier}  connect={connects} emit={emits}")
    report["signals_unconnected_or_unemitted"] = signals
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", help="also write the report as JSON to this path")
    parser.add_argument("--fail-on", nargs="*", default=[], help="section names that must be empty (exit 1 otherwise)")
    args = parser.parse_args()
    report = analyse()
    for section, items in report.items():
        print(f"== {section}: {len(items)}")
        for item in items:
            print(f"   {item}")
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 1 if any(report.get(name) for name in args.fail_on) else 0


if __name__ == "__main__":
    sys.exit(main())
