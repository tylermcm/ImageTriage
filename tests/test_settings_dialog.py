from __future__ import annotations

import unittest

from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication, QFrame, QLabel

from image_triage.models import DeleteMode, WinnerMode
from image_triage.phash_prefilter import PHashPrefilterSettings
from image_triage.settings_dialog import WorkflowSettingsDialog, _settings_tooltip
from image_triage.ui.display_metrics import COMPACT_DISPLAY


def _ensure_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class WorkflowSettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _ensure_app()

    def test_result_settings_preserves_auto_ai_embedding_batch_size(self) -> None:
        dialog = WorkflowSettingsDialog(
            sessions=["Default"],
            current_session="Default",
            winner_mode=WinnerMode.COPY,
            delete_mode=DeleteMode.SAFE_TRASH,
            ai_embed_batch_size=0,
        )

        result = dialog.result_settings()

        self.assertEqual(0, result.ai_embed_batch_size)
        dialog.deleteLater()

    def test_result_settings_defaults_startup_update_checks_on(self) -> None:
        dialog = WorkflowSettingsDialog(
            sessions=["Default"],
            current_session="Default",
            winner_mode=WinnerMode.COPY,
            delete_mode=DeleteMode.SAFE_TRASH,
        )

        result = dialog.result_settings()

        self.assertTrue(result.check_updates_on_startup)
        dialog.deleteLater()

    def test_result_settings_returns_startup_update_check_choice(self) -> None:
        dialog = WorkflowSettingsDialog(
            sessions=["Default"],
            current_session="Default",
            winner_mode=WinnerMode.COPY,
            delete_mode=DeleteMode.SAFE_TRASH,
            check_updates_on_startup=False,
        )
        dialog.check_updates_on_startup_checkbox.setChecked(True)

        result = dialog.result_settings()

        self.assertTrue(result.check_updates_on_startup)
        dialog.deleteLater()

    def test_single_drive_expansion_defaults_on_and_returns_the_choice(self) -> None:
        dialog = WorkflowSettingsDialog(
            sessions=["Default"],
            current_session="Default",
            winner_mode=WinnerMode.COPY,
            delete_mode=DeleteMode.SAFE_TRASH,
        )

        self.assertEqual(
            dialog.single_drive_expansion_checkbox.text(),
            "Keep only one branch expanded per level",
        )
        self.assertTrue(dialog.result_settings().single_drive_expansion_enabled)
        dialog.single_drive_expansion_checkbox.setChecked(False)
        self.assertFalse(dialog.result_settings().single_drive_expansion_enabled)
        dialog.deleteLater()

    def test_toolbar_presentation_is_not_exposed_in_settings(self) -> None:
        dialog = WorkflowSettingsDialog(
            sessions=["Default"],
            current_session="Default",
            winner_mode=WinnerMode.COPY,
            delete_mode=DeleteMode.SAFE_TRASH,
        )

        labels = {label.text() for label in dialog.findChildren(QLabel)}

        self.assertFalse(hasattr(dialog, "toolbar_style_combo"))
        self.assertNotIn("Toolbar", labels)
        self.assertFalse(hasattr(dialog.result_settings(), "toolbar_style"))
        dialog.deleteLater()

    def test_settings_tooltip_wraps_long_lines(self) -> None:
        tooltip = _settings_tooltip(
            "Weight of the tag-penalty-aware base score vs. the trained adapter when blending the final ranking.",
            width=38,
        )

        self.assertIn("\n", tooltip)
        self.assertLessEqual(max(len(line) for line in tooltip.splitlines()), 38)

    def test_result_settings_returns_custom_ai_embedding_batch_size(self) -> None:
        dialog = WorkflowSettingsDialog(
            sessions=["Default"],
            current_session="Default",
            winner_mode=WinnerMode.COPY,
            delete_mode=DeleteMode.SAFE_TRASH,
            ai_embed_batch_size=32,
        )
        dialog.ai_embed_batch_size_spin.setValue(64)

        result = dialog.result_settings()

        self.assertEqual(64, result.ai_embed_batch_size)
        dialog.deleteLater()

    def test_dispute_and_base_score_weight_are_visible_and_round_trip(self) -> None:
        dialog = WorkflowSettingsDialog(
            sessions=["Default"],
            current_session="Default",
            winner_mode=WinnerMode.COPY,
            delete_mode=DeleteMode.SAFE_TRASH,
            ai_dispute_weight=3,
            ai_base_score_weight_percent=65,
        )

        labels = {label.text() for label in dialog.findChildren(QLabel)}
        self.assertIn("Dispute weight", labels)
        self.assertIn("Base score weight", labels)

        dialog.ai_dispute_weight_spin.setValue(5)
        dialog.ai_base_score_weight_spin.setValue(20)
        result = dialog.result_settings()

        self.assertEqual(5, result.ai_dispute_weight)
        self.assertEqual(20, result.ai_base_score_weight_percent)
        dialog.deleteLater()

    def test_shortcuts_page_flags_conflicts_across_every_registry_row(self) -> None:
        """WI-3.2's uniqueness test: the Shortcuts page's own conflict
        checker (`_collect_shortcut_state`) now covers every unified
        binding, not just a hand-picked subset."""
        dialog = WorkflowSettingsDialog(
            sessions=["Default"],
            current_session="Default",
            winner_mode=WinnerMode.COPY,
            delete_mode=DeleteMode.SAFE_TRASH,
        )

        # No two defaults collide out of the box.
        _effective, conflicts = dialog._collect_shortcut_state()
        self.assertEqual({}, conflicts)

        # Rebinding one action onto another's shortcut is caught.
        editors = dialog._shortcut_editors
        target_attr, other_attr = list(editors)[0], list(editors)[1]
        shared = QKeySequence("Ctrl+Alt+F9")
        editors[target_attr].setKeySequence(shared)
        editors[other_attr].setKeySequence(shared)

        _effective, conflicts = dialog._collect_shortcut_state()

        self.assertIn("Ctrl+Alt+F9", conflicts)
        self.assertEqual({target_attr, other_attr}, set(conflicts["Ctrl+Alt+F9"]))
        dialog.deleteLater()

    def test_interface_size_choice_round_trips_and_sizes_dialog_chrome(self) -> None:
        dialog = WorkflowSettingsDialog(
            sessions=["Default"],
            current_session="Default",
            winner_mode=WinnerMode.COPY,
            delete_mode=DeleteMode.SAFE_TRASH,
            interface_size="large",
            display_profile=COMPACT_DISPLAY,
        )

        self.assertEqual("spacious", dialog.result_settings().interface_size)
        self.assertEqual(COMPACT_DISPLAY.settings_nav_width, dialog.findChild(QFrame, "settingsSidebar").width())
        screen_cap = int(dialog.screen().availableGeometry().width() * 0.94)
        self.assertEqual(min(COMPACT_DISPLAY.settings_min_width, screen_cap), dialog.minimumWidth())
        dialog.deleteLater()

    def test_settings_refresh_uses_descriptions_and_labeled_help(self) -> None:
        dialog = WorkflowSettingsDialog(
            sessions=["Default"],
            current_session="Default",
            winner_mode=WinnerMode.COPY,
            delete_mode=DeleteMode.SAFE_TRASH,
        )

        labels = {label.text() for label in dialog.findChildren(QLabel)}

        self.assertGreaterEqual(dialog.minimumWidth(), 700)
        self.assertIn("Review behavior", labels)
        self.assertIn("Navigation and preview", labels)
        self.assertEqual("Open settings guide", dialog.help_button.text())
        dialog.deleteLater()

    def test_phash_prefilter_result_settings_round_trip_controls(self) -> None:
        dialog = WorkflowSettingsDialog(
            sessions=["Default"],
            current_session="Default",
            winner_mode=WinnerMode.COPY,
            delete_mode=DeleteMode.SAFE_TRASH,
            phash_prefilter_settings=PHashPrefilterSettings(
                enabled=True,
                hamming_threshold=4,
                cache_enabled=False,
                diagnostics_enabled=True,
            ),
        )

        result = dialog.result_settings().phash_prefilter_settings

        self.assertTrue(result.enabled)
        self.assertNotIn("Run timing", {label.text() for label in dialog.findChildren(QLabel)})
        self.assertEqual(4, result.hamming_threshold)
        self.assertFalse(result.cache_enabled)
        self.assertTrue(result.diagnostics_enabled)
        dialog.deleteLater()

    def test_every_settings_result_field_has_a_consumer_in_window_py(self) -> None:
        """WI-3.3's validation criterion: every field of `WorkflowSettingsResult`
        is read back somewhere in the settings-accept handler (settings_controller.py). A
        source scan, not an import, so this doesn't need a live MainWindow
        and catches a field that's set but never read (as `ai_clip_model_variant`
        was before WI-3.3 removed it)."""
        import ast
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[1]
        settings_src = (repo_root / "image_triage" / "settings_dialog.py").read_text(encoding="utf-8")
        tree = ast.parse(settings_src)
        fields = None
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "WorkflowSettingsResult":
                fields = [n.target.id for n in node.body if isinstance(n, ast.AnnAssign)]
                break
        self.assertIsNotNone(fields, "WorkflowSettingsResult class not found")
        self.assertGreater(len(fields), 0)

        # The settings-accept handler lives in the settings controller (it moved out of window.py).
        consumer_src = "\n".join(
            (repo_root / "image_triage" / name).read_text(encoding="utf-8") for name in ("window.py", "settings_controller.py")
        )
        missing = [name for name in fields if f"result.{name}" not in consumer_src]

        self.assertEqual([], missing, f"no `result.<field>` consumer found in window.py or settings_controller.py for: {missing}")


if __name__ == "__main__":
    unittest.main()
