"""Action sweep: trigger every enabled QAction of a real MainWindow and fail on any exception (plan DC-0.3).

The post-remediation audit found four crashes that sat behind a menu entry, a dialog or an overflow and that no test
touched, because only a handful of test files drive a real window. This is the characterization net that makes the
MainWindow decomposition (docs/mainwindow_decomposition_plan.md) safe: after every slice the whole menu, toolbar and
shortcut surface still has to run.

What is deliberately neutralised, so a sweep can never touch real user data or the network:
  * every modal dialog is closed by a janitor timer (the shared harness also refuses ``exec()``), and every question
    box answers "No", so nothing is confirmed, deleted, installed or uninstalled;
  * subprocess launches, ``os.startfile``, URL opens and network requests raise ``_ExternalLaunch`` instead of running;
  * the managed AI root is redirected into the test's own folder.
An action that is *blocked* by one of these counts as reached, not as failed: the handler ran up to the boundary.
"""
from __future__ import annotations

import os
import sys
import traceback

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from tests.harness import install_dialog_guards, make_jpegs, open_folder
from tests.test_preview_lazy_build import _fresh_window

# Actions that end the session or are dangerous by nature; they are listed so the exclusion is visible.
_SKIPPED = {"exit", "quit"}

# Failures the sweep found that are known and tracked elsewhere. Each entry is (action text, exception type name) with the
# reason; the test fails if an entry no longer reproduces (so the list cannot rot) or if anything new appears.
KNOWN_FAILURES: dict[tuple[str, str], str] = {}


class _ExternalLaunch(Exception):
    """Raised instead of starting a process, opening a URL or touching the network."""


def _neutralise_the_outside_world(monkeypatch, tmp_path):
    import subprocess
    import urllib.request

    import image_triage.ai_paths as ai_paths
    from PySide6.QtGui import QDesktopServices

    def blocked(*_args, **_kwargs):
        raise _ExternalLaunch("external launch")

    monkeypatch.setattr(subprocess, "Popen", blocked)
    monkeypatch.setattr(subprocess, "run", blocked)
    monkeypatch.setattr(subprocess, "check_output", blocked)
    monkeypatch.setattr(os, "startfile", blocked, raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(lambda *a, **k: False))
    safe_ai_root = tmp_path / "managed_ai_root"
    monkeypatch.setattr(ai_paths, "default_managed_ai_root", lambda: safe_ai_root)
    monkeypatch.setenv("IMAGE_TRIAGE_AI_ROOT", str(safe_ai_root))


def _start_janitor(window) -> QTimer:
    """Close whatever popup or modal a handler opened, so a nested event loop can never wait for a human."""

    def sweep_up():
        popup = QApplication.activePopupWidget()
        if popup is not None:
            popup.close()
            return
        modal = QApplication.activeModalWidget()
        if modal is not None:
            modal.reject() if isinstance(modal, QDialog) else modal.close()

    timer = QTimer(window)
    timer.setInterval(100)
    timer.timeout.connect(sweep_up)
    timer.start()
    return timer


def _where(exc: BaseException) -> str:
    frame = traceback.extract_tb(exc.__traceback__)[-1]
    return f"{os.path.basename(frame.filename)}:{frame.lineno}"


def _classify(outcome: dict, text: str, exc: BaseException) -> None:
    if isinstance(exc, _ExternalLaunch):
        outcome["blocked_external"].append(text)
    elif isinstance(exc, AssertionError) and "would block a headless test" in str(exc):
        outcome["blocked_modal"].append(text)
    else:
        outcome["failed"].append((text, type(exc).__name__, f"{exc} [{_where(exc)}]"))


