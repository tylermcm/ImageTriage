from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication, QLabel

from image_triage.image_resize import ResizeSourceItem
from image_triage.share_phone import PhoneShareNetworkDiagnostic
from image_triage.share_queue import ShareQueueStore
import image_triage.ui.share_to_phone_dialog as share_dialog_module
from image_triage.ui.share_to_phone_dialog import ShareToPhoneDialog


class ShareToPhoneDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_prepare_surface_is_compact_and_uses_folder_date_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "photo.jpg"
            Image.new("RGB", (80, 60), "navy").save(source)
            store = ShareQueueStore(Path(temp_dir) / "queue.sqlite3")
            dialog = ShareToPhoneDialog((ResizeSourceItem(str(source), source.name),), store=store)

            self.assertEqual(dialog.selection_label.text(), "1 image")
            self.assertEqual(dialog.tabs.tabText(0), "Prepare")
            self.assertEqual(dialog.tabs.tabText(1), "Posting Queue")
            expected_name = f"{Path(temp_dir).name} - {datetime.now():%Y-%m-%d}"
            self.assertEqual(dialog.name_field.text(), expected_name)
            self.assertEqual(dialog.target_field.text(), "")
            self.assertEqual(dialog.target_field.placeholderText(), "Optional — social, client, portfolio")
            self.assertFalse(hasattr(dialog, "account_field"))
            self.assertFalse(hasattr(dialog, "privacy_label"))
            self.assertFalse(hasattr(dialog, "alt_table"))
            self.assertLessEqual(dialog.caption_group.height(), 112)
            self.assertEqual(dialog.prepare_button.text(), "Share")
            self.assertEqual(dialog.prepare_button.objectName(), "sharePhonePrimaryButton")
            self.assertTrue(dialog.prepare_button.isEnabled())
            self.assertEqual(dialog.close_button.text(), "Close")
            self.assertEqual(dialog.source_label.text(), f"From {Path(temp_dir).name}")
            dialog.show()
            self.app.processEvents()
            self.assertEqual((dialog.width(), dialog.height()), ShareToPhoneDialog.PREPARE_SIZE)
            self.assertGreaterEqual(dialog.height(), dialog.minimumSizeHint().height())
            dialog.close()

    def test_spec_preserves_source_order_without_exposing_alt_text_editor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = [Path(temp_dir) / name for name in ("first.jpg", "second.jpg")]
            for path in paths:
                Image.new("RGB", (80, 60), "navy").save(path)
            dialog = ShareToPhoneDialog(
                tuple(ResizeSourceItem(str(path), path.name) for path in paths),
                store=ShareQueueStore(Path(temp_dir) / "queue.sqlite3"),
            )
            spec = dialog._spec()

            self.assertEqual([source.source_name for source in spec.sources], ["first.jpg", "second.jpg"])
            self.assertEqual(spec.alt_text, ("", ""))
            self.assertEqual(spec.account, "")
            dialog.close()

    def test_result_surface_uses_connection_card_structure(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "photo.jpg"
            Image.new("RGB", (80, 60), "navy").save(source)
            dialog = ShareToPhoneDialog(
                (ResizeSourceItem(str(source), source.name),),
                store=ShareQueueStore(Path(temp_dir) / "queue.sqlite3"),
            )

            self.assertEqual(dialog.result_group.objectName(), "phoneShareConnectionCard")
            self.assertEqual(dialog.status_label.text(), "Waiting for your phone")
            self.assertEqual(dialog.status_pill.property("state"), "waiting")
            self.assertEqual(dialog.qr_label.width(), dialog.qr_label.height())
            self.assertEqual(dialog.qr_label.width(), ShareToPhoneDialog.QR_SIZE)
            dialog.show()
            self.app.processEvents()
            dialog._show_result_mode()
            self.app.processEvents()
            self.assertLessEqual(dialog.width(), 500)
            self.assertGreaterEqual(dialog.height(), dialog.minimumSizeHint().height())
            self.assertEqual(dialog.prepare_button.text(), "Share Another")
            self.assertEqual(dialog.close_button.text(), "Done")
            self.assertTrue(dialog.close_button.isDefault())
            self.assertTrue(dialog.tabs.tabBar().isHidden())
            dialog._reset_prepare_view()
            self.app.processEvents()
            self.assertEqual(dialog.close_button.text(), "Close")
            self.assertTrue(dialog.prepare_button.isDefault())
            dialog.close()

    def test_private_network_status_does_not_repeat_as_a_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dialog = ShareToPhoneDialog((), store=ShareQueueStore(Path(temp_dir) / "queue.sqlite3"))

            diagnostic = PhoneShareNetworkDiagnostic(profile="private", private_rule_present=True)
            original_diagnostic = share_dialog_module.phone_share_network_diagnostic
            share_dialog_module.phone_share_network_diagnostic = lambda: diagnostic
            try:
                dialog._refresh_network_diagnostic()
            finally:
                share_dialog_module.phone_share_network_diagnostic = original_diagnostic

            self.assertEqual(dialog.network_status.text(), "Private network")
            self.assertTrue(dialog.network_warning.isHidden())
            self.assertTrue(dialog.network_buttons.isHidden())
            self.assertEqual(dialog.network_panel.property("state"), "ok")
            dialog.close()

    def test_public_network_marks_panel_as_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dialog = ShareToPhoneDialog((), store=ShareQueueStore(Path(temp_dir) / "queue.sqlite3"))

            diagnostic = PhoneShareNetworkDiagnostic(profile="public", private_rule_present=False)
            original_diagnostic = share_dialog_module.phone_share_network_diagnostic
            share_dialog_module.phone_share_network_diagnostic = lambda: diagnostic
            try:
                dialog._refresh_network_diagnostic()
            finally:
                share_dialog_module.phone_share_network_diagnostic = original_diagnostic

            self.assertEqual(dialog.network_status.text(), "Public network")
            self.assertFalse(dialog.network_warning.isHidden())
            self.assertFalse(dialog.network_buttons.isHidden())
            self.assertEqual(dialog.network_panel.property("state"), "warning")
            dialog.close()

    def test_queue_can_open_without_a_current_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            dialog = ShareToPhoneDialog((), store=ShareQueueStore(Path(temp_dir) / "queue.sqlite3"), show_queue=True)

            self.assertIs(dialog.tabs.currentWidget(), dialog.queue_tab)
            self.assertFalse(dialog.prepare_button.isEnabled())
            self.assertTrue(dialog.prepare_button.isHidden())
            self.assertIs(dialog.queue_stack.currentWidget(), dialog.queue_empty)
            dialog.close()

    def test_queue_rows_show_status_chips(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ShareQueueStore(Path(temp_dir) / "queue.sqlite3")
            entry = store.create_ready(
                name="Portfolio refresh",
                target="Portfolio",
                account="",
                caption="",
                preset_key="social_large",
                source_paths=(str(Path(temp_dir) / "a.jpg"),),
                output_paths=(),
                alt_text=("",),
                package_dir=str(Path(temp_dir) / "package"),
            )
            store.set_status(entry.id, "posted")
            dialog = ShareToPhoneDialog((), store=store, show_queue=True)

            self.assertIs(dialog.queue_stack.currentWidget(), dialog.queue_table)
            chip = dialog.queue_table.cellWidget(0, 2).findChild(QLabel, "sharePhoneStatusChip")
            self.assertEqual(chip.property("shareStatus"), "posted")
            self.assertEqual(chip.text(), "✓ Posted")
            dialog.close()


if __name__ == "__main__":
    unittest.main()
