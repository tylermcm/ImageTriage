"""Compare a QML-shell screenshot with the HTML mockup render: side by side, 50% blend, amplified difference, and a block score.

    python scripts/qml_parity.py ref.png qml.png out_prefix [--crop x,y,w,h] [--zoom 2]
Writes <out>_side.png, <out>_blend.png, <out>_diff.png and prints the mean absolute difference per grid block (worst first).
"""
from __future__ import annotations

import argparse

from PIL import Image, ImageChops


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ref")
    parser.add_argument("qml")
    parser.add_argument("out")
    parser.add_argument("--crop", default="")
    parser.add_argument("--zoom", type=int, default=1)
    parser.add_argument("--blocks", type=int, default=6, help="worst blocks to list")
    args = parser.parse_args()
    a = Image.open(args.ref).convert("RGB")
    b = Image.open(args.qml).convert("RGB").resize(a.size) if Image.open(args.qml).size != a.size else Image.open(args.qml).convert("RGB")
    if args.crop:
        x, y, w, h = (int(v) for v in args.crop.split(","))
        a, b = a.crop((x, y, x + w, y + h)), b.crop((x, y, x + w, y + h))
    if args.zoom > 1:
        size = (a.width * args.zoom, a.height * args.zoom)
        a, b = a.resize(size, Image.NEAREST), b.resize(size, Image.NEAREST)
    side = Image.new("RGB", (a.width * 2 + 8, a.height), (255, 0, 255))
    side.paste(a, (0, 0))
    side.paste(b, (a.width + 8, 0))
    side.save(f"{args.out}_side.png")
    Image.blend(a, b, 0.5).save(f"{args.out}_blend.png")
    diff = ImageChops.difference(a, b)
    diff.point(lambda v: min(255, v * 6)).save(f"{args.out}_diff.png")
    grey = diff.convert("L")
    step = 40 * args.zoom
    scores = []
    for y in range(0, a.height, step):
        for x in range(0, a.width, step):
            block = grey.crop((x, y, min(x + step, a.width), min(y + step, a.height)))
            hist = block.histogram()
            mean = sum(i * c for i, c in enumerate(hist)) / max(1, block.width * block.height)
            scores.append((mean, x // args.zoom, y // args.zoom))
    total = sum(m for m, _, _ in scores) / len(scores)
    print(f"mean abs diff {total:.2f}/255")
    for mean, x, y in sorted(scores, reverse=True)[: args.blocks]:
        print(f"  block at {x},{y}: {mean:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
