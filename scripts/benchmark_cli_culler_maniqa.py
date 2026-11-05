"""Measure unchanged MANIQA inference in a selected isolated culler runtime."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time


def benchmark(manifest, image_path, device, threads, repeats):
    import aiculler
    import torch
    from PIL import Image, ImageOps

    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("The selected runtime cannot use CUDA")
    if threads:
        torch.set_num_threads(threads)
    sys.path.insert(0, manifest["app_path"])
    registry = aiculler.ModelRegistry.from_app_manifest(manifest["app_path"], device=device)
    started = time.perf_counter()
    if image_path:
        with Image.open(image_path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            image.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
    else:
        image = Image.new("RGB", (1280, 854), (71, 119, 151))
    decode = time.perf_counter() - started
    started = time.perf_counter()
    engine = registry.engine("maniqa")
    load = time.perf_counter() - started
    timings, scores = [], []
    try:
        for _ in range(repeats + 1):
            if engine.device.startswith("cuda"):
                torch.cuda.synchronize()
            started = time.perf_counter()
            result = engine.assess(image)
            if engine.device.startswith("cuda"):
                torch.cuda.synchronize()
            timings.append(time.perf_counter() - started)
            scores.append(result["score"])
    finally:
        registry.close()
    return {"torch": torch.__version__, "device": engine.device, "threads": torch.get_num_threads(),
            "gpu": torch.cuda.get_device_name(0) if engine.device.startswith("cuda") else None,
            "input": str(image_path) if image_path else "synthetic solid image", "size": list(image.size),
            "crops": len(result["crops"]), "decode_seconds": decode, "load_seconds": load,
            "cold_seconds": timings[0], "warm_seconds": timings[1:],
            "warm_median_seconds": statistics.median(timings[1:]), "scores": scores}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--device", choices=("cpu", "cuda"), required=True)
    parser.add_argument("--threads", type=int, default=0)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.repeats < 1 or args.threads < 0:
        parser.error("repeats must be positive and threads must be nonnegative")
    manifest = json.loads(args.runtime.read_text(encoding="utf-8"))
    for key in ("python", "worker", "app_path"):
        value = Path(manifest[key])
        manifest[key] = str(value if value.is_absolute() else args.runtime.resolve().parent / value)
    if not args.worker:
        return subprocess.run([manifest["python"], "-I", str(Path(__file__).resolve()), "--worker", *sys.argv[1:]], check=False).returncode
    report = benchmark(manifest, args.image, args.device, args.threads, args.repeats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
