#!/usr/bin/env python3
"""Throughput benchmark for an ARNIQA-style synthetic-degradation quality model (docs/ai_quality_plan.md, TOPIQ replacement).

Measures, on generated images only (nothing is downloaded and no photos are read):
  1. how many degraded 224 px crops per second the CPU data loader produces, per worker count;
  2. how many crops per second the GPU trains on (ResNet-50 and MobileNetV3-large, mixed precision, random weights);
  3. the two together for a short contrastive run, plus peak GPU memory.

Usage:  python3 bench_iqa_training.py --out results.json [--workers 8 16 32 48] [--seconds 10]
Run it at low priority on shared machines:  nice -n 10 python3 bench_iqa_training.py ...
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import time

import cv2
import numpy as np
import torch
import torch.nn.functional as F
import torchvision
from torch.utils.data import DataLoader, Dataset

cv2.setNumThreads(1)  # before any cv2 call: forked loader workers deadlock on OpenCV's thread pool otherwise
CROP = 224
VIEW = 512  # distortions are applied to a VIEW x VIEW region, then two CROP crops are taken from it


def make_base_images(count: int, seed: int = 0) -> list[np.ndarray]:
    """Smooth colour fields plus shapes and a little texture: cheap stand-ins for photographs."""
    rng = np.random.default_rng(seed)
    images = []
    for _ in range(count):
        small = rng.random((6, 9, 3)).astype(np.float32)
        base = cv2.resize(small, (768, 512), interpolation=cv2.INTER_CUBIC)
        for _ in range(8):
            center = (int(rng.integers(0, 768)), int(rng.integers(0, 512)))
            color = tuple(float(v) for v in rng.random(3))
            cv2.circle(base, center, int(rng.integers(15, 120)), color, -1)
        base += rng.normal(0.0, 0.02, base.shape).astype(np.float32)
        images.append((np.clip(base, 0, 1) * 255).astype(np.uint8))
    return images


# Each distortion takes (uint8 HxWx3, level in 1..5) and returns uint8.
def _blur(img, level):
    sigma = (0.3, 0.6, 1.2, 2.4, 4.0)[level - 1]
    return cv2.GaussianBlur(img, (0, 0), sigma)


def _motion(img, level):
    size = (3, 5, 9, 17, 31)[level - 1]
    kernel = np.zeros((size, size), np.float32)
    kernel[size // 2, :] = 1.0 / size
    return cv2.filter2D(img, -1, kernel)


def _noise(img, level):
    sigma = (2, 5, 10, 20, 35)[level - 1]
    noisy = img.astype(np.float32) + np.random.normal(0.0, sigma, img.shape).astype(np.float32)
    return np.clip(noisy, 0, 255).astype(np.uint8)


def _jpeg(img, level):
    quality = (90, 75, 55, 35, 15)[level - 1]
    ok, buffer = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return cv2.imdecode(buffer, cv2.IMREAD_COLOR)


def _downscale(img, level):
    scale = (0.90, 0.75, 0.55, 0.35, 0.20)[level - 1]
    h, w = img.shape[:2]
    small = cv2.resize(img, (max(8, int(w * scale)), max(8, int(h * scale))), interpolation=cv2.INTER_AREA)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def _exposure(img, level):
    stops = (0.25, 0.5, 1.0, 1.5, 2.0)[level - 1] * (1 if np.random.random() < 0.5 else -1)
    return np.clip(img.astype(np.float32) * (2.0 ** stops), 0, 255).astype(np.uint8)


DISTORTIONS = (_blur, _motion, _noise, _jpeg, _downscale, _exposure)


def distort(img: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    if rng.random() < 0.05:
        return img
    count = int(rng.choice((1, 2, 3), p=(0.6, 0.3, 0.1)))
    for index in rng.choice(len(DISTORTIONS), size=count, replace=False):
        img = DISTORTIONS[int(index)](img, int(rng.integers(1, 6)))
    return img


class DegradedPairs(Dataset):
    """Item: 4 crops (two distorted versions of one image, two crops of each), as float tensors in [0, 1]."""

    def __init__(self, images: list[np.ndarray], length: int = 1_000_000) -> None:
        self.images = images
        self.length = length

    def __len__(self) -> int:
        return self.length

    def __getitem__(self, index: int) -> torch.Tensor:
        cv2.setNumThreads(1)
        rng = np.random.default_rng((index * 2654435761) % (2**32))
        np.random.seed(int(rng.integers(0, 2**31)))
        base = self.images[int(rng.integers(0, len(self.images)))]
        h, w = base.shape[:2]
        y, x = int(rng.integers(0, h - VIEW + 1)), int(rng.integers(0, w - VIEW + 1))
        region = base[y : y + VIEW, x : x + VIEW]
        crops = []
        for _ in range(2):
            view = distort(region, rng)
            for _ in range(2):
                cy, cx = int(rng.integers(0, VIEW - CROP + 1)), int(rng.integers(0, VIEW - CROP + 1))
                crops.append(torch.from_numpy(np.ascontiguousarray(view[cy : cy + CROP, cx : cx + CROP])))
        return torch.stack(crops).permute(0, 3, 1, 2)  # uint8, 4x3xHxW


def _worker_init(_worker_id: int) -> None:
    cv2.setNumThreads(1)
    torch.set_num_threads(1)


def bench_loader(images, workers: int, seconds: float, batch: int = 16) -> float:
    loader = DataLoader(
        DegradedPairs(images), batch_size=batch, num_workers=workers, persistent_workers=False,
        prefetch_factor=4, pin_memory=False, worker_init_fn=_worker_init,
    )
    it = iter(loader)
    next(it)  # warm up the workers
    start, crops = time.perf_counter(), 0
    while time.perf_counter() - start < seconds:
        crops += next(it).shape[0] * 4
    rate = crops / (time.perf_counter() - start)
    del it, loader
    return rate


def build_model(name: str) -> torch.nn.Module:
    if name == "resnet50":
        model = torchvision.models.resnet50(weights=None)
        model.fc = torch.nn.Linear(2048, 128)
    else:
        model = torchvision.models.mobilenet_v3_large(weights=None)
        model.classifier = torch.nn.Linear(960, 128)
    return model


def contrastive_loss(embeddings: torch.Tensor, labels: torch.Tensor) -> torch.Tensor:
    z = F.normalize(embeddings.float(), dim=1)
    logits = z @ z.t() / 0.1
    logits.fill_diagonal_(-1e4)
    positives = (labels[:, None] == labels[None, :]) & ~torch.eye(len(z), dtype=torch.bool, device=z.device)
    log_prob = logits - torch.logsumexp(logits, dim=1, keepdim=True)
    return -(log_prob * positives).sum(1).div(positives.sum(1).clamp(min=1)).mean()


def bench_gpu(model_name: str, seconds: float, batch_images: int = 32, loader=None) -> dict:
    device = torch.device("cuda")
    model = build_model(model_name).to(device).to(memory_format=torch.channels_last)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-4)
    scaler = torch.amp.GradScaler("cuda")
    labels = torch.arange(batch_images, device=device).repeat_interleave(4)
    torch.cuda.reset_peak_memory_stats()
    source = iter(loader) if loader is not None else None
    fixed = torch.randint(0, 255, (batch_images, 4, 3, CROP, CROP), dtype=torch.uint8) if source is None else None
    steps, crops, start = 0, 0, None
    deadline = None
    while True:
        batch = next(source) if source is not None else fixed
        x = batch.to(device, non_blocking=True).flatten(0, 1).float().div_(255).contiguous(memory_format=torch.channels_last)
        with torch.autocast("cuda", dtype=torch.float16):
            loss = contrastive_loss(model(x), labels[: x.shape[0]])
        optimizer.zero_grad(set_to_none=True)
        scaler.scale(loss).backward()
        scaler.step(optimizer)
        scaler.update()
        steps += 1
        if steps == 3:  # warm-up done
            torch.cuda.synchronize()
            start, crops = time.perf_counter(), 0
            deadline = start + seconds
        elif start is not None:
            crops += x.shape[0]
            if time.perf_counter() > deadline:
                break
    torch.cuda.synchronize()
    return {
        "crops_per_s": crops / (time.perf_counter() - start),
        "peak_gpu_mem_gb": torch.cuda.max_memory_allocated() / 1e9,
        "final_loss": float(loss),
    }


def cpu_model() -> str:
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="bench_iqa_training.json")
    parser.add_argument("--workers", type=int, nargs="+", default=[8, 16, 32, 48])
    parser.add_argument("--seconds", type=float, default=10.0)
    args = parser.parse_args()

    result: dict = {
        "cpu": cpu_model(),
        "logical_cpus": os.cpu_count(),
        "torch": torch.__version__,
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "crop": CROP,
        "view": VIEW,
    }
    print(json.dumps(result, indent=2), flush=True)
    images = make_base_images(48)

    result["loader_crops_per_s"] = {}
    for workers in args.workers:
        rate = bench_loader(images, workers, args.seconds)
        result["loader_crops_per_s"][str(workers)] = rate
        print(f"loader workers={workers:3d}: {rate:8.0f} crops/s", flush=True)
    best_workers = max(result["loader_crops_per_s"], key=result["loader_crops_per_s"].get)
    result["best_workers"] = int(best_workers)

    if torch.cuda.is_available():
        result["gpu_only"] = {}
        for name in ("resnet50", "mobilenet_v3_large"):
            result["gpu_only"][name] = bench_gpu(name, args.seconds)
            print(f"gpu only {name}: {result['gpu_only'][name]}", flush=True)
        loader = DataLoader(
            DegradedPairs(images), batch_size=32, num_workers=int(best_workers), prefetch_factor=4, pin_memory=True,
            worker_init_fn=_worker_init,
        )
        result["end_to_end"] = {}
        for name in ("resnet50", "mobilenet_v3_large"):
            result["end_to_end"][name] = bench_gpu(name, args.seconds * 2, batch_images=32, loader=loader)
            print(f"end to end {name}: {result['end_to_end'][name]}", flush=True)

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
    print("wrote", args.out)


if __name__ == "__main__":
    main()
