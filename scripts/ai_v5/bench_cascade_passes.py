#!/usr/bin/env python3
"""Per-pass timing and load for the cascade, on this machine (CPU and GPU), over a fixed sample of frames.

Passes timed separately, per frame: NEF preview extraction, pass-1 measures + pHash, face/blink (Facet's permissive YuNet analyzer),
Q-ReAlign aesthetic (Facet), MANIQA. Run Facet parts from the Facet directory and MANIQA from the MANIQA directory (both packages are named "models").

  facet:   cd facet && PYTHONPATH=. venv/bin/python ../iqa_synth/bench_cascade_passes.py facet --bursts ../iqa_synth/unrated_bursts --n 24
  maniqa:  cd maniqa && PYTHONPATH=. ../facet/venv/bin/python ../iqa_synth/bench_cascade_passes.py maniqa --bursts ../iqa_synth/unrated_bursts --n 24 --ckpt ckpt_koniq10k.pt
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import time

import cv2
import numpy as np


def gpu_sample() -> tuple[float, float]:
    out = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used", "--format=csv,noheader,nounits"], capture_output=True, text=True).stdout
    u, m = out.strip().split(",")
    return float(u), float(m)


class Timer:
    """Wall time, process CPU time (all threads) and peak GPU utilisation while a block runs."""

    def __init__(self, label: str, frames: int) -> None:
        self.label, self.frames = label, frames

    def __enter__(self):
        self.w, self.c = time.perf_counter(), time.process_time()
        self.peak = 0.0
        return self

    def sample(self) -> None:
        try:
            self.peak = max(self.peak, gpu_sample()[0])
        except Exception:
            pass

    def __exit__(self, *exc):
        wall, cpu = time.perf_counter() - self.w, time.process_time() - self.c
        print(f"{self.label:44s} {1000 * wall / self.frames:8.0f} ms/frame   CPU busy {cpu / wall:5.1f} cores   GPU util peak {self.peak:3.0f}%", flush=True)


def load_sample(bursts_dir: str, n: int):
    bursts = json.load(open(os.path.join(bursts_dir, "bursts.json")))
    root = os.path.dirname(os.path.abspath(bursts_dir))
    frames, paths = [], []
    for b in bursts:
        for jpg, nef in zip(b["frames"], b["paths"]):
            frames.append(os.path.join(root, jpg)); paths.append(nef)
    step = max(1, len(frames) // n)
    return frames[::step][:n], paths[::step][:n]


def bench_facet(args) -> None:
    import torch
    from PIL import Image
    jpgs, nefs = load_sample(args.bursts, args.n)
    n = len(jpgs)
    print(f"{n} frames; CPU {os.cpu_count()} threads", flush=True)

    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "iqa_synth"))
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "iqa_synth"))
    try:
        import nef_preview
        with Timer("NEF preview extract + decode + resize 1280", n):
            for p in nefs:
                info = nef_preview.read_nef(p)
                im = Image.open(io.BytesIO(info["jpeg"])); im.draft("RGB", (2560, 2560)); im = im.convert("RGB")
                s = 1280 / max(im.size); im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
    except Exception as e:
        print("NEF preview timing skipped:", e)

    imgs = [cv2.imread(p) for p in jpgs]
    with Timer("pass 1 measures (Laplacian, clip, extremes)", n):
        for bgr in imgs:
            g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
            h, w = g.shape
            cv2.Laplacian(g[h // 4:3 * h // 4, w // 4:3 * w // 4], cv2.CV_32F).var(); (bgr.max(axis=2) >= 250).mean(); ((g <= 5) | (g >= 250)).mean()
    with Timer("pHash", n):
        for bgr in imgs:
            g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            low = cv2.dct(cv2.resize(g, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32))[:8, :8].flatten()[1:]; low > np.median(low)

    from analyzers.face_permissive import PermissiveFaceAnalyzer
    for dev in ("cpu",):
        fa = PermissiveFaceAnalyzer(device=dev)
        if hasattr(fa, "ensure_models"):
            fa.ensure_models()
        fa.analyze_faces(imgs[0])
        with Timer(f"faces + blink (YuNet analyzer, {dev})", n):
            for bgr in imgs:
                fa.analyze_faces(bgr)

    from models.qrealign_native import QReAlignNativeScorer
    for dev, dtype in (("cuda", "float16"), ("cpu", "float32")):
        sc = QReAlignNativeScorer(size="mini", device=dev, dtype=dtype).load()
        pils = [Image.fromarray(cv2.cvtColor(b, cv2.COLOR_BGR2RGB)) for b in imgs]
        k = n if dev == "cuda" else min(n, 8)
        sc.score(pils[0], "aesthetic")
        with Timer(f"Q-ReAlign Mini aesthetic ({dev} {dtype}, {k} frames)", k) as t:
            for pil in pils[:k]:
                sc.score(pil, "aesthetic"); t.sample()
        if dev == "cuda":
            print(f"   GPU memory peak {torch.cuda.max_memory_allocated() / 1e9:.1f} GB")
        sc.unload()


def bench_maniqa(args) -> None:
    import torch
    from models.maniqa import MANIQA
    jpgs, _ = load_sample(args.bursts, args.n)
    n = len(jpgs)
    imgs = [cv2.cvtColor(cv2.imread(p), cv2.COLOR_BGR2RGB).astype("float32") / 255 for p in jpgs]
    net = MANIQA(embed_dim=768, num_outputs=1, dim_mlp=768, patch_size=8, img_size=224, window_size=4, depths=[2, 2], num_heads=[4, 4], num_tab=2, scale=0.8)
    net.load_state_dict(torch.load(args.ckpt, map_location="cpu"), strict=False)

    def run(device, crops, frames):
        model = net.to(device).eval()
        rng = np.random.default_rng(20)
        with torch.no_grad():
            for img in frames:
                h, w = img.shape[:2]
                patches = np.stack([img[t:t + 224, l:l + 224] for t, l in ((int(rng.integers(0, h - 224)), int(rng.integers(0, w - 224))) for _ in range(crops))])
                model(((torch.from_numpy(patches).permute(0, 3, 1, 2) - 0.5) / 0.5).to(device))

    run("cuda", 20, imgs[:1])
    with Timer("MANIQA 20 crops (cuda fp32)", n) as t:
        run("cuda", 20, imgs); t.sample()
    print(f"   GPU memory peak {torch.cuda.max_memory_allocated() / 1e9:.1f} GB")
    for threads in (8, 32):
        torch.set_num_threads(threads)
        for crops in (20, 5):
            k = min(n, 6)
            with Timer(f"MANIQA {crops} crops (cpu, {threads} threads, {k} frames)", k):
                run("cpu", crops, imgs[:k])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("which", choices=["facet", "maniqa"])
    ap.add_argument("--bursts", required=True)
    ap.add_argument("--n", type=int, default=24)
    ap.add_argument("--ckpt", default="")
    a = ap.parse_args()
    bench_facet(a) if a.which == "facet" else bench_maniqa(a)
