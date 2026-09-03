from __future__ import annotations

import csv
import gc
import html
import json
import math
from pathlib import Path
import statistics
import time

import numpy as np
from PIL import Image

import benchmark


ROOT = Path(__file__).resolve().parent
DATASET_ROOT = ROOT / "datasets" / "coco128"
IMAGE_ROOT = DATASET_ROOT / "images" / "train2017"
LABEL_ROOT = DATASET_ROOT / "labels" / "train2017"
CACHE_ROOT = ROOT / "cache" / "quality"
RESULTS_ROOT = ROOT / "results"

COCO_CLASSES = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", "dog",
    "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", "kite",
    "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", "bottle",
    "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich",
    "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote",
    "keyboard", "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator", "book",
    "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush",
)

PAIR_CANDIDATES = (
    ("person", "bicycle"),
    ("person", "car"),
    ("person", "dog"),
    ("person", "horse"),
    ("person", "sports ball"),
    ("person", "chair"),
    ("person", "bottle"),
    ("car", "traffic light"),
    ("car", "truck"),
    ("chair", "dining table"),
    ("cup", "dining table"),
    ("bottle", "dining table"),
)


def _load_dataset() -> tuple[list[Path], list[set[str]]]:
    paths = sorted(IMAGE_ROOT.glob("*.jpg"))
    if not paths:
        raise FileNotFoundError(f"COCO128 images are missing from {IMAGE_ROOT}")
    labels: list[set[str]] = []
    for image_path in paths:
        label_path = LABEL_ROOT / f"{image_path.stem}.txt"
        classes: set[str] = set()
        if label_path.is_file():
            for line in label_path.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if parts:
                    class_id = int(parts[0])
                    if 0 <= class_id < len(COCO_CLASSES):
                        classes.add(COCO_CLASSES[class_id])
        labels.append(classes)
    return paths, labels


def _load_captions(paths: list[Path]) -> tuple[list[str], np.ndarray]:
    caption_path = ROOT / "datasets" / "annotations" / "captions_train2017.json"
    payload = json.loads(caption_path.read_text(encoding="utf-8"))
    path_index = {int(path.stem): index for index, path in enumerate(paths)}
    captions: list[str] = []
    targets: list[int] = []
    for annotation in payload.get("annotations", []):
        target = path_index.get(int(annotation["image_id"]))
        caption = " ".join(str(annotation.get("caption", "")).split())
        if target is not None and caption:
            captions.append(caption)
            targets.append(target)
    if not captions:
        raise ValueError("No COCO captions matched the benchmark images")
    return captions, np.asarray(targets, dtype=np.int64)


