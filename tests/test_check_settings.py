"""The live checks (scripts/check_*.py) pin settings and restore them at the end; one that died left them pinned in the profile the app
uses. They run against a throwaway settings key instead."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import _check_settings as check_settings  # noqa: E402
import image_triage.app_identity as identity  # noqa: E402

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the settings store is the Windows registry")

CHECKS = ("check_camera_raw_first.py", "check_camera_raw_recipe.py", "check_photocraft_handoff.py")


@pytest.fixture
def real_key(monkeypatch):
    original = identity._REGISTRY_KEY
    monkeypatch.setattr(identity, "_REGISTRY_KEY", original)  # put back whatever the test changed
    yield original
    check_settings._forget()


def test_after_the_call_settings_go_to_the_throwaway_key_and_not_the_real_one(real_key):
    from PySide6.QtCore import QSettings

    real = QSettings(real_key, QSettings.Format.NativeFormat)
    before = {key: real.value(key) for key in real.allKeys()}
    check_settings.use_throwaway_settings()
    pinned = identity.user_settings()
    # (the test suite's hermetic QSettings keeps each key in a file named after it, spaces as underscores)
    assert "Image_Triage_Check" in pinned.fileName().replace(" ", "_"), pinned.fileName()
    pinned.setValue("preview/editor_on_demand", False)
    pinned.sync()
    assert {key: real.value(key) for key in real.allKeys()} == before, "the profile the app uses was not touched"


def test_the_throwaway_key_is_emptied_when_the_check_ends(real_key):
    from PySide6.QtCore import QSettings

    check_settings.use_throwaway_settings()
    identity.user_settings().setValue("preview/editor_on_demand", False)
    check_settings._forget()
    assert QSettings(check_settings.CHECK_KEY, QSettings.Format.NativeFormat).allKeys() == []


def test_the_throwaway_key_is_not_the_apps_key():
    assert check_settings.CHECK_KEY != identity._REGISTRY_KEY and "Check" in check_settings.CHECK_KEY


@pytest.mark.parametrize("name", CHECKS)
def test_each_check_switches_before_it_first_touches_the_settings(name):
    tree = ast.parse((REPO / "scripts" / name).read_text(encoding="utf-8"))
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    calls = sorted(
        ((node.lineno, node.func.id) for node in ast.walk(main) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
         and node.func.id in {"use_throwaway_settings", "user_settings"}),
    )
    names = [name for _line, name in calls]
    assert "use_throwaway_settings" in names, f"{name} never switches"
    assert names.index("use_throwaway_settings") < names.index("user_settings"), f"{name} reads the settings before switching"


@pytest.mark.parametrize("name", CHECKS)
def test_each_check_s_stand_in_window_has_what_the_controller_asks_of_a_window(name):
    """The controller keeps the photos' edit states in the window's thumbnail manager: a stand-in without one made
    ``_refresh_photocraft_save`` fail inside its slot, so the grid was never told about a saved render."""
    tree = ast.parse((REPO / "scripts" / name).read_text(encoding="utf-8"))
    assigned = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Attribute) and t.attr == "_window" for t in node.targets)
        and isinstance(node.value, ast.Call)
    ]
    assert assigned, f"{name} builds no stand-in window"
    for node in assigned:
        assert "thumbnail_manager" in {keyword.arg for keyword in node.value.keywords}, name
