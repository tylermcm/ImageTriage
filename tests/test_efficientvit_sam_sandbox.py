from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from sandboxes.efficientvit_sam.core import (
    MaskProposal,
    ModelPaths,
    EfficientViTSam,
    PreprocessInfo,
    deduplicate_proposals,
    postprocess_logits,
    preprocess_rgb,
    providers_for_device,
    scale_prompt_coords,
)
from sandboxes.efficientvit_sam.cli import parse_point_tokens, write_image_report, write_index
from sandboxes.efficientvit_sam.semantic import (
    RegionPrediction,
    masked_region_crop,
    merge_predictions,
    normalize_vector,
)


class _Runtime:
    class GraphOptimizationLevel:
        ORT_ENABLE_ALL = object()

    class SessionOptions:
        def __init__(self) -> None:
            self.intra_op_num_threads = 0
            self.inter_op_num_threads = 0
            self.graph_optimization_level = None

    def __init__(self, providers: list[str]) -> None:
        self._providers = providers

    def get_available_providers(self) -> list[str]:
        return self._providers


class _Input:
    def __init__(self, name: str) -> None:
        self.name = name


class _Session:
    def __init__(self, path: str, providers: list[str]) -> None:
        self._encoder = "encoder" in path
        self._providers = providers

    def get_inputs(self) -> list[_Input]:
        if self._encoder:
            return [_Input("image")]
        return [_Input("image_embeddings"), _Input("point_coords"), _Input("point_labels")]

    def get_providers(self) -> list[str]:
        return self._providers

    def run(self, _outputs: object, feed: dict[str, np.ndarray]) -> list[np.ndarray]:
        if self._encoder:
            self.last_encoder_shape = feed["image"].shape
            return [np.zeros((1, 256, 64, 64), dtype=np.float32)]
        logits = np.full((1, 3, 256, 256), -4.0, dtype=np.float32)
        logits[0, 1, 64:192, 64:192] = 4.0
        return [logits, np.asarray([[0.2, 0.95, 0.5]], dtype=np.float32)]


class _InferenceRuntime(_Runtime):
    def InferenceSession(
        self,
        path: str,
        *,
        sess_options: object,
        providers: list[str],
    ) -> _Session:
        return _Session(path, providers)


