from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout

import image_triage.mask_engine_worker as meng


class _FakeEngine:
    def __init__(self, device: str = "cuda") -> None:
        self.device = device
        self.warm_calls = 0
        self.loaded: object = None

    def warm_imports(self) -> str:
        self.warm_calls += 1
        return self.device

    def load_model(self, model_dir) -> str:
        self.loaded = model_dir
        return self.device


def _host() -> tuple[meng.MaskEngineHost, _FakeEngine, _FakeEngine]:
    subject, semantic = _FakeEngine(), _FakeEngine()
    host = meng.MaskEngineHost(
        "cuda",
        subject_engine=subject,
        semantic_engine=semantic,
        prompt_engine=_FakeEngine(),
        depth_engine=_FakeEngine(),
    )
    return host, subject, semantic


class MaskEngineRoutingTests(unittest.TestCase):
    def test_warm_imports_without_engine_warms_the_whole_host_once(self) -> None:
        host, subject, semantic = _host()
        device = host.warm_imports()
        self.assertEqual("cuda", device)
        self.assertEqual(1, subject.warm_calls)
        self.assertEqual(1, semantic.warm_calls)

    def test_warm_imports_routes_to_a_single_engine(self) -> None:
        host, subject, semantic = _host()
        host.warm_imports("semantic")
        self.assertEqual(0, subject.warm_calls)
        self.assertEqual(1, semantic.warm_calls)

    def test_load_model_routes_by_engine(self) -> None:
        host, subject, semantic = _host()
        host.load_model("subject", "/models/birefnet")
        self.assertEqual("/models/birefnet", subject.loaded)
        self.assertIsNone(semantic.loaded)

    def test_unknown_engine_is_rejected(self) -> None:
        host, _subject, _semantic = _host()
        with self.assertRaises(ValueError):
            host.warm_imports("bogus")
        with self.assertRaises(ValueError):
            host.load_model("bogus", "/x")

    def test_infer_dispatches_to_the_matching_worker_function(self) -> None:
        host, subject, semantic = _host()
        calls: list[tuple[str, dict]] = []

        original_subject = meng.birefnet_worker.generate_subject_mask
        original_semantic = meng.oneformer_worker.generate_semantic_masks

        def fake_subject(**kwargs):
            calls.append(("subject", kwargs))
            self.assertIs(kwargs["engine"], subject)
            return {"device": "cuda", "components": []}

        def fake_semantic(**kwargs):
            calls.append(("semantic", kwargs))
            self.assertIs(kwargs["engine"], semantic)
            return {"device": "cuda", "sourceSize": [4, 4]}

        meng.birefnet_worker.generate_subject_mask = fake_subject
        meng.oneformer_worker.generate_semantic_masks = fake_semantic
        try:
            sub = host.infer("subject", {
                "modelDir": "/m/bi", "input": "in.png", "output": "out.png",
                "componentsDir": "comps",
            })
            sem = host.infer("semantic", {
                "modelDir": "/m/of", "inputPath": "in.png", "outputDir": "masks",
                "categories": ["sky", "water"], "minimumCoverage": 0.0005,
            })
        finally:
            meng.birefnet_worker.generate_subject_mask = original_subject
            meng.oneformer_worker.generate_semantic_masks = original_semantic

        self.assertEqual(["subject", "semantic"], [c[0] for c in calls])
        self.assertEqual([4, 4], sem["sourceSize"])
        self.assertEqual([], sub["components"])


class _FakeSam:
    def __init__(self, device: str = "cuda") -> None:
        self.device = device
        self.warm_calls = 0
        self.embedded: object = None
        self.segments: list[tuple] = []

    def warm_imports(self) -> str:
        self.warm_calls += 1
        return self.device

    def load_model(self, _model_dir) -> str:
        return self.device

    def embed(self, input_path, image_key=None):
        self.embedded = (str(input_path), image_key)
        return 8, 6

    def segment(self, *, points, labels, output_path, minimum_area, image_key=None):
        self.segments.append((points, labels, image_key))
        return {"device": self.device, "maskPath": str(output_path), "sourceSize": [8, 6],
                "bounds": [0, 0, 4, 4], "coverage": 0.1, "iou": 0.9, "chosenMask": 0, "suppressed": False}

    def segment_many(self, *, point_groups, label_groups, output_paths, minimum_area, image_key=None):
        self.segments.append((point_groups, label_groups, image_key))
        return [
            {"device": self.device, "maskPath": str(path), "sourceSize": [8, 6],
             "bounds": [0, 0, 4, 4], "coverage": 0.1, "iou": 0.9,
             "chosenMask": 0, "suppressed": False}
            for path in output_paths
        ]


