#!/usr/bin/env python3
"""Round 2 of the grouping spike: wider albums, a second photographer, a stronger classical feature set, more DINOv2 variants.

Round 1 asked how well time/EXIF/pHash and DINOv2 each tell that two neighbouring frames are the same moment.

Step 1 (this script, run on the server): for every NEF in the chosen albums read the embedded camera JPEG preview and
EXIF (capture time to 1/100 s, ISO, shutter, aperture, focal length, exposure bias and mode), compute a 64-bit pHash
(plain and brightness-normalised) and a DINOv2 embedding (CLS and mean-patch), write review thumbnails, and time DINOv2
on CPU alone. Nothing leaves the machine except what you copy.

Reads photos only; writes under --out.

  python3 grouping_spike_extract.py --root /media/dixie/ColossalBoi/Photography --out spike1 \
      --folders "Japan 4-24" "Iceland 10-24" "WhiteSands '26" "Engagement Trip/Canada 10-25"
"""
from __future__ import annotations

import argparse
import io
import json
import os
import time
from multiprocessing import Pool

import cv2
import numpy as np
from PIL import Image, ImageOps

import nef_preview

cv2.setNumThreads(1)
Image.MAX_IMAGE_PIXELS = None
SKIP = ("winners", "edit", "export", ".image_triage", "thumb", "cache", "web", "print")
DINO_REPO, DINO_FILE = "Skulleton12/DinoV2", "model.safetensors"
DINO_SIZE = (224, 336)  # height, width: 16 x 24 patches of 14 px, keeps the 3:2 shape
DINO_SIZE_HI = (336, 504)  # 24 x 36 patches


def list_nefs(root: str, folder: str) -> list[str]:
    found = []
    for dirpath, dirnames, filenames in os.walk(os.path.join(root, folder)):
        dirnames[:] = [d for d in dirnames if "unedit" in d.lower() or not any(s in d.lower() for s in SKIP)]
        for name in filenames:
            if name.lower().endswith(".nef") and not name.startswith("."):
                found.append(os.path.join(dirpath, name))
    return sorted(found)


def phash64(gray32: np.ndarray) -> int:
    dct = cv2.dct(gray32.astype(np.float32))
    low = dct[:8, :8].flatten()
    bits = low[1:] > np.median(low[1:])  # skip the DC term
    value = 0
    for b in np.concatenate([[False], bits]):
        value = (value << 1) | int(b)
    return value


def timestamp(info: dict) -> float | None:
    text = (info.get("datetime") or "").strip()
    if len(text) < 19:
        return None
    try:
        from datetime import datetime

        base = datetime.strptime(text[:19], "%Y:%m:%d %H:%M:%S").timestamp()
    except ValueError:
        return None
    sub = (info.get("subsec") or "").strip()
    return base + (float("0." + sub) if sub.isdigit() else 0.0)


def process(job: tuple[int, str, str]) -> dict | None:
    index, path, thumb_dir = job
    try:
        info = nef_preview.read_nef(path)
        if info is None:
            return None
        im = Image.open(io.BytesIO(info["jpeg"]))
        im.draft("RGB", (1100, 760))
        im = ImageOps.exif_transpose(im).convert("RGB")
        rgb = np.asarray(im.resize((DINO_SIZE[1], DINO_SIZE[0]), Image.LANCZOS))
        rgb_hi = np.asarray(im.resize((DINO_SIZE_HI[1], DINO_SIZE_HI[0]), Image.LANCZOS))
        thumb = im.resize((384, int(384 * im.height / im.width)), Image.LANCZOS)
        thumb.save(os.path.join(thumb_dir, f"{index}.jpg"), quality=82)
        gray = cv2.cvtColor(np.asarray(im.resize((256, 170), Image.AREA if hasattr(Image, "AREA") else Image.BILINEAR)), cv2.COLOR_RGB2GRAY).astype(np.float32)
        small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
        normalised = small / max(float(small.mean()), 1.0) * 128.0
        g9 = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
        dbits = (g9[:, 1:] > g9[:, :-1]).flatten()
        dhash = 0
        for bit in dbits:
            dhash = (dhash << 1) | int(bit)
        small64 = cv2.resize(gray, (64, 43), interpolation=cv2.INTER_AREA).astype(np.uint8)
        hsv = cv2.cvtColor(np.asarray(im.resize((96, 64), Image.BILINEAR)), cv2.COLOR_RGB2HSV)
        hist = cv2.calcHist([hsv], [0, 1, 2], None, [8, 4, 4], [0, 180, 0, 256, 0, 256]).flatten()
        hist = hist / max(hist.sum(), 1.0)
        return {
            "index": index, "path": path, "ts": timestamp(info), "iso": info["iso"], "exposure_s": info["exposure_s"],
            "fnumber": info["fnumber"], "focal": info["focal_length"], "bias": info["exposure_bias"], "mode": info["exposure_mode"],
            "model": info["model"], "phash": phash64(small), "phash_norm": phash64(normalised), "dhash": dhash, "hist": hist.astype(np.float32), "gray64": small64, "rgb": rgb, "rgb_hi": rgb_hi,
            "photographer": "lamont" if "lamont" in path.lower() else "tyler",
            "portrait": im.height > im.width,
        }
    except Exception:
        return None


