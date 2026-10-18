#!/usr/bin/env python3
"""Sanity-check a trained quality model on real held-out photos (none were used for training).

Writes under --out:
  summary.json        training history tail, and the model's agreement with measured sharpness and noise
  contact_sheet.jpg   the 12 lowest-scored and 12 highest-scored holdout photos, for eyeballing
  scores.json         per-image score (1 = pristine) with source paths

There are no human labels here: agreement with sharpness and noise is a plausibility check, not proof that the score
matches your culling choices. Those need your own keep/reject labels (docs/ai_quality_plan.md).
"""
from __future__ import annotations

import argparse
import json
import os

import cv2
import numpy as np
import torch

from train import QualityNet, spearman


@torch.no_grad()
def score_image(model: QualityNet, bgr: np.ndarray, device) -> tuple[float, float]:
    """Return (mean over six 224 px tiles, whole 512x768 image) quality, both 1 = pristine."""
    tiles = []
    for y in (0, 288):
        for x in (0, 272, 544):
            tiles.append(np.ascontiguousarray(bgr[y : y + 224, x : x + 224]))
    batch = torch.from_numpy(np.stack(tiles)).permute(0, 3, 1, 2).to(device)
    with torch.autocast("cuda", dtype=torch.float16):
        tile_quality = 1.0 - model(batch)[1].float()
        whole = 1.0 - model(torch.from_numpy(bgr).permute(2, 0, 1)[None].to(device))[1].float()
    return float(tile_quality.mean()), float(whole[0])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--run", required=True, help="run directory containing model.pt and history.json")
    parser.add_argument("--model", default="mobilenet_v3_large")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    images = np.load(os.path.join(args.data, "images.npy"), mmap_mode="r")
    manifest = json.load(open(os.path.join(args.data, "manifest.json"), encoding="utf-8"))
    holdout = [m for m in manifest if m["split"] == "holdout"] or manifest[-100:]
    device = torch.device("cuda")
    model = QualityNet(args.model).to(device)
    model.load_state_dict(torch.load(os.path.join(args.run, "model.pt"), map_location=device))
    model.eval()

    rows = []
    for item in holdout:
        tile, whole = score_image(model, np.asarray(images[item["index"]]), device)
        rows.append({"index": item["index"], "source": item["source"], "folder": item["folder"],
                     "tile_quality": tile, "whole_quality": whole,
                     "sharpness": item["sharpness"], "noise": item["noise"], "iso": item.get("iso"),
                     "exposure_s": item.get("exposure_s"), "lowlight": item.get("lowlight"), "kind": item.get("kind")})
    tile_q = [r["tile_quality"] for r in rows]
    log_sharp = [float(np.log1p(r["sharpness"])) for r in rows]
    noise = [r["noise"] for r in rows]
    history = json.load(open(os.path.join(args.run, "history.json"), encoding="utf-8"))
    summary = {
        "holdout_images": len(rows),
        "spearman_quality_vs_sharpness": spearman(tile_q, log_sharp),
        "spearman_quality_vs_noise": spearman(tile_q, noise),
        "spearman_tile_vs_whole": spearman(tile_q, [r["whole_quality"] for r in rows]),
        "quality_min_max_mean": [float(np.min(tile_q)), float(np.max(tile_q)), float(np.mean(tile_q))],
        "last_history": history[-3:],
    }
    def mean_quality(selector) -> dict:
        chosen = [r["tile_quality"] for r in rows if selector(r)]
        return {"n": len(chosen), "mean_quality": float(np.mean(chosen)) if chosen else None}

    summary["by_condition"] = {
        "iso_le_400": mean_quality(lambda r: r["iso"] is not None and r["iso"] <= 400),
        "iso_401_3200": mean_quality(lambda r: r["iso"] is not None and 400 < r["iso"] < 3200),
        "iso_ge_3200": mean_quality(lambda r: r["iso"] is not None and r["iso"] >= 3200),
        "exposure_ge_1_15s": mean_quality(lambda r: r["exposure_s"] is not None and r["exposure_s"] >= 1 / 15),
        "lowlight_any": mean_quality(lambda r: bool(r["lowlight"])),
        "kind_edited": mean_quality(lambda r: r["kind"] == "edited"),
    }
    summary["by_folder"] = {f: mean_quality(lambda r, f=f: r["folder"] == f) for f in sorted({r["folder"] for r in rows})}
    json.dump(summary, open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8"), indent=1)
    json.dump(rows, open(os.path.join(args.out, "scores.json"), "w", encoding="utf-8"))

    order = np.argsort(tile_q)
    low = [int(i) for i in order[:12]]
    pick = [(i, "LOW") for i in low] + [(int(i), "HIGH") for i in order[-12:] if int(i) not in low]
    cells = []
    for i, label in pick:
        thumb = cv2.resize(np.asarray(images[rows[i]["index"]]), (256, 171), interpolation=cv2.INTER_AREA).copy()
        cv2.putText(thumb, f"{label} {rows[i]['tile_quality']:.2f}", (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        cells.append(thumb)
    while len(cells) % 6:
        cells.append(np.zeros_like(cells[0]))
    sheet = np.vstack([np.hstack(cells[r * 6 : r * 6 + 6]) for r in range(len(cells) // 6)])
    cv2.imwrite(os.path.join(args.out, "contact_sheet.jpg"), sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
