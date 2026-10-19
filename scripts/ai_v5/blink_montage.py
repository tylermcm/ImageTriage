#!/usr/bin/env python3
"""Face crops for the highest blink scores, so the threshold can be judged by eye.

  python blink_montage.py --model-dir blink --scores blink_scores.json --root iqa_synth --top 24 --out blink_montage.jpg
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from PIL import Image, ImageDraw

from image_triage.cascade.blink import face_area
from image_triage.cascade.rankers.worker import BlinkEngine, load_image


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--scores", required=True)
    ap.add_argument("--root", required=True, help="the folder that contains burst_picks/ and unrated_bursts/")
    ap.add_argument("--top", type=int, default=24)
    ap.add_argument("--out", required=True)
    ap.add_argument("--min", type=float, default=0.0)
    args = ap.parse_args()
    rows = sorted(json.load(open(args.scores)), key=lambda r: -r["score"])
    rows = [r for r in rows if r["score"] > args.min][: args.top]
    engine = BlinkEngine(Path(args.model_dir), "cpu")
    tiles = []
    for row in rows:
        image = load_image(os.path.join(args.root, row["jpg"]), 1280)
        import numpy as np

        result = engine.landmarker.detect(engine.mp.Image(image_format=engine.mp.ImageFormat.SRGB, data=np.ascontiguousarray(np.asarray(image))))
        best = None
        for landmarks in result.face_landmarks or []:
            area = face_area((p.x, p.y) for p in landmarks)
            if best is None or area > best[0]:
                best = (area, landmarks)
        if best is None:
            continue
        xs = [p.x for p in best[1]]
        ys = [p.y for p in best[1]]
        w, h = image.size
        x0, x1, y0, y1 = min(xs) * w, max(xs) * w, min(ys) * h, max(ys) * h
        margin = 0.25 * max(x1 - x0, y1 - y0)
        crop = image.crop((max(0, x0 - margin), max(0, y0 - margin), min(w, x1 + margin), min(h, y1 + margin)))
        crop.thumbnail((300, 300))
        tile = Image.new("RGB", (300, 330), (20, 20, 20))
        tile.paste(crop, ((300 - crop.width) // 2, 0))
        ImageDraw.Draw(tile).text((6, 308), f"{row['score']:.2f}  {row['burst']}/{row['index']}  {os.path.basename(row['jpg'])}", fill=(255, 255, 255))
        tiles.append(tile)
    columns = 6
    rows_n = (len(tiles) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * 300, rows_n * 330), (0, 0, 0))
    for i, tile in enumerate(tiles):
        sheet.paste(tile, ((i % columns) * 300, (i // columns) * 330))
    sheet.save(args.out, quality=88)
    engine.close()
    print(len(tiles), "faces ->", args.out)


if __name__ == "__main__":
    main()
