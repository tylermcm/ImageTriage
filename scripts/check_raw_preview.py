"""Read-only real-RAW timings for the persistent PhotoCraft preview worker."""
import argparse
import json
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from image_triage.qml_shell.raw_preview import Connection


CASES = {
    "Exposure": {"exposure": .6}, "Contrast": {"contrast": 25},
    "Highlights": {"highlights": -30}, "Shadows": {"shadows": 40},
    "Whites": {"whites": 25}, "Blacks": {"blacks": -25},
    "White balance": {"temperature": 15, "tint": 10}, "Vibrance": {"vibrance": 25},
    "Saturation": {"saturation": 25}, "Parametric curve": {"curveLights": 25},
    "Point curve": {"pointCurve": [[0, 0], [128, 160], [255, 255]]},
    "Mixer": {"hslHue": [25, 0, 0, 0, 0, 0, 0, 0]},
    "Grading": {"gradeMidtones": {"hue": 40, "sat": 25, "lum": 5}},
    "Texture": {"texture": 30}, "Clarity": {"clarity": 30}, "Dehaze": {"dehaze": 20},
    "Vignette": {"vignetteAmount": -30}, "Grain": {"grainAmount": 25},
    "Glow": {"glow": {"amount": 25, "style": "diffusion", "range": 50, "spread": 50, "warmth": 0}},
    "Sharpening": {"sharpenAmount": 60}, "Luminance noise": {"noiseLuminance": 25},
    "Color noise": {"noiseColor": 25}, "Calibration": {"calibHue": [25, 0, 0], "calibShadowTint": 15},
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path)
    parser.add_argument("--worker", required=True)
    parser.add_argument("--quality", choices=["draft", "settled"], default="settled")
    parser.add_argument("--backend", choices=["cpu", "auto"], default="cpu")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    connection = Connection(args.worker)
    results = {}
    try:
        header, original = connection.call({"op": "open", "path": str(args.raw.resolve()), "edge": 1800,
            "quality": args.quality, "revision": 0, "backend": args.backend,
            "recipe": {"version": 1, "cameraRaw": {}}})
        for name, settings in CASES.items():
            elapsed = []
            for revision in range(3):
                start = time.perf_counter()
                header, image = connection.call({"op": "render", "revision": revision,
                    "quality": args.quality, "backend": args.backend,
                    "recipe": {"version": 1, "cameraRaw": settings}})
                elapsed.append((time.perf_counter()-start)*1000)
                if header["opens"] != 1 or image.isNull():
                    raise RuntimeError("Preview did not reuse its open sensor.")
            results[name] = {"newRecipeMs": round(elapsed[0], 1),
                "cachedMedianMs": round(statistics.median(elapsed[1:]), 1),
                "backend": header["backend"], "size": [image.width(), image.height()]}
            print(name, results[name], flush=True)
    finally:
        connection.close()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, indent=2), encoding="utf8")


if __name__ == "__main__":
    main()
