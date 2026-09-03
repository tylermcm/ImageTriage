from __future__ import annotations

import argparse
from contextlib import contextmanager
import inspect
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
import types

EXPORT_DEPS = Path(__file__).resolve().parent / "export_deps"
if EXPORT_DEPS.is_dir():
    sys.path.insert(0, str(EXPORT_DEPS))

import numpy as np


MODEL_ID = "wkcn/TinyCLIP-ViT-8M-16-Text-3M-YFCC15M"
MODEL_REVISION = "a2a8c6eaa2549ad66eb7c31b85022bf58273a26c"
ROOT = Path(__file__).resolve().parent
USER_HOME = ROOT.parents[4]
os.environ.setdefault("HOME", str(USER_HOME))
os.environ.setdefault("USERPROFILE", str(USER_HOME))
os.environ.setdefault("USERNAME", USER_HOME.name)
os.environ.setdefault("USER", USER_HOME.name)
os.environ.setdefault("LOGNAME", USER_HOME.name)
os.environ.setdefault("HF_HOME", str(ROOT / "cache" / "huggingface"))
TINY_ROOT = ROOT / "models" / "tinyclip-vit-8m-16-text-3m"
TINY_SOURCE = TINY_ROOT / "source"
RESULTS_DIR = ROOT / "results"
CURRENT_ROOT = Path(
    os.environ.get(
        "IMAGE_TRIAGE_BENCHMARK_CURRENT_CLIP",
        USER_HOME
        / "AppData"
        / "Local"
        / "image_triage_ai_cache"
        / "models"
        / "CLI-Culler"
        / "Clip"
        / "clip-vit-large-patch14",
    )
)


@contextmanager
def _synchronous_windows_imports():
    """Work around this machine's broken _overlapped provider during torch import."""
    if os.name != "nt":
        yield
        return
    fake_asyncio = types.ModuleType("asyncio")
    fake_asyncio.iscoroutinefunction = inspect.iscoroutinefunction
    fake_asyncio.iscoroutine = inspect.isawaitable
    fake_asyncio.coroutines = types.SimpleNamespace(_is_coroutine=object())
    previous = sys.modules.get("asyncio")
    sys.modules["asyncio"] = fake_asyncio
    try:
        import unittest.mock  # noqa: F401
        yield
    finally:
        if previous is None:
            sys.modules.pop("asyncio", None)
        else:
            sys.modules["asyncio"] = previous