def sweep_actions(window, monkeypatch) -> dict:
    """Trigger every distinct, enabled, non-menu action once. Returns what happened to each.

    PySide does not propagate an exception out of a Qt slot: it prints it and hands it to ``sys.excepthook``, so
    ``action.trigger()`` never raises. The hook is replaced for the duration so those exceptions are collected.
    """
    outcome = {"triggered": [], "disabled": [], "skipped": [], "blocked_modal": [], "blocked_external": [], "failed": []}
    caught: list[BaseException] = []
    monkeypatch.setattr(sys, "excepthook", lambda _kind, value, _tb: caught.append(value))
    # The same command is often registered more than once (the action registry, menu copies, toolbar mirrors),
    # and some copies are disabled placeholders: trigger the first *enabled* one of each (objectName, text).
    # Enabled-ness is snapshotted *before* anything runs: an early action (a refresh, a busy state) can disable
    # the rest of the menu, which would make the result depend on the order of ``findChildren``.
    groups: dict[tuple[str, str], list[tuple[QAction, bool]]] = {}
    for action in list(window.findChildren(QAction)):
        try:
            text = action.text().replace("&", "").strip()
            if text and action.menu() is None:
                groups.setdefault((action.objectName(), text), []).append((action, action.isEnabled()))
        except RuntimeError:
            continue
    for (_name, text), candidates in groups.items():
        try:
            if text.lower() in _SKIPPED or any(a.menuRole() == QAction.MenuRole.QuitRole for a, _ in candidates):
                outcome["skipped"].append(text)
                continue
            action = next((a for a, was_enabled in candidates if was_enabled), None)
            if action is None:
                outcome["disabled"].append(text)
                continue
            caught.clear()
            try:
                action.trigger()
                QApplication.processEvents()
            except Exception as exc:  # noqa: BLE001 - direct (non-slot) raises
                caught.append(exc)
            if caught:
                for exc in caught:
                    _classify(outcome, text, exc)
            else:
                outcome["triggered"].append(text)
        except RuntimeError as exc:
            # An earlier action rebuilt a menu and this action's C++ object went with it: not a defect.
            if "already deleted" not in str(exc):
                raise
    return outcome


@pytest.fixture(params=["folder_open", "three_selected"])
def sweep_window(request, tmp_path, monkeypatch):
    """Two window states, because most actions are only enabled once there is a selection."""
    recorder = install_dialog_guards(monkeypatch)
    recorder.question_answer = QMessageBox.StandardButton.No
    _neutralise_the_outside_world(monkeypatch, tmp_path)
    folder = tmp_path / "shoot"
    folder.mkdir()
    make_jpegs(folder, [f"img{i}.jpg" for i in range(6)])
    with _fresh_window() as window:
        janitor = _start_janitor(window)
        open_folder(window, str(folder), 6)
        if request.param == "three_selected":
            window.grid.set_selected_indexes([0, 1, 2], current_index=1)
            QApplication.processEvents()
            window._update_action_states()
        yield window
        janitor.stop()


def test_every_enabled_action_runs_without_raising(sweep_window, monkeypatch):
    outcome = sweep_actions(sweep_window, monkeypatch)
    print({k: len(v) for k, v in outcome.items()})  # visible with -s / -rP; the floor below is the real guard
    print('not reached (disabled in this state):', sorted(outcome['disabled']))

    # A sweep that reaches almost nothing proves nothing: guard against the harness silently going blind.
    reached = len(outcome["triggered"]) + len(outcome["blocked_modal"]) + len(outcome["blocked_external"])
    assert reached >= 120, f"only {reached} actions were reached; outcome: { {k: len(v) for k, v in outcome.items()} }"

    found = {(text, kind): detail for text, kind, detail in outcome["failed"]}
    unexpected = {key: detail for key, detail in found.items() if key not in KNOWN_FAILURES}
    assert not unexpected, "actions raised:\n" + "\n".join(f"  {t} -> {k}: {d}" for (t, k), d in unexpected.items())

    stale = [key for key in KNOWN_FAILURES if key not in found]
    assert not stale, f"KNOWN_FAILURES entries that no longer fail (remove them): {stale}"