class MaskEnginePromptEngineTests(unittest.TestCase):
    def _host(self):
        sam = _FakeSam()
        host = meng.MaskEngineHost(
            "cuda",
            subject_engine=_FakeEngine(),
            semantic_engine=_FakeEngine(),
            prompt_engine=sam,
            depth_engine=_FakeEngine(),
        )
        return host, sam

    def test_embed_and_segment_route_to_the_prompt_engine(self) -> None:
        host, sam = self._host()
        embed = host.embed({"inputPath": "img.png", "imageKey": "k1"})
        self.assertEqual([8, 6], embed["sourceSize"])
        self.assertIsNotNone(sam.embedded)
        self.assertEqual("k1", sam.embedded[1])

        seg = host.segment({"points": [[5, 5]], "labels": [1], "outputPath": "m.png", "imageKey": "k1"})
        self.assertEqual("cuda", seg["device"])
        self.assertEqual([(5.0, 5.0)], sam.segments[0][0])
        self.assertEqual([1], sam.segments[0][1])

    def test_missing_labels_default_to_all_positive(self) -> None:
        host, sam = self._host()
        host.segment({"points": [[1, 2], [3, 4]], "outputPath": "m.png"})
        self.assertEqual([1, 1], sam.segments[0][1])

    def test_segment_many_routes_independent_prompt_groups(self) -> None:
        host, sam = self._host()
        result = host.segment_many({
            "pointGroups": [[[1, 2]], [[3, 4]]],
            "labelGroups": [[1], [1]],
            "outputPaths": ["one.png", "two.png"],
            "imageKey": "k1",
        })
        self.assertEqual(2, len(result["results"]))
        self.assertEqual([[(1.0, 2.0)], [(3.0, 4.0)]], sam.segments[0][0])

    def test_warm_imports_without_engine_warms_prompt_too(self) -> None:
        host, sam = self._host()
        host.warm_imports()
        self.assertEqual(1, sam.warm_calls)


class MaskEngineServerProtocolTests(unittest.TestCase):
    def _run(self, host: meng.MaskEngineHost, lines: list[str]) -> list[dict]:
        stdin = io.StringIO("".join(line + "\n" for line in lines))
        out = io.StringIO()
        original_stdin = meng.sys.stdin
        meng.sys.stdin = stdin
        try:
            with redirect_stdout(out):
                meng.run_server("cuda", host=host)
        finally:
            meng.sys.stdin = original_stdin
        return [
            json.loads(l.removeprefix("RESPONSE "))
            for l in out.getvalue().splitlines()
            if l.startswith("RESPONSE ")
        ]

    def test_full_sequence_routes_and_shuts_down(self) -> None:
        host, subject, semantic = _host()
        meng.oneformer_worker.generate_semantic_masks  # touch for clarity
        original = meng.oneformer_worker.generate_semantic_masks
        meng.oneformer_worker.generate_semantic_masks = lambda **k: {"device": "cuda", "sourceSize": [8, 6]}
        try:
            responses = self._run(host, [
                json.dumps({"id": 1, "command": "warm-imports"}),
                json.dumps({"id": 2, "command": "load-model", "engine": "semantic", "modelDir": "/m/of"}),
                json.dumps({"id": 3, "command": "infer", "engine": "semantic",
                            "inputPath": "in.png", "outputDir": "masks", "modelDir": "/m/of"}),
                json.dumps({"id": 4, "command": "shutdown"}),
            ])
        finally:
            meng.oneformer_worker.generate_semantic_masks = original

        self.assertEqual([1, 2, 3, 4], [r["id"] for r in responses])
        self.assertTrue(all(r["ok"] for r in responses))
        self.assertEqual("all", responses[0]["result"]["engine"])
        self.assertEqual("semantic", responses[1]["result"]["engine"])
        self.assertEqual([8, 6], responses[2]["result"]["sourceSize"])
        self.assertEqual("shutdown", responses[3]["result"]["stage"])
        # load_model resolves the path (absolute per-OS); just confirm it routed.
        self.assertTrue(str(semantic.loaded).replace("\\", "/").endswith("m/of"))

    def test_missing_engine_and_unknown_command_report_errors(self) -> None:
        host, _s, _sem = _host()
        responses = self._run(host, [
            json.dumps({"id": 1, "command": "load-model"}),          # no engine
            json.dumps({"id": 2, "command": "frobnicate"}),          # unknown
            json.dumps({"id": 3, "command": "infer", "engine": "x"}),  # bad engine
        ])
        self.assertFalse(responses[0]["ok"])
        self.assertIn("engine", responses[0]["error"])
        self.assertFalse(responses[1]["ok"])
        self.assertFalse(responses[2]["ok"])


if __name__ == "__main__":
    unittest.main()
