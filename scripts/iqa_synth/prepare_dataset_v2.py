#!/usr/bin/env python3
"""Second training set for the self-trained quality model: the whole Photography library, balanced.

Differences from prepare_dataset.py (which only found camera JPEGs and skipped export folders):
  * Nikon NEF raw files are a source: each one's embedded full-size camera JPEG preview is used (nef_preview.py).
  * Folder quotas follow the square root of each folder's file count, so big trips count for less and small ones for more.
  * Within a folder, low-light shots (high ISO or long exposure) are guaranteed at least LOWLIGHT_SHARE of the picks when
    the folder has them, because the first model misread dark and noisy scenes as damaged.
  * Edited or exported JPEGs are included, capped at EDITED_SHARE of the set, and skipped when a raw original with the
    same file name exists (so one shot is not counted twice).
  * Filtering is light: unusable frames only (near black or white, tiny), then the softest 8% inside each brightness
    third, so a dark scene is only compared with other dark scenes.

Reads only; writes images.npy and manifest.json under --out. Nothing about the photos leaves the machine.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import random
from multiprocessing import Pool

import cv2
import numpy as np
from PIL import Image, ImageOps

import nef_preview

cv2.setNumThreads(1)
W, H = 768, 512
RAW_EXTS = (".nef",)
JPG_EXTS = (".jpg", ".jpeg")
SKIP_DIRS = (".image_triage", "thumb", "cache", "_winners")
EDITED_HINTS = ("edit", "export", "web", "instagram", "print", "processed", "final", "output")
LOWLIGHT_SHARE = 0.25
EDITED_SHARE = 0.12
IMG_MAX = Image.MAX_IMAGE_PIXELS
Image.MAX_IMAGE_PIXELS = None  # the owner's own 90 MP panoramas are legitimate


def walk(folder: str) -> list[str]:
    found = []
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if not any(s in d.lower() for s in SKIP_DIRS)]
        for name in filenames:
            if name.startswith("."):
                continue
            if name.lower().endswith(RAW_EXTS + JPG_EXTS):
                found.append(os.path.join(dirpath, name))
    return found


def is_edited_path(path: str) -> bool:
    parts = [p.lower() for p in path.split(os.sep)[:-1]]
    return any(h in part and "unedit" not in part for part in parts for h in EDITED_HINTS)


def scan_one(path: str) -> dict | None:
    """Cheap metadata pass (reads headers only for NEF)."""
    row = {"path": path, "ext": os.path.splitext(path)[1].lower(), "iso": None, "exposure_s": None, "model": ""}
    try:
        if row["ext"] in RAW_EXTS:
            with open(path, "rb"):
                pass
            info = nef_preview.read_nef_meta(path)
            if info is None:
                return None
            row.update({k: info.get(k) for k in ("iso", "exposure_s", "model")})
        else:
            with Image.open(path) as im:
                exif = im.getexif()
                sub = exif.get_ifd(0x8769) if hasattr(exif, "get_ifd") else {}
                iso = sub.get(0x8827) or exif.get(0x8827)
                exposure = sub.get(0x829A)
                row["iso"] = int(iso) if iso else None
                row["exposure_s"] = float(exposure) if exposure else None
                row["model"] = str(exif.get(0x0110, ""))
    except Exception:
        return None
    return row


def lowlight(row: dict) -> bool:
    iso, exposure = row.get("iso"), row.get("exposure_s")
    return bool((iso and iso >= 3200) or (exposure and exposure >= 1 / 15))


def load_pixels(path: str) -> tuple[np.ndarray, dict] | None:
    try:
        if path.lower().endswith(RAW_EXTS):
            info = nef_preview.read_nef(path)
            if info is None:
                return None
            im = Image.open(io.BytesIO(info["jpeg"]))
        else:
            im = Image.open(path)
        im.draft("RGB", (W * 2, H * 2))
        im = ImageOps.exif_transpose(im).convert("RGB")
        width, height = im.size
        if min(width, height) < 500:
            return None
        scale = max(W / width, H / height)
        im = im.resize((max(W, round(width * scale)), max(H, round(height * scale))), Image.LANCZOS)
        left, top = (im.width - W) // 2, (im.height - H) // 2
        bgr = cv2.cvtColor(np.asarray(im.crop((left, top, left + W, top + H))), cv2.COLOR_RGB2BGR)
    except Exception:
        return None
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    mean = float(gray.mean())
    if mean < 3 or mean > 252 or float(gray.std()) < 3:
        return None  # black, blown out, or flat: not a usable photo
    kernel = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], np.float32)
    return bgr, {
        "mean_luma": mean,
        "sharpness": float(cv2.Laplacian(gray, cv2.CV_32F).var()),
        "noise": float(np.abs(cv2.filter2D(gray, -1, kernel)).mean() * np.sqrt(np.pi / 2) / 6.0),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--total", type=int, default=14000)
    parser.add_argument("--max-per-folder", type=int, default=1800)
    parser.add_argument("--holdout", nargs="*", default=["Japan 4-24", "Iceland 10-24", "Steamboat '20"])
    parser.add_argument("--exclude", nargs="*", default=[], help="top-level folders to skip entirely")
    parser.add_argument("--drop-soft", type=float, default=0.08)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args()
    random.seed(args.seed)
    os.makedirs(args.out, exist_ok=True)

    folders = sorted(d for d in os.listdir(args.root) if os.path.isdir(os.path.join(args.root, d)) and d not in args.exclude and not d.startswith("."))
    paths_by_folder = {f: walk(os.path.join(args.root, f)) for f in folders}
    paths_by_folder = {f: p for f, p in paths_by_folder.items() if p}
    print("folders:", {f: len(p) for f, p in paths_by_folder.items()}, flush=True)

    all_paths = [p for ps in paths_by_folder.values() for p in ps]
    with Pool(args.workers) as pool:
        scanned = pool.map(scan_one, all_paths, chunksize=64)
    rows = {r["path"]: r for r in scanned if r}
    print(f"scanned {len(rows)} of {len(all_paths)} files", flush=True)

    # one shot, one source: drop a JPEG when a raw with the same stem exists in the same top-level folder
    raw_stems = {(f, os.path.splitext(os.path.basename(p))[0].lower()) for f, ps in paths_by_folder.items() for p in ps if p.lower().endswith(RAW_EXTS)}
    candidates: dict[str, list[dict]] = {}
    for folder, paths in paths_by_folder.items():
        for p in paths:
            row = rows.get(p)
            if not row:
                continue
            stem = os.path.splitext(os.path.basename(p))[0].lower()
            row["folder"] = folder
            row["edited"] = row["ext"] in JPG_EXTS and is_edited_path(p)
            if row["ext"] in JPG_EXTS and (folder, stem) in raw_stems:
                continue
            candidates.setdefault(folder, []).append(row)

    weights = {f: math.sqrt(len(ps)) for f, ps in candidates.items()}
    scale = args.total / sum(weights.values())
    chosen: list[dict] = []
    for folder, items in candidates.items():
        quota = min(args.max_per_folder, len(items), max(1, round(weights[folder] * scale)))
        dark = [r for r in items if lowlight(r)]
        edited = [r for r in items if r["edited"]]
        plain = [r for r in items if not r["edited"] and not lowlight(r)]
        random.shuffle(dark), random.shuffle(edited), random.shuffle(plain)
        take_dark = min(len(dark), max(round(quota * LOWLIGHT_SHARE), round(quota * len(dark) / len(items))))
        take_edit = min(len(edited), round(quota * EDITED_SHARE))
        pick = dark[:take_dark] + edited[:take_edit]
        pick += plain[: max(0, quota - len(pick))]
        pick += [r for r in dark[take_dark:]][: max(0, quota - len(pick))]
        chosen += pick[:quota]
        print(f"{folder}: {len(items)} candidates, quota {quota}, low-light {take_dark}, edited {min(take_edit, len(pick))}", flush=True)

    random.shuffle(chosen)
    with Pool(args.workers) as pool:
        loaded = pool.map(load_pixels, [r["path"] for r in chosen], chunksize=4)
    items = [(row, res) for row, res in zip(chosen, loaded) if res is not None]
    print(f"decoded {len(items)} of {len(chosen)}", flush=True)

    # light quality filter: drop the softest few percent within each brightness third
    lumas = np.array([m["mean_luma"] for _, (_, m) in items])
    edges = np.quantile(lumas, [1 / 3, 2 / 3])
    third = np.digitize(lumas, edges)
    keep = np.ones(len(items), bool)
    for t in range(3):
        idx = np.where(third == t)[0]
        sharp = np.array([items[i][1][1]["sharpness"] for i in idx])
        cutoff = np.quantile(sharp, args.drop_soft)
        keep[idx[sharp < cutoff]] = False
    items = [it for it, k in zip(items, keep) if k]

    images = np.lib.format.open_memmap(os.path.join(args.out, "images.npy"), mode="w+", dtype=np.uint8, shape=(len(items), H, W, 3))
    manifest = []
    for index, (row, (bgr, stats)) in enumerate(items):
        images[index] = bgr
        manifest.append({
            "index": index, "source": row["path"], "folder": row["folder"], "kind": "edited" if row["edited"] else ("raw_preview" if row["ext"] in RAW_EXTS else "jpeg"),
            "iso": row["iso"], "exposure_s": row["exposure_s"], "model": row["model"], "lowlight": lowlight(row),
            "third_party_consented": "lamont" in row["path"].lower(),
            "split": "holdout" if row["folder"] in args.holdout else "train", **stats,
        })
    images.flush()
    json.dump(manifest, open(os.path.join(args.out, "manifest.json"), "w", encoding="utf-8"))
    tr = sum(m["split"] == "train" for m in manifest)
    print(f"wrote {len(manifest)} images ({tr} train, {len(manifest) - tr} holdout; low-light {sum(m['lowlight'] for m in manifest)}, "
          f"edited {sum(m['kind'] == 'edited' for m in manifest)}, raw previews {sum(m['kind'] == 'raw_preview' for m in manifest)}) to {args.out}", flush=True)


if __name__ == "__main__":
    main()
