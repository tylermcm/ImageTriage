#!/usr/bin/env python3
"""Build the pristine-source image set for the self-trained quality model (docs/ai_quality_plan.md, route 3).

Reads camera JPEGs from folders the owner chose, never modifies them, and writes only to --out:
  images.npy     uint8 (N, 512, 768, 3) BGR, memory-mappable, every image resized and centre-cropped to 3:2
  manifest.json  per image: source path, folder, sharpness and noise measures, split (train or holdout)

The weakest images by sharpness within each folder are dropped, because the method assumes the sources are close to
pristine. Nothing about the photos leaves the machine; the manifest holds file paths only.

  python3 prepare_dataset.py --root /media/dixie/ColossalBoi/Photography --out DIR --per-folder 600 \
      --include "Alaska" "Banff 8-25" ... --holdout "Cancun" "Flatirons"
"""
from __future__ import annotations

import argparse
import json
import os
import random
from multiprocessing import Pool

import cv2
import numpy as np

cv2.setNumThreads(1)
W, H = 768, 512
SKIP_PARTS = ("edit", "export", "_winners", ".image_triage", "web", "instagram", "print", "thumb", "preview", "cache")
EXTS = (".jpg", ".jpeg")


def skipped(directory_name: str) -> bool:
    lowered = directory_name.lower()
    if "unedit" in lowered:  # 'Unedited' holds camera originals
        return False
    return any(part in lowered for part in SKIP_PARTS)


def list_jpegs(folder: str) -> list[str]:
    found = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if not skipped(d)]
        for name in filenames:
            if name.lower().endswith(EXTS) and not name.startswith("."):
                found.append(os.path.join(dirpath, name))
    return found


def load_resized(path: str) -> tuple[np.ndarray, dict] | None:
    """Decode at reduced size, honour EXIF orientation, scale so the image covers 768x512, centre-crop."""
    try:
        from PIL import Image, ImageOps

        with Image.open(path) as im:
            im.draft("RGB", (W * 2, H * 2))  # fast JPEG decode at reduced size
            im = ImageOps.exif_transpose(im).convert("RGB")
            width, height = im.size
            if min(width, height) < 700:  # too small to be a camera original
                return None
            scale = max(W / width, H / height)
            im = im.resize((max(W, round(width * scale)), max(H, round(height * scale))), Image.LANCZOS)
            left, top = (im.width - W) // 2, (im.height - H) // 2
            bgr = cv2.cvtColor(np.asarray(im.crop((left, top, left + W, top + H))), cv2.COLOR_RGB2BGR)
    except Exception:
        return None
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    sharp = float(cv2.Laplacian(gray, cv2.CV_32F).var())
    kernel = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], np.float32)
    noise = float(np.abs(cv2.filter2D(gray, -1, kernel)).mean() * np.sqrt(np.pi / 2) / 6.0)
    return bgr, {"sharpness": sharp, "noise": noise}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--include", nargs="+", required=True, help="top-level folder names under --root")
    parser.add_argument("--holdout", nargs="*", default=[], help="folders kept out of training for validation")
    parser.add_argument("--per-folder", type=int, default=600)
    parser.add_argument("--drop-weakest", type=float, default=0.2, help="fraction of lowest-sharpness images dropped per folder")
    parser.add_argument("--workers", type=int, default=40)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    random.seed(args.seed)
    os.makedirs(args.out, exist_ok=True)
    jobs: list[tuple[str, str]] = []
    for folder in args.include:
        files = list_jpegs(os.path.join(args.root, folder))
        random.shuffle(files)
        # oversample so that dropping the weakest still leaves about --per-folder
        take = int(args.per_folder / max(0.1, 1.0 - args.drop_weakest)) if files else 0
        jobs += [(folder, path) for path in files[:take]]
        print(f"{folder}: {len(files)} jpegs found, sampling {min(len(files), take)}", flush=True)

    with Pool(args.workers) as pool:
        results = pool.map(load_resized_job, [path for _, path in jobs], chunksize=8)

    by_folder: dict[str, list[tuple[str, np.ndarray, dict]]] = {}
    for (folder, path), result in zip(jobs, results):
        if result is not None:
            by_folder.setdefault(folder, []).append((path, result[0], result[1]))

    kept = []
    for folder, items in by_folder.items():
        items.sort(key=lambda item: item[2]["sharpness"])
        items = items[int(len(items) * args.drop_weakest):][: args.per_folder]
        kept += [(folder, *item) for item in items]
        print(f"{folder}: kept {len(items)}", flush=True)

    images = np.lib.format.open_memmap(os.path.join(args.out, "images.npy"), mode="w+", dtype=np.uint8, shape=(len(kept), H, W, 3))
    manifest = []
    for index, (folder, path, bgr, stats) in enumerate(kept):
        images[index] = bgr
        manifest.append({"index": index, "source": path, "folder": folder, "split": "holdout" if folder in args.holdout else "train", **stats})
    images.flush()
    with open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle)
    train = sum(1 for m in manifest if m["split"] == "train")
    print(f"wrote {len(manifest)} images ({train} train, {len(manifest) - train} holdout) to {args.out}")


def load_resized_job(path: str):
    return load_resized(path)


if __name__ == "__main__":
    main()
