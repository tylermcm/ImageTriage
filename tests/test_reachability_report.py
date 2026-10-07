from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "reachability_report.py"


def _load():
    spec = importlib.util.spec_from_file_location("reachability_report", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_report_has_every_section_and_is_repeatable() -> None:
    module = _load()
    first = module.analyse()
    second = module.analyse()

    assert set(first) == {
        "orphan_modules",
        "unused_imports",
        "unreferenced_methods",
        "methods_referenced_only_by_tests",
        "unreferenced_module_defs",
        "write_only_window_attributes",
        "signals_unconnected_or_unemitted",
    }
    assert first == second


def test_whitelist_patterns_match_identifiers() -> None:
    module = _load()

    assert module.whitelisted("window.py::MainWindow.foo", ["window.py::MainWindow.*"])
    assert not module.whitelisted("preview.py::X.foo", ["window.py::MainWindow.*"])