def build_dino(out_dir: str, device: str):
    """The repo is in transformers format and the server's transformers cannot load it, so map the weights into timm."""
    import timm
    import torch
    from huggingface_hub import hf_hub_download
    from safetensors.torch import load_file

    weights = hf_hub_download(DINO_REPO, DINO_FILE, local_dir=os.path.join(out_dir, "dino"))
    source = {k.removeprefix("dinov2."): v for k, v in load_file(weights).items()}
    model = timm.create_model("vit_base_patch14_dinov2", pretrained=False, num_classes=0, dynamic_img_size=True)
    mapped = {
        "cls_token": source["embeddings.cls_token"], "pos_embed": source["embeddings.position_embeddings"],
        "patch_embed.proj.weight": source["embeddings.patch_embeddings.projection.weight"],
        "patch_embed.proj.bias": source["embeddings.patch_embeddings.projection.bias"],
        "norm.weight": source["layernorm.weight"], "norm.bias": source["layernorm.bias"],
    }
    for i in range(len(model.blocks)):
        e, b = f"encoder.layer.{i}.", f"blocks.{i}."
        for kind in ("weight", "bias"):
            mapped[b + f"attn.qkv.{kind}"] = torch.cat([source[e + f"attention.attention.{n}.{kind}"] for n in ("query", "key", "value")], 0)
            mapped[b + f"attn.proj.{kind}"] = source[e + f"attention.output.dense.{kind}"]
            for src, dst in (("norm1", "norm1"), ("norm2", "norm2"), ("mlp.fc1", "mlp.fc1"), ("mlp.fc2", "mlp.fc2")):
                mapped[b + f"{dst}.{kind}"] = source[e + f"{src}.{kind}"]
        mapped[b + "ls1.gamma"] = source[e + "layer_scale1.lambda1"]
        mapped[b + "ls2.gamma"] = source[e + "layer_scale2.lambda1"]
    missing, unexpected = model.load_state_dict(mapped, strict=False)
    if missing or unexpected:
        raise RuntimeError(f"DINOv2 weight mapping incomplete: missing={missing[:5]} unexpected={unexpected[:5]}")
    return model.to(device).eval()


def embed(model, rgb_batch: np.ndarray, device: str, half: bool):
    import torch

    mean = torch.tensor([0.485, 0.456, 0.406], device=device).view(1, 3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225], device=device).view(1, 3, 1, 1)
    x = torch.from_numpy(rgb_batch).to(device).permute(0, 3, 1, 2).float().div_(255)
    x = (x - mean) / std
    with torch.no_grad(), torch.autocast(device, dtype=torch.float16, enabled=half):
        tokens = model.forward_features(x)
    cls = tokens[:, 0].float()
    patch_mean = tokens[:, 1:].float().mean(1)
    return cls.cpu().numpy(), patch_mean.cpu().numpy()


