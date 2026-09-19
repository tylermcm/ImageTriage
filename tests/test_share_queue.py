from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from image_triage.share_queue import ShareQueueStore


class ShareQueueStoreTests(unittest.TestCase):
    def test_ready_entry_tracks_transfer_and_manual_post(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = ShareQueueStore(Path(temp_dir) / "queue.sqlite3")
            entry = store.create_ready(
                name="  Launch   Picks ",
                target="Instagram",
                account="Studio",
                caption="A caption",
                preset_key="social_large",
                source_paths=("C:/photos/a.raw",),
                output_paths=("C:/cache/01-a.jpg",),
                alt_text=("Portrait at sunset",),
                package_dir=str(Path(temp_dir) / "package"),
            )

            self.assertEqual(entry.name, "Launch Picks")
            self.assertEqual(entry.status, "ready")
            transferred = store.mark_transferred(entry.id)
            self.assertIsNotNone(transferred)
            self.assertEqual(transferred.status, "transferred")
            self.assertTrue(transferred.transferred_at)

            posted = store.set_status(entry.id, "posted")
            self.assertIsNotNone(posted)
            self.assertEqual(posted.status, "posted")
            self.assertTrue(posted.posted_at)

            # A later download cannot incorrectly move a manually-posted item backward.
            self.assertEqual(store.mark_transferred(entry.id).status, "posted")

    def test_delete_removes_only_package_below_share_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            store = ShareQueueStore(temp_root / "queue.sqlite3")
            outside = temp_root / "outside"
            outside.mkdir()
            (outside / "keep.txt").write_text("keep", encoding="utf-8")
            entry = store.create_ready(
                name="Outside",
                target="General",
                account="",
                caption="",
                preset_key="social_large",
                source_paths=(),
                output_paths=(),
                alt_text=(),
                package_dir=str(outside),
            )

            self.assertTrue(store.delete(entry.id, remove_files=True))
            self.assertTrue((outside / "keep.txt").exists())
            self.assertIsNone(store.get(entry.id))


if __name__ == "__main__":
    unittest.main()
