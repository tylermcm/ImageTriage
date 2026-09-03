from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _load_shortcuts_module():
    """Load image_triage/ui/shortcuts.py directly without triggering the
    image_triage package __init__ (which imports modules that don't work on
    Python <3.10 due to dataclass(slots=True))."""

    path = PROJECT_ROOT / "image_triage" / "ui" / "shortcuts.py"
    spec = importlib.util.spec_from_file_location("image_triage_shortcuts_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_shortcuts = _load_shortcuts_module()
SHORTCUT_REGISTRY = _shortcuts.SHORTCUT_REGISTRY
apply_shortcut_overrides = _shortcuts.apply_shortcut_overrides
load_shortcut_overrides = _shortcuts.load_shortcut_overrides
save_shortcut_overrides = _shortcuts.save_shortcut_overrides


class _FakeSettings:
    """In-memory stand-in for QSettings supporting value/setValue/remove."""

    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self._store: dict[str, str] = dict(initial or {})

    def value(self, key: str, default: object = None, type=None):  # noqa: A002 - mimics QSettings
        return self._store.get(key, default)

    def setValue(self, key: str, value: object) -> None:
        self._store[key] = str(value)

    def remove(self, key: str) -> None:
        self._store.pop(key, None)


class _FakeAction:
    def __init__(self) -> None:
        self.shortcut_sequences: list[str] = []

    def setShortcut(self, sequence) -> None:
        # QKeySequence.__str__ returns the platform string ("Ctrl+Shift+L" etc.)
        # which is exactly what we want to assert against.
        self.shortcut_sequences.append(sequence.toString())


class ShortcutOverrideHelpersTests(unittest.TestCase):
    def test_registry_has_unique_attrs_and_includes_known_actions(self) -> None:
        attr_names = [row[0] for row in SHORTCUT_REGISTRY]
        self.assertEqual(len(attr_names), len(set(attr_names)), "Duplicate attr_name in registry")
        self.assertIn("quick_rerank_ai_culling", attr_names)
        self.assertIn("workflow_settings", attr_names)
        self.assertIn("undo", attr_names)

    def test_registry_defaults_are_non_empty(self) -> None:
        for attr_name, _category, default, _display in SHORTCUT_REGISTRY:
            self.assertTrue(default, f"empty default for {attr_name}")

    def test_load_returns_only_non_default_entries(self) -> None:
        settings = _FakeSettings(
            {
                "shortcuts/quick_rerank_ai_culling": "Ctrl+Shift+Y",  # matches default
                "shortcuts/workflow_settings": "Ctrl+P",  # custom
            }
        )

        result = load_shortcut_overrides(settings=settings)

        self.assertNotIn("quick_rerank_ai_culling", result)
        self.assertEqual(result.get("workflow_settings"), "Ctrl+P")

    def test_save_writes_overrides_and_wipes_un_overridden_attrs(self) -> None:
        settings = _FakeSettings(
            {
                "shortcuts/workflow_settings": "Ctrl+P",
                "shortcuts/grid_view": "Ctrl+1",
            }
        )

        save_shortcut_overrides(
            {"quick_rerank_ai_culling": "Ctrl+Alt+S", "workflow_settings": ""},
            settings=settings,
        )

        self.assertEqual(
            settings._store.get("shortcuts/quick_rerank_ai_culling"),
            "Ctrl+Alt+S",
        )
        # Empty value clears the override.
        self.assertNotIn("shortcuts/workflow_settings", settings._store)
        # Keys not in the override dict are also wiped — registry owns the universe.
        self.assertNotIn("shortcuts/grid_view", settings._store)

    def test_apply_sets_default_when_no_override(self) -> None:
        actions = SimpleNamespace(
            quick_rerank_ai_culling=_FakeAction(),
            workflow_settings=_FakeAction(),
        )

        apply_shortcut_overrides(actions, overrides={})

        self.assertEqual(actions.quick_rerank_ai_culling.shortcut_sequences, ["Ctrl+Shift+Y"])
        self.assertEqual(actions.workflow_settings.shortcut_sequences, ["Ctrl+,"])

    def test_apply_uses_override_when_provided(self) -> None:
        actions = SimpleNamespace(
            quick_rerank_ai_culling=_FakeAction(),
            workflow_settings=_FakeAction(),
        )

        apply_shortcut_overrides(
            actions,
            overrides={"quick_rerank_ai_culling": "Ctrl+Alt+S"},
        )

        self.assertEqual(actions.quick_rerank_ai_culling.shortcut_sequences, ["Ctrl+Alt+S"])
        # workflow_settings still gets its registered default.
        self.assertEqual(actions.workflow_settings.shortcut_sequences, ["Ctrl+,"])

    def test_apply_skips_missing_actions(self) -> None:
        # Only quick_rerank_ai_culling is present; every other registry entry
        # is missing on this fake namespace. Must not raise.
        actions = SimpleNamespace(quick_rerank_ai_culling=_FakeAction())

        apply_shortcut_overrides(actions, overrides={})

        self.assertEqual(actions.quick_rerank_ai_culling.shortcut_sequences, ["Ctrl+Shift+Y"])

    def test_load_then_save_then_load_round_trips_correctly(self) -> None:
        settings = _FakeSettings()

        save_shortcut_overrides(
            {"quick_rerank_ai_culling": "Ctrl+Alt+L"},
            settings=settings,
        )
        loaded = load_shortcut_overrides(settings=settings)

        self.assertEqual(loaded, {"quick_rerank_ai_culling": "Ctrl+Alt+L"})


if __name__ == "__main__":
    unittest.main()