def main() -> None:
    import torch

    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--folders", nargs="+", required=True)
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument("--limit", type=int, default=0, help="per folder, for a quick test")
    args = parser.parse_args()
    thumb_dir = os.path.join(args.out, "thumbs")
    os.makedirs(thumb_dir, exist_ok=True)

    paths: list[tuple[str, str]] = []
    for folder in args.folders:
        files = list_nefs(args.root, folder)
        files = files[: args.limit] if args.limit else files
        print(f"{folder}: {len(files)} NEF", flush=True)
        paths += [(folder, p) for p in files]
    started = time.time()
    with Pool(args.workers) as pool:
        rows = pool.map(process, [(i, p, thumb_dir) for i, (_, p) in enumerate(paths)], chunksize=4)
    folder_of = {p: f for f, p in paths}
    rows = [r for r in rows if r is not None and r["ts"] is not None]
    # the same file can sit in two sub-folders (copies); keep one per (album, file name, capture time)
    seen, unique = set(), []
    for r in rows:
        key = (folder_of[r["path"]], os.path.basename(r["path"]).lower(), r["ts"])
        if key not in seen:
            seen.add(key)
            unique.append(r)
    print(f"dropped {len(rows) - len(unique)} duplicate copies", flush=True)
    rows = unique
    for r in rows:
        r["stream"] = f"{folder_of[r['path']]}|{r['photographer']}|{r['model']}"
    rows.sort(key=lambda r: (r["stream"], r["ts"], r["path"]))
    print(f"read {len(rows)} photos in {time.time() - started:.0f}s", flush=True)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = build_dino(args.out, device)
    rgb = np.stack([r["rgb"] for r in rows])
    rgb_hi = np.stack([r["rgb_hi"] for r in rows])
    cls_all, mean_all, cls_hi_all, mean_hi_all = [], [], [], []
    t0 = time.time()
    for s in range(0, len(rows), 48):
        c, m = embed(model, rgb[s : s + 48], device, half=(device == "cuda"))
        cls_all.append(c), mean_all.append(m)
        c2, m2 = embed(model, rgb_hi[s : s + 48], device, half=(device == "cuda"))
        cls_hi_all.append(c2), mean_hi_all.append(m2)
    gpu_seconds = time.time() - t0
    print(f"DINOv2 on {device}: {gpu_seconds:.1f}s for {len(rows)} photos (both resolutions)", flush=True)

    timings = {}
    cpu_model = None
    np.savez(
        os.path.join(args.out, "features.npz"),
        index=np.array([r["index"] for r in rows]), path=np.array([r["path"] for r in rows]),
        folder=np.array([folder_of[r["path"]] for r in rows]), ts=np.array([r["ts"] for r in rows]),
        iso=np.array([r["iso"] or 0 for r in rows], float), exposure_s=np.array([r["exposure_s"] or 0 for r in rows], float),
        fnumber=np.array([r["fnumber"] or 0 for r in rows], float), focal=np.array([r["focal"] or 0 for r in rows], float),
        bias=np.array([r["bias"] if r["bias"] is not None else np.nan for r in rows], float),
        mode=np.array([r["mode"] if r["mode"] is not None else -1 for r in rows]),
        model=np.array([r["model"] for r in rows]), portrait=np.array([r["portrait"] for r in rows]),
        phash=np.array([r["phash"] for r in rows], dtype=np.uint64), phash_norm=np.array([r["phash_norm"] for r in rows], dtype=np.uint64),
        dino_cls=np.concatenate(cls_all), dino_mean=np.concatenate(mean_all),
        dino_cls_hi=np.concatenate(cls_hi_all), dino_mean_hi=np.concatenate(mean_hi_all),
        dhash=np.array([r["dhash"] for r in rows], dtype=np.uint64), hist=np.stack([r["hist"] for r in rows]),
        gray64=np.stack([r["gray64"] for r in rows]), photographer=np.array([r["photographer"] for r in rows]),
        stream=np.array([r["stream"] for r in rows]),
    )
    with open(os.path.join(args.out, "timings.json"), "w", encoding="utf-8") as handle:
        json.dump({"photos": len(rows), "gpu_seconds": gpu_seconds, "cpu_ms_per_photo": timings, "cpu": os.popen("lscpu | grep 'Model name'").read().strip()}, handle, indent=1)
    print("wrote", args.out, flush=True)


if __name__ == "__main__":
    main()
