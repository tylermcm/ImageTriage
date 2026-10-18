#!/usr/bin/env python3
"""Self-supervised technical-quality model, ARNIQA-style (docs/ai_quality_plan.md, TOPIQ replacement, route 3).

Our own implementation of the idea (no ARNIQA code, weights or data): take near-pristine photos we own, damage them
programmatically at known severities, and train a network so that
  * two crops of the same damaged image have similar embeddings, other images' crops differ (NT-Xent), and
  * it predicts how damaged the image is (overall severity and a level per damage type).
The quality score used downstream is 1 - predicted severity. Random initial weights: no pretrained backbone is used.

  python3 train.py --data data --out run1 --model mobilenet_v3_large --epochs 30
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time

import cv2
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision
from torch.utils.data import DataLoader, Dataset

cv2.setNumThreads(1)  # before any cv2 call: forked loader workers deadlock on OpenCV's thread pool otherwise
CROP, VIEW = 224, 512


# ---- damage -------------------------------------------------------------------------------------------------------
def _blur(img, level):
    return cv2.GaussianBlur(img, (0, 0), (0.3, 0.6, 1.2, 2.4, 4.0)[level - 1])


def _motion(img, level):
    size = (3, 5, 9, 17, 31)[level - 1]
    kernel = np.zeros((size, size), np.float32)
    angle = np.random.uniform(0, 180)
    center = (size // 2, size // 2)
    cv2.line(kernel, (0, size // 2), (size - 1, size // 2), 1.0, 1)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    kernel = cv2.warpAffine(kernel, matrix, (size, size))
    kernel /= max(kernel.sum(), 1e-6)
    return cv2.filter2D(img, -1, kernel)


def _noise(img, level):
    sigma = (2, 5, 10, 20, 35)[level - 1]
    x = img.astype(np.float32)
    shot = np.random.normal(0.0, 1.0, img.shape).astype(np.float32) * np.sqrt(np.maximum(x, 1.0)) * sigma / 12.0
    read = np.random.normal(0.0, sigma * 0.5, img.shape).astype(np.float32)
    return np.clip(x + shot + read, 0, 255).astype(np.uint8)


def _jpeg(img, level):
    quality = (90, 75, 55, 35, 15)[level - 1]
    _, buffer = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return cv2.imdecode(buffer, cv2.IMREAD_COLOR)


def _downscale(img, level):
    scale = (0.90, 0.75, 0.55, 0.35, 0.20)[level - 1]
    h, w = img.shape[:2]
    small = cv2.resize(img, (max(8, int(w * scale)), max(8, int(h * scale))), interpolation=cv2.INTER_AREA)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def _exposure(img, level):
    stops = (0.25, 0.5, 1.0, 1.5, 2.0)[level - 1] * (1 if np.random.random() < 0.5 else -1)
    return np.clip(img.astype(np.float32) * (2.0 ** stops), 0, 255).astype(np.uint8)


def _contrast(img, level):
    factor = (0.9, 0.75, 0.6, 0.45, 0.3)[level - 1] if np.random.random() < 0.5 else (1.15, 1.3, 1.5, 1.8, 2.2)[level - 1]
    x = img.astype(np.float32)
    return np.clip((x - 127.5) * factor + 127.5, 0, 255).astype(np.uint8)


def _saturation(img, level):
    amount = (0.15, 0.3, 0.5, 0.75, 1.0)[level - 1] * (1 if np.random.random() < 0.5 else -1)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[..., 1] = np.clip(hsv[..., 1] * (1.0 + amount), 0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


def _oversharpen(img, level):
    amount = (0.5, 1.0, 1.8, 2.8, 4.0)[level - 1]
    blurred = cv2.GaussianBlur(img, (0, 0), 1.5)
    return cv2.addWeighted(img, 1.0 + amount, blurred, -amount, 0)


DISTORTIONS = (_blur, _motion, _noise, _jpeg, _downscale, _exposure, _contrast, _saturation, _oversharpen)
NAMES = ("blur", "motion", "noise", "jpeg", "downscale", "exposure", "contrast", "saturation", "oversharpen")
N_TYPES = len(DISTORTIONS)


def distort(img: np.ndarray, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
    """Return the damaged image and a vector of levels (0 = untouched, 1..5), one per distortion type."""
    levels = np.zeros(N_TYPES, np.float32)
    if rng.random() < 0.05:
        return img, levels
    count = int(rng.choice((1, 2, 3, 4), p=(0.5, 0.3, 0.15, 0.05)))
    for index in rng.choice(N_TYPES, size=count, replace=False):
        level = int(rng.integers(1, 6))
        img = DISTORTIONS[int(index)](img, level)
        levels[int(index)] = level
    return img, levels


def severity(levels: np.ndarray) -> float:
    """0 for untouched; each applied level 1..5 removes 18% per step of 'quality', so five stacked damages stay below 1."""
    return float(1.0 - np.prod(1.0 - 0.18 * levels))


# ---- data ---------------------------------------------------------------------------------------------------------
class Pairs(Dataset):
    """Item: 4 crops (two damaged versions of one image x two crops), their view ids, severities and per-type levels."""

    def __init__(self, images: np.ndarray, indices: list[int], length: int, seed: int) -> None:
        self.images, self.indices, self.length, self.seed = images, indices, length, seed

    def __len__(self) -> int:
        return self.length

    def __getitem__(self, i: int):
        rng = np.random.default_rng(self.seed + i * 7919)
        np.random.seed(int(rng.integers(0, 2**31)))
        base = self.images[self.indices[int(rng.integers(0, len(self.indices)))]]
        if rng.random() < 0.5:
            base = base[:, ::-1]
        h, w = base.shape[:2]
        y, x = int(rng.integers(0, h - VIEW + 1)), int(rng.integers(0, w - VIEW + 1))
        region = np.ascontiguousarray(base[y : y + VIEW, x : x + VIEW])
        crops, levels_out, sev_out = [], [], []
        for _ in range(2):
            view, levels = distort(region, rng)
            for _ in range(2):
                cy, cx = int(rng.integers(0, VIEW - CROP + 1)), int(rng.integers(0, VIEW - CROP + 1))
                crops.append(torch.from_numpy(np.ascontiguousarray(view[cy : cy + CROP, cx : cx + CROP])))
                levels_out.append(torch.from_numpy(levels))
                sev_out.append(severity(levels))
        return torch.stack(crops).permute(0, 3, 1, 2), torch.tensor(sev_out), torch.stack(levels_out)


def worker_init(_id: int) -> None:
    cv2.setNumThreads(1)
    torch.set_num_threads(1)


# ---- model --------------------------------------------------------------------------------------------------------
class QualityNet(nn.Module):
    def __init__(self, name: str) -> None:
        super().__init__()
        if name == "resnet50":
            backbone = torchvision.models.resnet50(weights=None)
            features = backbone.fc.in_features
            backbone.fc = nn.Identity()
        else:
            backbone = torchvision.models.mobilenet_v3_large(weights=None)
            features = backbone.classifier[0].in_features
            backbone.classifier = nn.Identity()
        self.backbone = backbone
        self.project = nn.Sequential(nn.Linear(features, 256), nn.ReLU(inplace=True), nn.Linear(256, 128))
        self.quality = nn.Sequential(nn.Linear(features, 128), nn.ReLU(inplace=True), nn.Linear(128, 1 + N_TYPES))
        self.register_buffer("mean", torch.tensor([0.406, 0.456, 0.485]).view(1, 3, 1, 1))  # BGR order
        self.register_buffer("std", torch.tensor([0.225, 0.224, 0.229]).view(1, 3, 1, 1))

    def forward(self, bgr_uint8_or_float: torch.Tensor):
        x = bgr_uint8_or_float.float()
        if x.max() > 1.5:
            x = x / 255.0
        feat = self.backbone((x - self.mean) / self.std)
        out = self.quality(feat)
        return self.project(feat), torch.sigmoid(out[:, 0]), torch.sigmoid(out[:, 1:])


class ScoreOnly(nn.Module):
    """What gets exported: image in, quality score out (1 = pristine)."""

    def __init__(self, net: QualityNet) -> None:
        super().__init__()
        self.net = net

    def forward(self, x):
        return 1.0 - self.net(x)[1]


def nt_xent(z: torch.Tensor, group: torch.Tensor, temperature: float = 0.1) -> torch.Tensor:
    z = F.normalize(z.float(), dim=1)
    logits = z @ z.t() / temperature
    eye = torch.eye(len(z), dtype=torch.bool, device=z.device)
    logits = logits.masked_fill(eye, -1e4)
    positives = (group[:, None] == group[None, :]) & ~eye
    log_prob = logits - torch.logsumexp(logits, dim=1, keepdim=True)
    return -(log_prob * positives).sum(1).div(positives.sum(1).clamp(min=1)).mean()


# ---- evaluation ---------------------------------------------------------------------------------------------------
def _ranks(a: np.ndarray) -> np.ndarray:
    order = a.argsort()
    r = np.empty(len(a))
    r[order] = np.arange(len(a))
    return r


def spearman(a, b) -> float:
    a, b = _ranks(np.asarray(a, float)), _ranks(np.asarray(b, float))
    return float(np.corrcoef(a, b)[0, 1]) if a.std() > 0 and b.std() > 0 else float("nan")


@torch.no_grad()
def evaluate(model: QualityNet, images: np.ndarray, indices: list[int], device, count: int = 400) -> dict:
    """On held-out images: (1) Spearman between predicted and true severity over random damage, (2) for each single
    damage type, how often a stronger level scores lower quality than a weaker one on the same crop."""
    model.eval()
    rng = np.random.default_rng(123)
    np.random.seed(123)
    preds, truth = [], []
    for k in range(count):
        base = images[indices[k % len(indices)]]
        y, x = int(rng.integers(0, 512 - CROP + 1)), int(rng.integers(0, 768 - CROP + 1))
        crop = np.ascontiguousarray(base[y : y + CROP, x : x + CROP])
        damaged, levels = distort(crop, rng)
        t = torch.from_numpy(damaged).permute(2, 0, 1)[None].to(device)
        with torch.autocast("cuda", dtype=torch.float16):
            preds.append(float(model(t)[1]))
        truth.append(severity(levels))
    result = {"severity_spearman": spearman(preds, truth)}
    monotone = {}
    for t_index, name in enumerate(NAMES):
        ok, total = 0, 0
        for k in range(60):
            base = images[indices[(k * 5) % len(indices)]]
            y, x = int(rng.integers(0, 512 - CROP + 1)), int(rng.integers(0, 768 - CROP + 1))
            crop = np.ascontiguousarray(base[y : y + CROP, x : x + CROP])
            np.random.seed(1000 + k)
            scores = []
            for level in (1, 3, 5):
                np.random.seed(1000 + k)
                d = DISTORTIONS[t_index](crop, level)
                tt = torch.from_numpy(d).permute(2, 0, 1)[None].to(device)
                with torch.autocast("cuda", dtype=torch.float16):
                    scores.append(float(model(tt)[1]))
            ok += int(scores[0] < scores[1] < scores[2])  # predicted severity rises with level
            total += 1
        monotone[name] = ok / total
    result["monotone_by_type"] = monotone
    model.train()
    return result


# ---- training -----------------------------------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--model", default="mobilenet_v3_large", choices=["mobilenet_v3_large", "resnet50"])
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-images", type=int, default=32)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--eval-every", type=int, default=5)
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    images = np.load(os.path.join(args.data, "images.npy"), mmap_mode="r")
    manifest = json.load(open(os.path.join(args.data, "manifest.json"), encoding="utf-8"))
    train_idx = [m["index"] for m in manifest if m["split"] == "train"]
    hold_idx = [m["index"] for m in manifest if m["split"] == "holdout"] or train_idx[-200:]
    print(f"train images {len(train_idx)}, holdout images {len(hold_idx)}", flush=True)

    device = torch.device("cuda")
    model = QualityNet(args.model).to(device).to(memory_format=torch.channels_last)
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.05)
    scaler = torch.amp.GradScaler("cuda")
    steps_per_epoch = len(train_idx) // args.batch_images
    total_steps = steps_per_epoch * args.epochs
    group = torch.arange(args.batch_images * 2, device=device).repeat_interleave(2)  # crops of one damaged view

    step, history, started = 0, [], time.time()
    for epoch in range(args.epochs):
        loader = DataLoader(
            Pairs(images, train_idx, steps_per_epoch * args.batch_images, seed=epoch * 1_000_003),
            batch_size=args.batch_images, num_workers=args.workers, prefetch_factor=4, pin_memory=True,
            worker_init_fn=worker_init, drop_last=True,
        )
        running = [0.0, 0.0, 0.0]
        for crops, sev, levels in loader:
            lr = args.lr * min(1.0, (step + 1) / 300) * 0.5 * (1 + math.cos(math.pi * step / total_steps))
            for g in optimizer.param_groups:
                g["lr"] = lr
            x = crops.to(device, non_blocking=True).flatten(0, 1).contiguous(memory_format=torch.channels_last)
            sev = sev.to(device).flatten()
            levels = (levels.to(device).flatten(0, 1)) / 5.0
            with torch.autocast("cuda", dtype=torch.float16):
                z, pred_sev, pred_levels = model(x)
            l_con = nt_xent(z, group[: len(z)])
            l_sev = F.mse_loss(pred_sev.float(), sev)
            l_lvl = F.mse_loss(pred_levels.float(), levels)
            loss = l_con + 4.0 * l_sev + 2.0 * l_lvl
            optimizer.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()
            step += 1
            running = [running[0] + float(l_con), running[1] + float(l_sev), running[2] + float(l_lvl)]
        record = {
            "epoch": epoch + 1, "minutes": round((time.time() - started) / 60, 1),
            "contrastive": running[0] / steps_per_epoch, "severity_mse": running[1] / steps_per_epoch,
            "level_mse": running[2] / steps_per_epoch,
        }
        if (epoch + 1) % args.eval_every == 0 or epoch + 1 == args.epochs:
            record["holdout"] = evaluate(model, images, hold_idx, device)
            torch.save(model.state_dict(), os.path.join(args.out, "model.pt"))
        history.append(record)
        print(json.dumps(record), flush=True)
        with open(os.path.join(args.out, "history.json"), "w", encoding="utf-8") as handle:
            json.dump(history, handle, indent=1)

    model.eval().cpu()
    dummy = torch.zeros(1, 3, 384, 384)
    try:
        torch.onnx.export(
            ScoreOnly(model), dummy, os.path.join(args.out, "quality.onnx"), input_names=["image_bgr"],
            output_names=["quality"], dynamic_axes={"image_bgr": {0: "n", 2: "h", 3: "w"}}, opset_version=17,
        )
        print("exported quality.onnx", flush=True)
    except Exception as error:  # ONNX export is a convenience here; the checkpoint is already saved
        print("onnx export failed:", error, flush=True)


if __name__ == "__main__":
    main()
