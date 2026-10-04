"""Architecture ratchet and reference-integrity gates (docs/mainwindow_decomposition_plan.md, DC-0.4).

These are static (see scripts/architecture_report.py) and run in about a second. They exist because the first
remediation moved and deleted code without anything checking that every caller still resolved: the post-remediation
audit found five dangling references that no test covered.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import architecture_report as report  # noqa: E402


def test_no_architecture_number_got_worse():
    problems = report.check_ratchet(report.collect_metrics(), report.load_ratchet())
    assert not problems, "\n".join(problems)


def test_modules_declared_ui_free_do_not_import_qtwidgets():
    problems = report.check_ui_free(report.load_ratchet().get("ui_free_modules", []))
    assert not problems, "\n".join(problems)


def test_every_self_attribute_read_in_the_window_and_controllers_is_defined():
    problems = report.check_self_members()
    assert not problems, "\n".join(problems)


def test_every_internal_import_resolves_to_a_defined_name():
    problems = report.check_internal_imports()
    assert not problems, "\n".join(problems)


def test_update_never_raises_a_recorded_number():
    recorded = {"metrics": {"a": 5, "b": 5}, "private_accesses_by_module": {"x.py": 4}}
    current = {"metrics": {"a": 3, "b": 9}, "private_accesses_by_module": {"x.py": 2, "y.py": 1}}
    out = report.updated_ratchet(current, recorded)
    assert out["metrics"] == {"a": 3, "b": 5}
    assert out["private_accesses_by_module"] == {"x.py": 2}  # a new module is not registered by accident
    assert len(report.check_ratchet(current, recorded)) == 2  # b rose, and y.py is a new reach
    # a freshly extracted controller is registered deliberately, at its current count
    registered = report.updated_ratchet(current, recorded, accept_new_modules=True)
    assert registered["private_accesses_by_module"] == {"x.py": 2, "y.py": 1}
    assert registered["metrics"] == {"a": 3, "b": 5}  # still never raised


def test_informational_totals_may_rise_when_a_controller_is_registered():
    recorded = {"metrics": {"private_accesses_total": 10}, "private_accesses_by_module": {"x.py": 10}}
    current = {"metrics": {"private_accesses_total": 60}, "private_accesses_by_module": {"x.py": 10, "new_controller.py": 50}}
    assert report.check_ratchet(current, {**recorded, "private_accesses_by_module": {"x.py": 10, "new_controller.py": 50}}) == []
    assert report.updated_ratchet(current, recorded)["metrics"]["private_accesses_total"] == 60
