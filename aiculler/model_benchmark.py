from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
import statistics
import time
from typing import Iterable

import numpy as np


@dataclass(frozen=True)
class ModelBenchmarkResult:
    model: str
    strategy: str
    setting: int
    sample_count: int
    repetitions: int
    status: str
    error: str
    median_seconds: float | None
    minimum_seconds: float | None
    maximum_seconds: float | None
    images_per_second: float | None
    max_absolute_error: float | None
    minimum_cosine_similarity: float | None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def benchmark_model_session(
    session,
    *,
    model_name: str,
    input_name: str,
    output_names: list[str] | None,
    samples: list[np.ndarray],
    batch_sizes: Iterable[int],
    caller_counts: Iterable[int],
    repetitions: int,
) -> list[ModelBenchmarkResult]:
    if not samples:
        return []
    repeat_count = max(1, int(repetitions))
    session.run(output_names, {input_name: samples[0]})
    reference = _run_batched(
        session,
        input_name=input_name,
        output_names=output_names,
        samples=samples,
        batch_size=1,
    )[1]
    results: list[ModelBenchmarkResult] = []
    for batch_size in _normalized_settings(batch_sizes, len(samples)):
        results.append(
            _measure_strategy(
                model_name=model_name,
                strategy="batch",
                setting=batch_size,
                sample_count=len(samples),
                repetitions=repeat_count,
                reference=reference,
                operation=lambda size=batch_size: _run_batched(
                    session,
                    input_name=input_name,
                    output_names=output_names,
                    samples=samples,
                    batch_size=size,
                ),
            )
        )
    for caller_count in _normalized_settings(caller_counts, len(samples)):
        results.append(
            _measure_strategy(
                model_name=model_name,
                strategy="concurrent_singleton",
                setting=caller_count,
                sample_count=len(samples),
                repetitions=repeat_count,
                reference=reference,
                operation=lambda count=caller_count: _run_concurrent_singletons(
                    session,
                    input_name=input_name,
                    output_names=output_names,
                    samples=samples,
                    caller_count=count,
                ),
            )
        )
    return results


def _measure_strategy(
    *,
    model_name: str,
    strategy: str,
    setting: int,
    sample_count: int,
    repetitions: int,
    reference: list[np.ndarray],
    operation,
) -> ModelBenchmarkResult:
    durations: list[float] = []
    observed: list[np.ndarray] = []
    for repetition in range(repetitions):
        try:
            started_at = time.perf_counter()
            _ignored_duration, outputs = operation()
            durations.append(time.perf_counter() - started_at)
        except Exception as exc:
            return ModelBenchmarkResult(
                model=model_name,
                strategy=strategy,
                setting=setting,
                sample_count=sample_count,
                repetitions=repetitions,
                status="unsupported",
                error=" ".join(str(exc).split()),
                median_seconds=None,
                minimum_seconds=None,
                maximum_seconds=None,
                images_per_second=None,
                max_absolute_error=None,
                minimum_cosine_similarity=None,
            )
        if repetition == 0:
            observed = outputs
    median_seconds = statistics.median(durations)
    max_error, min_cosine = _output_drift(reference, observed)
    return ModelBenchmarkResult(
        model=model_name,
        strategy=strategy,
        setting=setting,
        sample_count=sample_count,
        repetitions=repetitions,
        status="completed",
        error="",
        median_seconds=round(median_seconds, 6),
        minimum_seconds=round(min(durations), 6),
        maximum_seconds=round(max(durations), 6),
        images_per_second=round(sample_count / median_seconds, 6) if median_seconds > 0.0 else 0.0,
        max_absolute_error=max_error,
        minimum_cosine_similarity=min_cosine,
    )


def _run_batched(
    session,
    *,
    input_name: str,
    output_names: list[str] | None,
    samples: list[np.ndarray],
    batch_size: int,
) -> tuple[float, list[np.ndarray]]:
    started_at = time.perf_counter()
    outputs: list[np.ndarray] = []
    for offset in range(0, len(samples), batch_size):
        batch = np.concatenate(samples[offset : offset + batch_size], axis=0)
        batch_outputs = session.run(output_names, {input_name: batch})
        outputs.extend(_split_output_rows(batch_outputs[0], batch.shape[0]))
    return time.perf_counter() - started_at, outputs


def _run_concurrent_singletons(
    session,
    *,
    input_name: str,
    output_names: list[str] | None,
    samples: list[np.ndarray],
    caller_count: int,
) -> tuple[float, list[np.ndarray]]:
    started_at = time.perf_counter()

    def run_one(sample: np.ndarray) -> np.ndarray:
        values = session.run(output_names, {input_name: sample})
        return _split_output_rows(values[0], 1)[0]

    with ThreadPoolExecutor(max_workers=caller_count, thread_name_prefix="model-benchmark") as executor:
        outputs = list(executor.map(run_one, samples))
    return time.perf_counter() - started_at, outputs


def _split_output_rows(output: np.ndarray, expected_rows: int) -> list[np.ndarray]:
    array = np.asarray(output)
    if array.ndim == 0:
        if expected_rows != 1:
            raise ValueError("Scalar model output cannot represent a multi-image batch")
        return [array.reshape(1).astype(np.float64)]
    if array.shape[0] != expected_rows:
        if expected_rows == 1:
            return [array.reshape(-1).astype(np.float64)]
        raise ValueError(
            f"Model output batch dimension {array.shape[0]} does not match input batch {expected_rows}"
        )
    return [np.asarray(array[index]).reshape(-1).astype(np.float64) for index in range(expected_rows)]


def _output_drift(
    reference: list[np.ndarray],
    observed: list[np.ndarray],
) -> tuple[float, float | None]:
    if len(reference) != len(observed):
        raise ValueError("Benchmark output count changed between strategies")
    max_error = 0.0
    cosine_values: list[float] = []
    for expected, actual in zip(reference, observed):
        if expected.shape != actual.shape:
            raise ValueError("Benchmark output shape changed between strategies")
        if expected.size:
            max_error = max(max_error, float(np.max(np.abs(expected - actual))))
        if expected.size > 1:
            denominator = float(np.linalg.norm(expected) * np.linalg.norm(actual))
            if denominator > 0.0:
                cosine_values.append(float(np.dot(expected, actual) / denominator))
    min_cosine = min(cosine_values) if cosine_values else None
    return round(max_error, 12), None if min_cosine is None else round(min_cosine, 12)


def _normalized_settings(values: Iterable[int], sample_count: int) -> list[int]:
    return sorted({max(1, min(int(value), sample_count)) for value in values})
