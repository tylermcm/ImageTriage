import unittest
from pathlib import Path
import tempfile

from PIL import Image

from aiculler.features import HeuristicTechnicalScorer, IngestionEngine
from aiculler.storage import SQLiteFeatureStore


class HeuristicTechnicalScorerTests(unittest.TestCase):
    def test_score_is_normalized(self):
        scorer = HeuristicTechnicalScorer()
        score = scorer.score(Image.new("RGB", (64, 64), "white"))

        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)


class IngestionEngineTests(unittest.TestCase):
    def test_preview_error_persists_image_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "broken.jpg"
            source.write_bytes(b"not an image")
            store = SQLiteFeatureStore(tmp_path / "test.sqlite")
            try:
                engine = IngestionEngine(store, tmp_path / "cache")
                image_ids = engine.ingest_paths([source])

                self.assertEqual(len(image_ids), 1)
                row = store.get_image(image_ids[0])
                self.assertIsNotNone(row)
                self.assertEqual(row["status"], "error")
                self.assertIn("cannot identify image file", row["error"])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
