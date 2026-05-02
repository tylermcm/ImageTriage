from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication

from image_triage.models import DeleteMode, WinnerMode
from image_triage.settings_dialog import WorkflowSettingsDialog


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


if __name__ == "__main__":
    unittest.main()
