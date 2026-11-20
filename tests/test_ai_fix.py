"""An AI tool's error comes with its fix (ai_fix.py): a missing file of the app is repaired by Windows Installer, a missing runtime or
model by the AI Center, anything else offers nothing."""
import sys

from image_triage import ai_fix


def test_errors_are_sorted_by_what_fixes_them():
    assert ai_fix.kind("MaskEngine host script is missing.") == ai_fix.REPAIR_APP
    assert ai_fix.kind("Upscale worker unavailable: no such file") == ai_fix.REPAIR_APP
    assert ai_fix.kind("Super Resolution needs the updated RAW preview worker.") == ai_fix.REPAIR_APP
    assert ai_fix.kind("Super Resolution needs the AI runtime (AI > AI Center).") == ai_fix.AI_CENTER
    assert ai_fix.kind("The Super Resolution model is not installed. Install it in AI > AI Center.") == ai_fix.AI_CENTER
    assert ai_fix.kind("No depth model is installed.") == ai_fix.AI_CENTER
    assert ai_fix.kind("Super Resolution was cancelled") == ""
    assert ai_fix.kind("") == ""
    assert ai_fix.LABELS[ai_fix.REPAIR_APP] == "Repair Image Triage"


def test_a_repair_asks_windows_installer_for_the_missing_files_only(monkeypatch):
    ran = []
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(ai_fix, "installed_product_code", lambda: "{11111111-2222-3333-4444-555555555555}")
    assert ai_fix.repair_app(run=ran.append) == ""
    assert ran == [["msiexec", "/fp", "{11111111-2222-3333-4444-555555555555}"]]
    monkeypatch.setattr(ai_fix, "installed_product_code", lambda: None)
    assert "Reinstall" in ai_fix.repair_app(run=ran.append) and len(ran) == 1


def test_a_source_checkout_is_told_where_the_file_comes_from(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert "source folder" in ai_fix.repair_app(run=lambda *a: None)


def test_an_error_an_ai_tool_shows_always_has_a_fix_but_a_toast_only_for_an_ai_problem():
    assert ai_fix.kind("The fill model did not answer.", ai_tool=True) == ai_fix.AI_CENTER       # anything else: the AI Center
    assert ai_fix.kind("The fill model did not answer.") == ""
    assert ai_fix.kind("AI Erase was cancelled", ai_tool=True) == ""                               # not a fault


def test_an_ai_error_toast_carries_its_fix():
    from image_triage.qml_shell.menu_model import ToastModel

    opened = []
    toasts = ToastModel()
    toasts.open_ai_center = lambda: opened.append(True)
    toasts.push("error", "The batch stopped", "Super Resolution needs the AI runtime (AI > AI Center).")
    toasts.push("error", "The batch stopped", "The disk is full.")
    first, second = toasts.items[-2:]
    assert first["action"] == "Fix in AI Center" and second["action"] == ""
    toasts.act(first["id"]) if hasattr(toasts, "act") else toasts._callbacks[first["id"]]()
    assert opened == [True]