def _export_tinyclip() -> dict[str, object]:
    vision_path = TINY_ROOT / "onnx" / "vision_model.onnx"
    text_path = TINY_ROOT / "onnx" / "text_model.onnx"
    tokenizer_path = TINY_ROOT / "tokenizer.json"
    metadata_path = TINY_ROOT / "export.json"
    if all(path.is_file() for path in (vision_path, text_path, tokenizer_path, metadata_path)):
        return json.loads(metadata_path.read_text(encoding="utf-8"))

    TINY_ROOT.mkdir(parents=True, exist_ok=True)
    vision_path.parent.mkdir(parents=True, exist_ok=True)
    required_source = ("config.json", "model.safetensors", "tokenizer.json")
    missing_source = [name for name in required_source if not (TINY_SOURCE / name).is_file()]
    if missing_source:
        raise FileNotFoundError(
            "Download the pinned TinyCLIP snapshot before exporting; missing: "
            + ", ".join(missing_source)
        )
    with _synchronous_windows_imports():
        import torch
        from transformers import CLIPModel, CLIPTokenizerFast

        model = CLIPModel.from_pretrained(
            TINY_SOURCE,
            local_files_only=True,
            attn_implementation="eager",
        ).eval()
        tokenizer = CLIPTokenizerFast.from_pretrained(TINY_SOURCE, local_files_only=True)
    tokenizer.save_pretrained(TINY_ROOT)

    class VisionEncoder(torch.nn.Module):
        def __init__(self, clip_model):
            super().__init__()
            self.vision_model = clip_model.vision_model
            self.visual_projection = clip_model.visual_projection

        def forward(self, pixel_values):
            pooled = self.vision_model(pixel_values=pixel_values).pooler_output
            return self.visual_projection(pooled)

    class TextEncoder(torch.nn.Module):
        def __init__(self, clip_model):
            super().__init__()
            self.text_model = clip_model.text_model
            self.text_projection = clip_model.text_projection
            self.eos_token_id = int(clip_model.config.text_config.eos_token_id)

        def forward(self, input_ids):
            hidden = self.text_model.embeddings(input_ids=input_ids)
            sequence_length = hidden.shape[1]
            causal_mask = torch.full(
                (sequence_length, sequence_length),
                torch.finfo(hidden.dtype).min,
                dtype=hidden.dtype,
                device=hidden.device,
            ).triu(diagonal=1)
            causal_mask = causal_mask[None, None, :, :].expand(hidden.shape[0], 1, -1, -1)
            encoded = self.text_model.encoder(
                inputs_embeds=hidden,
                attention_mask=causal_mask,
                is_causal=True,
            ).last_hidden_state
            encoded = self.text_model.final_layer_norm(encoded)
            if self.eos_token_id == 2:
                eos_positions = input_ids.to(dtype=torch.int).argmax(dim=-1)
            else:
                eos_positions = (input_ids.to(dtype=torch.int) == self.eos_token_id).int().argmax(dim=-1)
            pooled = encoded[torch.arange(encoded.shape[0], device=encoded.device), eos_positions]
            return self.text_projection(pooled)

    image_size = int(model.config.vision_config.image_size)
    sequence_length = int(model.config.text_config.max_position_embeddings)
    image_example = torch.zeros((1, 3, image_size, image_size), dtype=torch.float32)
    text_example = torch.zeros((1, sequence_length), dtype=torch.int64)
    common = {
        "opset_version": 17,
        "do_constant_folding": True,
        "dynamo": False,
    }
    with torch.inference_mode():
        torch.onnx.export(
            VisionEncoder(model),
            (image_example,),
            vision_path,
            input_names=["pixel_values"],
            output_names=["image_embeds"],
            dynamic_axes={"pixel_values": {0: "batch"}, "image_embeds": {0: "batch"}},
            **common,
        )
        torch.onnx.export(
            TextEncoder(model),
            (text_example,),
            text_path,
            input_names=["input_ids"],
            output_names=["text_embeds"],
            dynamic_axes={"input_ids": {0: "batch"}, "text_embeds": {0: "batch"}},
            **common,
        )

    metadata = {
        "model_id": MODEL_ID,
        "revision": MODEL_REVISION,
        "image_size": image_size,
        "sequence_length": sequence_length,
        "projection_dimension": int(model.config.projection_dim),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return metadata


def _session_options(ort, threads: int):
    options = ort.SessionOptions()
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.intra_op_num_threads = threads
    options.add_session_config_entry("session.intra_op.allow_spinning", "0")
    return options


def _tokenizer(tokenizer_path: Path):
    from tokenizers import Tokenizer

    tokenizer = Tokenizer.from_file(str(tokenizer_path))
    pad_id = tokenizer.token_to_id("<|endoftext|>")
    if pad_id is None:
        raise RuntimeError(f"Tokenizer has no <|endoftext|> token: {tokenizer_path}")
    tokenizer.enable_truncation(max_length=77)
    tokenizer.enable_padding(length=77, pad_id=pad_id, pad_token="<|endoftext|>")
    return tokenizer


def _percentile(values: list[float], percentile: float) -> float:
    return float(np.percentile(np.asarray(values, dtype=np.float64), percentile))


def _minimum_row_cosine(reference: np.ndarray, observed: np.ndarray) -> float:
    left = reference / np.linalg.norm(reference, axis=1, keepdims=True)
    right = observed / np.linalg.norm(observed, axis=1, keepdims=True)
    return float(np.min(np.sum(left * right, axis=1)))


def _validate_tinyclip_export(threads: int) -> dict[str, float]:
    import onnxruntime as ort

    with _synchronous_windows_imports():
        import torch
        from transformers import CLIPModel, CLIPTokenizerFast

        model = CLIPModel.from_pretrained(
            TINY_SOURCE,
            local_files_only=True,
            attn_implementation="eager",
        ).eval()
        tokenizer = CLIPTokenizerFast.from_pretrained(TINY_SOURCE, local_files_only=True)
        rng = np.random.default_rng(20260902)
        images = rng.standard_normal((2, 3, 224, 224), dtype=np.float32)
        encoded = tokenizer(
            ["red car", "dog on a beach"],
            padding="max_length",
            truncation=True,
            max_length=77,
            return_tensors="pt",
        )
        with torch.inference_mode():
            image_reference = model.visual_projection(
                model.vision_model(pixel_values=torch.from_numpy(images)).pooler_output
            ).cpu().numpy()
            text_reference = model.text_projection(
                model.text_model(input_ids=encoded["input_ids"]).pooler_output
            ).cpu().numpy()

    options = _session_options(ort, threads)
    vision = ort.InferenceSession(
        str(TINY_ROOT / "onnx" / "vision_model.onnx"),
        sess_options=options,
        providers=["CPUExecutionProvider"],
    )
    text = ort.InferenceSession(
        str(TINY_ROOT / "onnx" / "text_model.onnx"),
        sess_options=options,
        providers=["CPUExecutionProvider"],
    )
    image_observed = vision.run(None, {vision.get_inputs()[0].name: images})[0]
    text_observed = text.run(
        None,
        {text.get_inputs()[0].name: encoded["input_ids"].cpu().numpy().astype(np.int64)},
    )[0]
    return {
        "image_max_absolute_error": float(np.max(np.abs(image_reference - image_observed))),
        "image_minimum_cosine": _minimum_row_cosine(image_reference, image_observed),
        "text_max_absolute_error": float(np.max(np.abs(text_reference - text_observed))),
        "text_minimum_cosine": _minimum_row_cosine(text_reference, text_observed),
    }


def _worker(model_name: str, root: Path, sample_count: int, repetitions: int, threads: int) -> dict[str, object]:
    import onnxruntime as ort
    import psutil

    vision_path = root / "onnx" / "vision_model.onnx"
    text_path = root / "onnx" / "text_model.onnx"
    tokenizer_path = root / "tokenizer.json"
    missing = [str(path) for path in (vision_path, text_path, tokenizer_path) if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing benchmark inputs: " + ", ".join(missing))

    process = psutil.Process()
    baseline_rss = process.memory_info().rss
    options = _session_options(ort, threads)
    load_started = time.perf_counter()
    vision = ort.InferenceSession(str(vision_path), sess_options=options, providers=["CPUExecutionProvider"])
    text = ort.InferenceSession(str(text_path), sess_options=options, providers=["CPUExecutionProvider"])
    load_seconds = time.perf_counter() - load_started
    loaded_rss = process.memory_info().rss

    vision_input = vision.get_inputs()[0]
    height = vision_input.shape[-2] if isinstance(vision_input.shape[-2], int) else 224
    width = vision_input.shape[-1] if isinstance(vision_input.shape[-1], int) else 224
    rng = np.random.default_rng(20260902)
    samples = rng.standard_normal((sample_count, 3, height, width), dtype=np.float32)
    input_name = vision_input.name
    output_name = vision.get_outputs()[0].name
    vision.run([output_name], {input_name: samples[:1]})

    throughput: dict[str, object] = {}
    peak_rss = loaded_rss
    for batch_size in (1, 4, 8):
        durations: list[float] = []
        for _ in range(repetitions):
            started = time.perf_counter()
            for offset in range(0, sample_count, batch_size):
                vision.run([output_name], {input_name: samples[offset : offset + batch_size]})
            durations.append(time.perf_counter() - started)
            peak_rss = max(peak_rss, process.memory_info().rss)
        median = statistics.median(durations)
        throughput[str(batch_size)] = {
            "median_seconds": round(median, 6),
            "images_per_second": round(sample_count / median, 3),
            "runs_seconds": [round(value, 6) for value in durations],
        }

    tokenizer = _tokenizer(tokenizer_path)
    prompts = (
        "baseball",
        "red car",
        "dog on a beach",
        "people at a restaurant",
        "a mountain at sunset",
        "birthday party indoors",
        "a person riding a bicycle",
        "snow-covered trees",
    )
    text_input = text.get_inputs()[0].name
    text_output = text.get_outputs()[0].name
    text_latencies: list[float] = []
    for _ in range(3):
        for prompt in prompts:
            started = time.perf_counter()
            ids = np.asarray([tokenizer.encode(prompt).ids], dtype=np.int64)
            text.run([text_output], {text_input: ids})
            text_latencies.append((time.perf_counter() - started) * 1000.0)
    peak_rss = max(peak_rss, process.memory_info().rss)

    file_bytes = sum(path.stat().st_size for path in (vision_path, text_path, tokenizer_path))
    return {
        "model": model_name,
        "root": str(root.resolve()),
        "provider": "CPUExecutionProvider",
        "threads": threads,
        "model_bytes": file_bytes,
        "model_megabytes": round(file_bytes / (1024 * 1024), 2),
        "session_load_seconds": round(load_seconds, 6),
        "rss_increase_megabytes": round((loaded_rss - baseline_rss) / (1024 * 1024), 2),
        "peak_rss_megabytes": round(peak_rss / (1024 * 1024), 2),
        "embedding_dimension": int(np.asarray(vision.run([output_name], {input_name: samples[:1]})[0]).shape[-1]),
        "image_throughput": throughput,
        "text_query_ms": {
            "median": round(statistics.median(text_latencies), 3),
            "p95": round(_percentile(text_latencies, 95), 3),
            "samples": len(text_latencies),
        },
    }


def _run_isolated(model_name: str, root: Path, sample_count: int, repetitions: int, threads: int) -> dict[str, object]:
    output = subprocess.check_output(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--worker",
            "--model-name",
            model_name,
            "--model-root",
            str(root),
            "--samples",
            str(sample_count),
            "--repetitions",
            str(repetitions),
            "--threads",
            str(threads),
        ],
        text=True,
    )
    return json.loads(output)


def _markdown(report: dict[str, object]) -> str:
    rows = []
    for result in report["results"]:
        batch = result["image_throughput"]["4"]
        rows.append(
            "| {model} | {size:.2f} MB | {load:.3f} s | {memory:.2f} MB | {image:.2f} | {text:.2f} ms | {dim} |".format(
                model=result["model"],
                size=result["model_megabytes"],
                load=result["session_load_seconds"],
                memory=result["rss_increase_megabytes"],
                image=batch["images_per_second"],
                text=result["text_query_ms"]["median"],
                dim=result["embedding_dimension"],
            )
        )
    return "\n".join(
        [
            "# TinyCLIP Speed Benchmark",
            "",
            f"- CPU: {report['system']['processor']}",
            f"- ONNX Runtime: {report['system']['onnxruntime']}",
            f"- Threads: {report['settings']['threads']}",
            f"- Images per run: {report['settings']['samples']}",
            f"- Repetitions: {report['settings']['repetitions']}",
            "- Image throughput shown at batch size 4.",
            "",
            "| Model | Files | Load | RSS increase | Images/s | Text query | Embedding |",
            "|---|---:|---:|---:|---:|---:|---:|",
            *rows,
            "",
            "Full batch-size timings and raw repetitions are in `benchmark.json`.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark TinyCLIP against Image Triage's current CLIP ONNX model.")
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--model-name", default="")
    parser.add_argument("--model-root", type=Path)
    parser.add_argument("--samples", type=int, default=24)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--threads", type=int, default=min(4, os.cpu_count() or 1))
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(_worker(args.model_name, args.model_root, args.samples, args.repetitions, args.threads)))
        return 0

    tiny_metadata = _export_tinyclip()
    export_validation = _validate_tinyclip_export(args.threads)
    current = _run_isolated("Current CLIP ViT-L/14", CURRENT_ROOT, args.samples, args.repetitions, args.threads)
    tiny = _run_isolated("TinyCLIP ViT-8M/16 Text-3M", TINY_ROOT, args.samples, args.repetitions, args.threads)

    import onnxruntime as ort

    report = {
        "system": {
            "platform": platform.platform(),
            "processor": platform.processor() or os.environ.get("PROCESSOR_IDENTIFIER", "unknown"),
            "python": sys.version.split()[0],
            "onnxruntime": ort.__version__,
        },
        "settings": {
            "samples": args.samples,
            "repetitions": args.repetitions,
            "threads": args.threads,
            "provider": "CPUExecutionProvider",
            "random_seed": 20260902,
        },
        "tinyclip_export": tiny_metadata,
        "tinyclip_export_validation": export_validation,
        "results": [current, tiny],
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    json_path = RESULTS_DIR / "benchmark.json"
    markdown_path = RESULTS_DIR / "README.md"
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_markdown(report), encoding="utf-8")
    print(markdown_path.read_text(encoding="utf-8"))
    print(f"JSON: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
