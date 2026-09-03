from __future__ import annotations

import unittest

import numpy as np

from aiculler.features import HeadlessFeatureExtractor
from aiculler.text_scoring import CLIPTextEncoder


class _Meta:
    def __init__(self, name: str, shape: list[object], type_name: str) -> None:
        self.name = name
        self.shape = shape
        self.type = type_name


class _CombinedSession:
    def __init__(self) -> None:
        self._inputs = [
            _Meta("input_ids", ["text_batch_size", "sequence_length"], "tensor(int64)"),
            _Meta("pixel_values", ["image_batch_size", 3, 224, 224], "tensor(float)"),
            _Meta("attention_mask", ["text_batch_size", "sequence_length"], "tensor(int64)"),
        ]
        self._outputs = [
            _Meta("logits_per_image", ["image_batch_size", "text_batch_size"], "tensor(float)"),
            _Meta("text_embeds", ["text_batch_size", 512], "tensor(float)"),
            _Meta("image_embeds", ["image_batch_size", 512], "tensor(float)"),
        ]

    def get_inputs(self):
        return self._inputs

    def get_outputs(self):
        return self._outputs


class TinyCLIPRuntimeTests(unittest.TestCase):
    def test_image_encoder_selects_image_output_and_supplies_text_inputs(self) -> None:
        session = _CombinedSession()
        pixels = np.zeros((1, 3, 224, 224), dtype=np.float32)

        feeds = HeadlessFeatureExtractor._build_clip_image_feeds(session, "pixel_values", pixels)

        self.assertEqual("image_embeds", HeadlessFeatureExtractor._select_embedding_output(session))
        self.assertEqual({"input_ids", "pixel_values", "attention_mask"}, set(feeds))
        self.assertEqual((1, 77), feeds["input_ids"].shape)
        self.assertEqual((1, 77), feeds["attention_mask"].shape)

    def test_text_encoder_selects_text_output_and_supplies_image_input(self) -> None:
        session = _CombinedSession()
        ids = np.zeros((1, 77), dtype=np.int64)
        mask = np.zeros((1, 77), dtype=np.int64)
        mask[:, :4] = 1

        feeds = CLIPTextEncoder._build_clip_text_feeds(
            session,
            "input_ids",
            ids,
            attention_mask=mask,
        )

        self.assertEqual("text_embeds", CLIPTextEncoder._select_output_name(session))
        self.assertEqual({"input_ids", "pixel_values", "attention_mask"}, set(feeds))
        np.testing.assert_array_equal(mask, feeds["attention_mask"])
        self.assertEqual((1, 3, 224, 224), feeds["pixel_values"].shape)


if __name__ == "__main__":
    unittest.main()
