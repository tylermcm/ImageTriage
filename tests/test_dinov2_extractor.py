from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import torch


AICULLING_ROOT = Path(__file__).resolve().parents[1] / "AICullingPipeline"
if str(AICULLING_ROOT) not in sys.path:
    sys.path.insert(0, str(AICULLING_ROOT))

from app.models.dinov2_extractor import DINOv2EmbeddingExtractor


class _FakeTransformersModel(torch.nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.config = type("Config", (), {"hidden_size": 768, "image_size": 518})()


@unittest.skipUnless(importlib.util.find_spec("transformers") is not None, "transformers is required for this test")
class DINOv2ExtractorTests(unittest.TestCase):
    def test_local_transformers_repository_uses_transformers_backend(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            model_dir = Path(temp_dir)
            (model_dir / "config.json").write_text(
                '{"model_type":"dinov2","architectures":["Dinov2Model"],"image_size":518}',
                encoding="utf-8",
            )
            (model_dir / "model.safetensors").write_bytes(b"weights")

            with patch("transformers.Dinov2Model.from_pretrained", return_value=_FakeTransformersModel()):
                extractor = DINOv2EmbeddingExtractor(str(model_dir), device="cpu")

        self.assertEqual(extractor.model_name, str(model_dir.resolve()))
        self.assertEqual(extractor.feature_dim, 768)
        self.assertEqual(extractor.preprocessing.height, 518)
        self.assertEqual(extractor.preprocessing.width, 518)


if __name__ == "__main__":
    unittest.main()