def _preprocess(path: Path, image_size: int) -> np.ndarray:
    with Image.open(path) as opened:
        image = opened.convert("RGB").resize((image_size, image_size), Image.Resampling.BILINEAR)
    array = np.asarray(image, dtype=np.float32) / 255.0
    mean = np.asarray([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
    std = np.asarray([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
    return ((array - mean) / std).transpose(2, 0, 1)


def _normalized(array: np.ndarray) -> np.ndarray:
    values = np.asarray(array, dtype=np.float32)
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    return values / np.maximum(norms, np.finfo(np.float32).eps)


def _encode_model(name: str, model_root: Path, paths: list[Path], threads: int = 4) -> tuple[np.ndarray, object]:
    import onnxruntime as ort
    from tokenizers import Tokenizer

    cache_path = CACHE_ROOT / f"{name}.npz"
    vision_path = model_root / "onnx" / "vision_model.onnx"
    text_path = model_root / "onnx" / "text_model.onnx"
    tokenizer_path = model_root / "tokenizer.json"
    signature = json.dumps(
        {
            "vision_size": vision_path.stat().st_size,
            "vision_mtime": vision_path.stat().st_mtime_ns,
            "images": [(path.name, path.stat().st_size, path.stat().st_mtime_ns) for path in paths],
        },
        separators=(",", ":"),
    )
    if cache_path.is_file():
        cached = np.load(cache_path, allow_pickle=False)
        if str(cached["signature"].item()) == signature:
            image_embeddings = np.asarray(cached["embeddings"], dtype=np.float32)
        else:
            image_embeddings = None
    else:
        image_embeddings = None

    options = benchmark._session_options(ort, threads)
    if image_embeddings is None:
        vision = ort.InferenceSession(str(vision_path), sess_options=options, providers=["CPUExecutionProvider"])
        input_meta = vision.get_inputs()[0]
        image_size = input_meta.shape[-1] if isinstance(input_meta.shape[-1], int) else 224
        inputs = np.stack([_preprocess(path, image_size) for path in paths])
        output_name = vision.get_outputs()[0].name
        rows = []
        started = time.perf_counter()
        for offset in range(0, len(paths), 8):
            rows.append(vision.run([output_name], {input_meta.name: inputs[offset : offset + 8]})[0])
        elapsed = time.perf_counter() - started
        image_embeddings = _normalized(np.concatenate(rows, axis=0))
        CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(cache_path, signature=signature, embeddings=image_embeddings, elapsed=elapsed)
        del vision, inputs, rows
        gc.collect()

    text_session = ort.InferenceSession(str(text_path), sess_options=options, providers=["CPUExecutionProvider"])
    tokenizer = Tokenizer.from_file(str(tokenizer_path))
    pad_id = tokenizer.token_to_id("<|endoftext|>")
    if pad_id is None:
        raise RuntimeError(f"Tokenizer lacks <|endoftext|>: {tokenizer_path}")
    tokenizer.enable_truncation(max_length=77)
    tokenizer.enable_padding(length=77, pad_id=pad_id, pad_token="<|endoftext|>")

    def encode_text(prompts: list[str]) -> np.ndarray:
        rows = []
        for offset in range(0, len(prompts), 32):
            ids = np.asarray(
                [tokenizer.encode(prompt).ids for prompt in prompts[offset : offset + 32]],
                dtype=np.int64,
            )
            rows.append(
                text_session.run(
                    [text_session.get_outputs()[0].name],
                    {text_session.get_inputs()[0].name: ids},
                )[0]
            )
        return _normalized(np.concatenate(rows, axis=0))

    return image_embeddings, encode_text


def _average_precision(relevant: np.ndarray, order: np.ndarray) -> float:
    ranked = relevant[order].astype(np.float64)
    positive_count = int(np.sum(ranked))
    if positive_count == 0:
        return 0.0
    precision = np.cumsum(ranked) / np.arange(1, len(ranked) + 1)
    return float(np.sum(precision * ranked) / positive_count)


def _ndcg(relevant: np.ndarray, order: np.ndarray, k: int) -> float:
    ranked = relevant[order[:k]].astype(np.float64)
    discounts = 1.0 / np.log2(np.arange(2, len(ranked) + 2))
    dcg = float(np.sum(ranked * discounts))
    ideal_count = min(int(np.sum(relevant)), k)
    ideal = float(np.sum(discounts[:ideal_count]))
    return dcg / ideal if ideal else 0.0


def _query_specs(labels: list[set[str]]) -> list[dict[str, object]]:
    specs: list[dict[str, object]] = []
    for class_name in COCO_CLASSES:
        relevant = np.asarray([class_name in item for item in labels], dtype=bool)
        positives = int(np.sum(relevant))
        if positives >= 2:
            specs.append({"query": class_name, "kind": "object", "relevant": relevant, "positives": positives})
    for left, right in PAIR_CANDIDATES:
        relevant = np.asarray([left in item and right in item for item in labels], dtype=bool)
        positives = int(np.sum(relevant))
        if positives >= 2:
            specs.append(
                {
                    "query": f"{left} and {right}",
                    "kind": "multi_object",
                    "relevant": relevant,
                    "positives": positives,
                }
            )
    return specs


def _evaluate_captions(
    images: np.ndarray,
    encode_text,
    captions: list[str],
    targets: np.ndarray,
) -> dict[str, object]:
    text = encode_text(captions)
    scores = images @ text.T
    ranks: list[int] = []
    for caption_index, target in enumerate(targets):
        order = np.argsort(-scores[:, caption_index])
        ranks.append(int(np.flatnonzero(order == target)[0]) + 1)
    rank_array = np.asarray(ranks, dtype=np.int64)
    return {
        "caption_count": len(captions),
        "recall_at_1": float(np.mean(rank_array <= 1)),
        "recall_at_5": float(np.mean(rank_array <= 5)),
        "recall_at_10": float(np.mean(rank_array <= 10)),
        "mean_reciprocal_rank": float(np.mean(1.0 / rank_array)),
        "median_rank": float(np.median(rank_array)),
        "ranks": ranks,
    }


def _evaluate(
    specs: list[dict[str, object]],
    images: np.ndarray,
    encode_text,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    prompts = [str(spec["query"]) for spec in specs]
    text = encode_text(prompts)
    scores = images @ text.T
    rows: list[dict[str, object]] = []
    for index, spec in enumerate(specs):
        relevant = np.asarray(spec["relevant"], dtype=bool)
        order = np.argsort(-scores[:, index])
        top5 = relevant[order[:5]]
        rows.append(
            {
                "query": spec["query"],
                "kind": spec["kind"],
                "positives": spec["positives"],
                "average_precision": _average_precision(relevant, order),
                "precision_at_5": float(np.mean(top5)),
                "recall_at_5": float(np.sum(top5) / np.sum(relevant)),
                "ndcg_at_10": _ndcg(relevant, order, 10),
                "order": order.tolist(),
                "scores": scores[order, index].tolist(),
            }
        )
    summary: dict[str, object] = {
        "mean_average_precision": statistics.fmean(float(row["average_precision"]) for row in rows),
        "mean_precision_at_5": statistics.fmean(float(row["precision_at_5"]) for row in rows),
        "mean_recall_at_5": statistics.fmean(float(row["recall_at_5"]) for row in rows),
        "mean_ndcg_at_10": statistics.fmean(float(row["ndcg_at_10"]) for row in rows),
    }
    summary["by_kind"] = {
        kind: {
            "query_count": len(kind_rows),
            "mean_average_precision": statistics.fmean(float(row["average_precision"]) for row in kind_rows),
            "mean_precision_at_5": statistics.fmean(float(row["precision_at_5"]) for row in kind_rows),
            "mean_recall_at_5": statistics.fmean(float(row["recall_at_5"]) for row in kind_rows),
            "mean_ndcg_at_10": statistics.fmean(float(row["ndcg_at_10"]) for row in kind_rows),
        }
        for kind in ("object", "multi_object")
        if (kind_rows := [row for row in rows if row["kind"] == kind])
    }
    return summary, rows


def _visual_report(paths: list[Path], labels: list[set[str]], comparison: list[dict[str, object]]) -> str:
    largest_gaps = sorted(comparison, key=lambda row: abs(float(row["ap_delta"])), reverse=True)[:10]
    pair_rows = [row for row in comparison if row["kind"] == "multi_object"][:4]
    selected = []
    seen = set()
    for row in largest_gaps + pair_rows:
        if row["query"] not in seen:
            selected.append(row)
            seen.add(row["query"])

    sections = []
    for row in selected:
        columns = []
        for model_key, model_label in (("current", "Current CLIP"), ("tiny", "TinyCLIP")):
            cards = []
            for image_index, score in zip(row[model_key]["order"][:5], row[model_key]["scores"][:5]):
                path = paths[int(image_index)]
                relevant = bool(row["relevant"][int(image_index)])
                rel_path = Path("..") / "datasets" / "coco128" / "images" / "train2017" / path.name
                cards.append(
                    f'<figure class="{"hit" if relevant else "miss"}"><img src="{rel_path.as_posix()}">'
                    f'<figcaption>{"match" if relevant else "miss"} · {float(score):.3f}<br>{html.escape(path.name)}</figcaption></figure>'
                )
            columns.append(f'<div class="model"><h3>{model_label}</h3><div class="grid">{"".join(cards)}</div></div>')
        sections.append(
            f'<section><h2>{html.escape(str(row["query"]))}</h2>'
            f'<p>Relevant images: {row["positives"]} · AP delta (Tiny - Current): {float(row["ap_delta"]):+.3f}</p>'
            f'<div class="models">{"".join(columns)}</div></section>'
        )
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>TinyCLIP quality comparison</title>
<style>
body{{font:14px Arial,sans-serif;margin:24px;background:#15171a;color:#f1f3f5}}h1{{font-size:26px}}h2{{margin-bottom:4px}}
section{{border-top:1px solid #454a52;padding:20px 0}}.models{{display:grid;grid-template-columns:1fr 1fr;gap:24px}}
.grid{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px}}figure{{margin:0;border:3px solid #a33;background:#222}}
figure.hit{{border-color:#2c9b58}}img{{width:100%;aspect-ratio:1;object-fit:cover;display:block}}figcaption{{padding:6px;font-size:11px;line-height:1.4}}
@media(max-width:1000px){{.models{{grid-template-columns:1fr}}}}
</style></head><body><h1>TinyCLIP vs current CLIP</h1>
<p>Green borders contain the requested COCO labels; red borders do not. Queries shown have the largest AP differences plus multi-object examples.</p>
{"".join(sections)}</body></html>"""


def _markdown_summary(report: dict[str, object], comparison: list[dict[str, object]]) -> str:
    current = report["models"]["current"]["summary"]
    tiny = report["models"]["tiny"]["summary"]
    rows = [
        "# TinyCLIP Retrieval Quality",
        "",
        f"COCO128: {report['image_count']} images, {report['object_query_count']} object queries, "
        f"{report['multi_object_query_count']} multi-object queries.",
        "",
        "| Query set | Current mAP | TinyCLIP mAP | Relative change | Current P@5 | TinyCLIP P@5 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for label, key in (("All", None), ("Objects", "object"), ("Multi-object", "multi_object")):
        current_values = current if key is None else current["by_kind"][key]
        tiny_values = tiny if key is None else tiny["by_kind"][key]
        current_map = float(current_values["mean_average_precision"])
        tiny_map = float(tiny_values["mean_average_precision"])
        relative = (tiny_map / current_map - 1.0) * 100.0
        rows.append(
            f"| {label} | {current_map:.3f} | {tiny_map:.3f} | {relative:+.1f}% | "
            f"{float(current_values['mean_precision_at_5']):.3f} | {float(tiny_values['mean_precision_at_5']):.3f} |"
        )
    wins = sum(float(row["ap_delta"]) > 0.001 for row in comparison)
    losses = sum(float(row["ap_delta"]) < -0.001 for row in comparison)
    ties = len(comparison) - wins - losses
    rows.extend(
        [
            "",
            f"TinyCLIP wins {wins} queries, loses {losses}, and is effectively tied on {ties}.",
            "",
            "## Caption-to-image retrieval",
            "",
            "| Model | Captions | R@1 | R@5 | R@10 | MRR | Median rank |",
            "|---|---:|---:|---:|---:|---:|---:|",
            f"| Current CLIP | {current['caption_to_image']['caption_count']} | "
            f"{current['caption_to_image']['recall_at_1']:.3f} | {current['caption_to_image']['recall_at_5']:.3f} | "
            f"{current['caption_to_image']['recall_at_10']:.3f} | {current['caption_to_image']['mean_reciprocal_rank']:.3f} | "
            f"{current['caption_to_image']['median_rank']:.1f} |",
            f"| TinyCLIP | {tiny['caption_to_image']['caption_count']} | "
            f"{tiny['caption_to_image']['recall_at_1']:.3f} | {tiny['caption_to_image']['recall_at_5']:.3f} | "
            f"{tiny['caption_to_image']['recall_at_10']:.3f} | {tiny['caption_to_image']['mean_reciprocal_rank']:.3f} | "
            f"{tiny['caption_to_image']['median_rank']:.1f} |",
            "",
            "## Largest TinyCLIP regressions",
            "",
            "| Query | Kind | Current AP | TinyCLIP AP | Delta |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for row in sorted(comparison, key=lambda item: float(item["ap_delta"]))[:10]:
        rows.append(
            f"| {row['query']} | {row['kind']} | {float(row['current']['average_precision']):.3f} | "
            f"{float(row['tiny']['average_precision']):.3f} | {float(row['ap_delta']):+.3f} |"
        )
    rows.extend(
        [
            "",
            "## Largest TinyCLIP gains",
            "",
            "| Query | Kind | Current AP | TinyCLIP AP | Delta |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for row in sorted(comparison, key=lambda item: float(item["ap_delta"]), reverse=True)[:10]:
        rows.append(
            f"| {row['query']} | {row['kind']} | {float(row['current']['average_precision']):.3f} | "
            f"{float(row['tiny']['average_precision']):.3f} | {float(row['ap_delta']):+.3f} |"
        )
    rows.extend(
        [
            "",
            "Object-label metrics measure presence only. Caption retrieval additionally tests actions, relationships, colors, and scenes against the exact source image.",
            "",
        ]
    )
    return "\n".join(rows)


def main() -> int:
    paths, labels = _load_dataset()
    captions, caption_targets = _load_captions(paths)
    specs = _query_specs(labels)
    models = (
        ("current", "Current CLIP ViT-L/14", benchmark.CURRENT_ROOT),
        ("tiny", "TinyCLIP ViT-8M/16 Text-3M", benchmark.TINY_ROOT),
    )
    evaluated: dict[str, dict[str, object]] = {}
    for key, label, root in models:
        print(f"Encoding and evaluating {label}...", flush=True)
        images, encode_text = _encode_model(key, root, paths)
        summary, rows = _evaluate(specs, images, encode_text)
        summary["caption_to_image"] = _evaluate_captions(images, encode_text, captions, caption_targets)
        evaluated[key] = {"label": label, "summary": summary, "rows": rows}
        del images, encode_text
        gc.collect()

    comparison = []
    for index, spec in enumerate(specs):
        current = evaluated["current"]["rows"][index]
        tiny = evaluated["tiny"]["rows"][index]
        comparison.append(
            {
                "query": spec["query"],
                "kind": spec["kind"],
                "positives": spec["positives"],
                "relevant": np.asarray(spec["relevant"], dtype=bool).tolist(),
                "ap_delta": float(tiny["average_precision"]) - float(current["average_precision"]),
                "current": current,
                "tiny": tiny,
            }
        )

    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    report = {
        "dataset": "Ultralytics COCO128",
        "image_count": len(paths),
        "object_query_count": sum(spec["kind"] == "object" for spec in specs),
        "multi_object_query_count": sum(spec["kind"] == "multi_object" for spec in specs),
        "caption_count": len(captions),
        "models": {
            key: {"label": value["label"], "summary": value["summary"]}
            for key, value in evaluated.items()
        },
        "queries": comparison,
    }
    json_path = RESULTS_ROOT / "quality.json"
    csv_path = RESULTS_ROOT / "quality_queries.csv"
    html_path = RESULTS_ROOT / "quality_report.html"
    markdown_path = RESULTS_ROOT / "quality_summary.md"
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("query", "kind", "positives", "current_ap", "tiny_ap", "ap_delta", "current_p5", "tiny_p5"))
        for row in comparison:
            writer.writerow(
                (
                    row["query"], row["kind"], row["positives"],
                    row["current"]["average_precision"], row["tiny"]["average_precision"], row["ap_delta"],
                    row["current"]["precision_at_5"], row["tiny"]["precision_at_5"],
                )
            )
    html_path.write_text(_visual_report(paths, labels, comparison), encoding="utf-8")
    markdown_path.write_text(_markdown_summary(report, comparison), encoding="utf-8")

    console_summary = {}
    for key, value in evaluated.items():
        summary = dict(value["summary"])
        caption_summary = dict(summary["caption_to_image"])
        caption_summary.pop("ranks", None)
        summary["caption_to_image"] = caption_summary
        console_summary[key] = summary
    print(json.dumps(console_summary, indent=2))
    print(f"Queries: {len(specs)} ({report['object_query_count']} object, {report['multi_object_query_count']} multi-object)")
    print(f"Visual report: {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
