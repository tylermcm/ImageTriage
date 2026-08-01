from __future__ import annotations

import unittest

import numpy as np

from aiculler.model_benchmark import benchmark_model_session


class _FakeSession:
    def run(self, _output_names, inputs):
        batch = next(iter(inputs.values()))
        flattened = batch.reshape(batch.shape[0], -1).astype(np.float32)
        return [np.stack((flattened.mean(axis=1), flattened.max(axis=1)), axis=1)]


class _SingletonOnlySession(_FakeSession):
    def run(self, output_names, inputs):
        batch = next(iter(inputs.values()))
        if batch.shape[0] > 1:
            raise RuntimeError("batch size is fixed at one")
        return super().run(output_names, inputs)


class ModelBenchmarkTests(unittest.TestCase):
    def test_batch_and_concurrent_strategies_preserve_outputs(self) -> None:
        samples = [np.full((1, 3, 2, 2), index, dtype=np.float32) for index in range(1, 5)]

        results = benchmark_model_session(
            _FakeSession(),
            model_name="fake",
            input_name="pixels",
            output_names=["embedding"],
            samples=samples,
            batch_sizes=[1, 2, 4],
            caller_counts=[1, 2, 4],
            repetitions=2,
        )

        self.assertEqual(6, len(results))
        self.assertEqual({"batch", "concurrent_singleton"}, {result.strategy for result in results})
        self.assertTrue(all(result.status == "completed" for result in results))
        self.assertTrue(all(result.max_absolute_error == 0.0 for result in results))
        self.assertTrue(all(result.minimum_cosine_similarity == 1.0 for result in results))
        self.assertTrue(all(result.images_per_second > 0.0 for result in results))

    def test_unsupported_batch_is_recorded_without_aborting_other_results(self) -> None:
        samples = [np.ones((1, 3, 2, 2), dtype=np.float32) for _ in range(2)]

        results = benchmark_model_session(
            _SingletonOnlySession(),
            model_name="singleton",
            input_name="pixels",
            output_names=None,
            samples=samples,
            batch_sizes=[1, 2],
            caller_counts=[1, 2],
            repetitions=1,
        )

        batch_two = next(result for result in results if result.strategy == "batch" and result.setting == 2)
        self.assertEqual("unsupported", batch_two.status)
        self.assertIn("fixed at one", batch_two.error)
        self.assertEqual(3, sum(result.status == "completed" for result in results))


if __name__ == "__main__":
    unittest.main()