class EfficientViTSamSandboxTests(unittest.TestCase):
    def test_point_parser_accepts_powershell_and_quoted_forms(self) -> None:
        self.assertEqual((640.0, 420.0), parse_point_tokens(["640", "420"]))
        self.assertEqual((640.0, 420.0), parse_point_tokens(["640,420"]))

    def test_preprocess_preserves_aspect_ratio_and_pads_to_512(self) -> None:
        rgb = np.zeros((600, 1200, 3), dtype=np.uint8)
        tensor, info = preprocess_rgb(rgb)

        self.assertEqual((1, 3, 512, 512), tensor.shape)
        self.assertEqual((256, 512), (info.resized_height, info.resized_width))
        self.assertTrue(np.all(tensor[:, :, 256:, :] == 0.0))

    def test_prompt_coordinates_use_sam_decoder_coordinate_space(self) -> None:
        info = PreprocessInfo(600, 1200, 256, 512)
        coords = np.asarray([[[600.0, 300.0]]], dtype=np.float32)

        scaled = scale_prompt_coords(coords, info)

        np.testing.assert_allclose([512.0, 256.0], scaled[0, 0], atol=0.01)

    def test_postprocess_restores_original_size(self) -> None:
        info = PreprocessInfo(300, 600, 256, 512)
        logits = np.zeros((1, 2, 256, 256), dtype=np.float32)
        logits[0, 0, :64, :] = 4.0

        restored = postprocess_logits(logits, info)

        self.assertEqual((2, 300, 600), restored.shape)
        self.assertGreater(float(restored[0, 50].mean()), float(restored[0, 250].mean()))

    def test_duplicate_masks_keep_the_higher_scoring_candidate(self) -> None:
        mask = np.zeros((20, 20), dtype=bool)
        mask[2:12, 2:12] = True
        low = MaskProposal(mask, mask.astype(np.float32), (3, 3), 0.7, 0.9, 0.25, (2, 2, 12, 12))
        high = MaskProposal(mask.copy(), mask.astype(np.float32), (5, 5), 0.9, 0.9, 0.25, (2, 2, 12, 12))

        kept = deduplicate_proposals([low, high])

        self.assertEqual(1, len(kept))
        self.assertEqual(0.9, kept[0].predicted_iou)

    def test_auto_provider_prefers_cuda_and_cpu_is_explicit(self) -> None:
        runtime = _Runtime(["CUDAExecutionProvider", "CPUExecutionProvider"])
        self.assertEqual(
            ["CUDAExecutionProvider", "CPUExecutionProvider"],
            providers_for_device(runtime, "auto"),
        )
        self.assertEqual(["CPUExecutionProvider"], providers_for_device(runtime, "cpu"))

    def test_cuda_provider_reports_a_clear_missing_runtime_error(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "CUDAExecutionProvider is unavailable"):
            providers_for_device(_Runtime(["CPUExecutionProvider"]), "cuda")

    def test_fake_onnx_sessions_cover_embedding_and_point_decode(self) -> None:
        runtime = _InferenceRuntime(["CPUExecutionProvider"])
        model = EfficientViTSam(
            ModelPaths(Path("encoder.onnx"), Path("decoder.onnx")),
            runtime=runtime,
        )
        embedding = model.embed(np.zeros((300, 600, 3), dtype=np.uint8))

        proposal, decoder_ms = model.segment_points(embedding, [(300.0, 150.0)], [1.0])

        self.assertEqual((300, 600), proposal.mask.shape)
        self.assertAlmostEqual(0.95, proposal.predicted_iou, places=6)
        self.assertGreater(proposal.area_fraction, 0.0)
        self.assertGreaterEqual(decoder_ms, 0.0)

    def test_visual_report_writes_review_artifacts(self) -> None:
        rgb = np.zeros((40, 60, 3), dtype=np.uint8)
        mask = np.zeros((40, 60), dtype=bool)
        mask[10:30, 20:40] = True
        proposal = MaskProposal(
            mask,
            mask.astype(np.float16),
            (30.0, 20.0),
            0.9,
            0.85,
            float(mask.mean()),
            (20, 10, 40, 30),
        )
        with TemporaryDirectory() as temporary:
            output = Path(temporary)
            report = write_image_report(
                output,
                Path("example.jpg"),
                rgb,
                [proposal],
                {"decode": 1.0, "encoder": 2.0, "decoder": 3.0, "total": 6.0},
                ("CPUExecutionProvider",),
            )
            index = write_index(output, [report])
            report_dir = Path(str(report["output_dir"]))

            self.assertTrue(index.is_file())
            self.assertTrue((report_dir / "contact_sheet.jpg").is_file())
            self.assertTrue((report_dir / "masks" / "region_001.png").is_file())
            self.assertTrue((report_dir / "report.json").is_file())

    def test_masked_region_crop_keeps_requested_region_and_fades_context(self) -> None:
        rgb = np.full((20, 30, 3), 220, dtype=np.uint8)
        mask = np.zeros((20, 30), dtype=bool)
        mask[5:15, 10:20] = True
        proposal = MaskProposal(
            mask,
            mask.astype(np.float16),
            (15.0, 10.0),
            0.9,
            0.9,
            float(mask.mean()),
            (10, 5, 20, 15),
        )

        crop = np.asarray(masked_region_crop(rgb, proposal, padding_ratio=0.5))

        self.assertEqual(220, int(crop[crop.shape[0] // 2, crop.shape[1] // 2, 0]))
        self.assertLess(int(crop[0, 0, 0]), 220)

    def test_semantic_merge_excludes_ambiguous_regions_and_max_merges_labels(self) -> None:
        first = np.zeros((8, 8), dtype=np.float32)
        first[1:5, 1:5] = 0.8
        second = np.zeros((8, 8), dtype=np.float32)
        second[3:7, 3:7] = 0.9
        ambiguous = np.ones((8, 8), dtype=np.float32)

        def prediction(mask: np.ndarray, label: str, margin: float) -> RegionPrediction:
            proposal = MaskProposal(
                mask >= 0.5, mask, (2.0, 2.0), 0.9, 0.9,
                float((mask >= 0.5).mean()), (0, 0, 8, 8),
            )
            return RegionPrediction(proposal, label, 0.8, margin, {label: 0.3})

        merged = merge_predictions(
            [prediction(first, "water", 0.04), prediction(second, "water", 0.03), prediction(ambiguous, "sky", 0.001)],
            minimum_margin=0.01,
        )

        self.assertEqual({"water"}, set(merged))
        self.assertAlmostEqual(0.9, float(merged["water"][4, 4]), places=5)
        np.testing.assert_allclose([0.6, 0.8], normalize_vector(np.asarray([3.0, 4.0])))


if __name__ == "__main__":
    unittest.main()
