"""Generate and analyze Photoshop Light-control calibration renders.

This is a research harness, not part of the shipping editor. It creates one
lossless target, renders the current Image Triage implementation at controlled
settings, and compares those files with manually exported Photoshop results.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cli_editor.photo_terminal.adjustments import EditRecipe  # noqa: E402


TARGET_WIDTH = 2048
TARGET_HEIGHT = 1536
GRAY_RAMP_BOX = (0, 0, 2048, 256)
COLOR_RAMP_BOX = (0, 512, 2048, 768)

CONTROL_VALUES: dict[str, tuple[float, ...]] = {
    "exposure": (-5.0, -2.0, 2.0, 5.0),
    "contrast": (-100.0, -50.0, 50.0, 100.0),
    "highlights": (-100.0, -50.0, 50.0, 100.0),
    "shadows": (-100.0, -50.0, 50.0, 100.0),
    "whites": (-100.0, -50.0, 50.0, 100.0),
    "blacks": (-100.0, -50.0, 50.0, 100.0),
}


@dataclass(frozen=True)
class CalibrationSetting:
    control: str
    value: float

    @property
    def token(self) -> str:
        magnitude = f"{abs(self.value):g}".replace(".", "p")
        sign = "m" if self.value < 0 else "p"
        return f"{self.control}_{sign}{magnitude}"

    @property
    def filename(self) -> str:
        return f"{self.token}.png"


def calibration_settings() -> tuple[CalibrationSetting, ...]:
    return tuple(
        CalibrationSetting(control, value)
        for control, values in CONTROL_VALUES.items()
        for value in values
    )


def _level_ramp() -> np.ndarray:
    return np.repeat(np.arange(256, dtype=np.uint8), TARGET_WIDTH // 256)


def build_target() -> Image.Image:
    pixels = np.zeros((TARGET_HEIGHT, TARGET_WIDTH, 3), dtype=np.uint8)
    ramp = _level_ramp()

    # Every input level occupies exactly eight columns. The analyzer samples
    # this region to recover the renderer's 256-point transfer curve.
    pixels[0:256, :, :] = ramp[None, :, None]

    # Sixteen neutral steps expose clipping and banding at a glance.
    step_width = TARGET_WIDTH // 16
    for index in range(16):
        start = index * step_width
        end = TARGET_WIDTH if index == 15 else (index + 1) * step_width
        pixels[256:512, start:end, :] = int(round(index * 255 / 15))

    # Primary-channel ramps reveal channel clipping and chroma drift.
    channel_bands = ((512, 597, 0), (597, 682, 1), (682, 768, 2))
    for top, bottom, channel in channel_bands:
        pixels[top:bottom, :, channel] = ramp[None, :]

    # Hue-bearing luminance ramps catch color changes that a neutral ramp
    # cannot. Each row band scales a fixed RGB direction through 0..100%.
    colors = np.asarray(
        [
            (214, 136, 110),
            (77, 142, 215),
            (92, 176, 112),
            (220, 184, 76),
        ],
        dtype=np.float32,
    )
    factor = ramp.astype(np.float32) / 255.0
    for index, color in enumerate(colors):
        top = 768 + index * 64
        pixels[top : top + 64, :, :] = np.rint(
            factor[None, :, None] * color[None, None, :]
        ).astype(np.uint8)

    # Equal-amplitude detail placed in five tonal zones shows whether a
    # control preserves local texture while moving a region's brightness.
    x = np.arange(TARGET_WIDTH, dtype=np.float32)
    texture = np.sin(2.0 * np.pi * x / 32.0) * 10.0
    zone_levels = (16, 64, 128, 192, 240)
    zone_height = 256 // len(zone_levels)
    for index, level in enumerate(zone_levels):
        top = 1024 + index * zone_height
        bottom = 1280 if index == len(zone_levels) - 1 else top + zone_height
        values = np.clip(level + texture, 0, 255).astype(np.uint8)
        pixels[top:bottom, :, :] = values[None, :, None]

    # Saturated and photographic-color patches expose hue shifts and gamut
    # clipping without making the analytical ramp itself image-dependent.
    patches = (
        (0, 0, 0),
        (32, 32, 32),
        (96, 96, 96),
        (160, 160, 160),
        (224, 224, 224),
        (255, 255, 255),
        (255, 0, 0),
        (0, 255, 0),
        (0, 0, 255),
        (0, 255, 255),
        (255, 0, 255),
        (255, 255, 0),
        (214, 136, 110),
        (77, 142, 215),
        (92, 176, 112),
        (220, 184, 76),
    )
    patch_width = TARGET_WIDTH // len(patches)
    for index, color in enumerate(patches):
        left = index * patch_width
        right = TARGET_WIDTH if index == len(patches) - 1 else left + patch_width
        pixels[1280:1536, left:right, :] = color

    return Image.fromarray(pixels, mode="RGB")


def _write_guide(target: Image.Image, path: Path) -> None:
    guide = target.copy()
    draw = ImageDraw.Draw(guide)
    regions = (
        ((0, 0, 2047, 255), "256-level neutral ramp"),
        ((0, 256, 2047, 511), "16 neutral steps"),
        ((0, 512, 2047, 767), "RGB channel ramps"),
        ((0, 768, 2047, 1023), "color luminance ramps"),
        ((0, 1024, 2047, 1279), "texture by tonal zone"),
        ((0, 1280, 2047, 1535), "neutral and color patches"),
    )
    for box, label in regions:
        draw.rectangle(box, outline=(255, 64, 64), width=3)
        draw.rectangle((box[0] + 8, box[1] + 8, box[0] + 260, box[1] + 31), fill=(0, 0, 0))
        draw.text((box[0] + 14, box[1] + 12), label, fill=(255, 255, 255))
    guide.save(path)


def _render_current(target: Image.Image, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for setting in calibration_settings():
        recipe = EditRecipe(**{setting.control: setting.value})
        recipe.apply(target).save(output_dir / setting.filename)


def _write_manifest(run_dir: Path) -> None:
    payload = {
        "schema": 1,
        "target": "tone_target.png",
        "dimensions": [TARGET_WIDTH, TARGET_HEIGHT],
        "regions": {
            "grayRamp": list(GRAY_RAMP_BOX),
            "colorRamp": list(COLOR_RAMP_BOX),
        },
        "exports": [
            {
                "control": setting.control,
                "value": setting.value,
                "filename": setting.filename,
            }
            for setting in calibration_settings()
        ],
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(payload, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_instructions(run_dir: Path) -> None:
    endpoints = {
        control: max(abs(value) for value in values)
        for control, values in CONTROL_VALUES.items()
    }
    endpoint_names = [
        setting.filename
        for setting in calibration_settings()
        if abs(setting.value) == endpoints[setting.control]
    ]
    midpoint_names = [
        setting.filename
        for setting in calibration_settings()
        if setting.filename not in endpoint_names
    ]
    text = f"""# Photoshop Light-Control Calibration

The target is `tone_target.png`. `tone_target_guide.png` is only a labeled
reference and must not be processed.

## Photoshop setup

1. Open `tone_target.png` without resizing it or changing its sRGB profile.
2. Use **Filter > Camera Raw Filter** and reset every Light control to zero.
3. Change exactly one control to the value encoded in the required filename.
   `m` means negative and `p` means positive; for example,
   `highlights_m100.png` is Highlights -100.
4. Apply the filter and export a lossless PNG into `photoshop/` using that
   exact filename. Do not resize, convert color profile, or add another edit.
5. Revert to the untouched target before producing the next file.

The analyzer is incremental. Start with these twelve endpoint renders:

{chr(10).join(f'- `{name}`' for name in endpoint_names)}

Then add these midpoint renders for curve fitting:

{chr(10).join(f'- `{name}`' for name in midpoint_names)}

## Analyze

From the repository root:

```powershell
.\\scripts\\tone-calibration.ps1 analyze "{run_dir}"
```

The command updates `report.md` and `curves.csv`. Missing Photoshop files are
listed but do not prevent analysis of completed exports.
"""
    (run_dir / "PHOTOSHOP_STEPS.md").write_text(text, encoding="utf-8")


def _extract_gray_curve(image: Image.Image) -> tuple[np.ndarray, np.ndarray]:
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    left, top, right, bottom = GRAY_RAMP_BOX
    region = rgb[top:bottom, left:right, :]
    block_width = (right - left) // 256
    channels = np.empty((256, 3), dtype=np.float32)
    for level in range(256):
        block = region[:, level * block_width : (level + 1) * block_width, :]
        channels[level] = np.median(block, axis=(0, 1))
    luma = channels @ np.asarray((0.2126, 0.7152, 0.0722), dtype=np.float32)
    return channels, luma


def _mean_chroma_drift(target: Image.Image, rendered: Image.Image) -> float:
    left, top, right, bottom = COLOR_RAMP_BOX
    baseline = np.asarray(target.convert("RGB"), dtype=np.float32)[top:bottom, left:right]
    output = np.asarray(rendered.convert("RGB"), dtype=np.float32)[top:bottom, left:right]
    baseline_sum = baseline.sum(axis=2, keepdims=True)
    output_sum = output.sum(axis=2, keepdims=True)
    valid = np.logical_and(baseline_sum[..., 0] >= 24.0, output_sum[..., 0] >= 24.0)
    if not np.any(valid):
        return 0.0
    baseline_chroma = baseline / np.maximum(1.0, baseline_sum)
    output_chroma = output / np.maximum(1.0, output_sum)
    return float(np.mean(np.abs(output_chroma[valid] - baseline_chroma[valid])) * 100.0)


def _metrics(target: Image.Image, rendered: Image.Image) -> tuple[dict[str, float | int], np.ndarray]:
    channels, luma = _extract_gray_curve(rendered)
    differences = np.diff(luma)
    neutral_cast = np.max(channels, axis=1) - np.min(channels, axis=1)
    metrics: dict[str, float | int] = {
        "minimum": float(luma.min()),
        "maximum": float(luma.max()),
        "uniqueLevels": int(len(np.unique(np.rint(luma).astype(np.int16)))),
        "blackClippedInputs": int(np.count_nonzero(luma <= 0.5)),
        "whiteClippedInputs": int(np.count_nonzero(luma >= 254.5)),
        "reversals": int(np.count_nonzero(differences < -0.5)),
        "flatSteps": int(np.count_nonzero(np.abs(differences) < 0.5)),
        "worstReverseStep": float(min(0.0, differences.min(initial=0.0))),
        "maximumNeutralCast": float(neutral_cast.max(initial=0.0)),
        "meanChromaDriftPct": _mean_chroma_drift(target, rendered),
    }
    return metrics, channels


def analyze(run_dir: Path) -> Path:
    target_path = run_dir / "tone_target.png"
    if not target_path.exists():
        raise FileNotFoundError(f"Missing calibration target: {target_path}")
    target = Image.open(target_path).convert("RGB")
    rows: list[dict[str, object]] = []
    curves: list[dict[str, object]] = []
    missing_photoshop: list[str] = []

    for renderer, folder_name in (("Image Triage", "current"), ("Photoshop", "photoshop")):
        folder = run_dir / folder_name
        for setting in calibration_settings():
            path = folder / setting.filename
            if not path.exists():
                if renderer == "Photoshop":
                    missing_photoshop.append(setting.filename)
                continue
            with Image.open(path) as opened:
                rendered = opened.convert("RGB")
            if rendered.size != target.size:
                raise ValueError(
                    f"{path.name} is {rendered.size[0]}x{rendered.size[1]}; "
                    f"expected {target.size[0]}x{target.size[1]}"
                )
            metrics, channels = _metrics(target, rendered)
            rows.append(
                {
                    "renderer": renderer,
                    "control": setting.control,
                    "value": setting.value,
                    **metrics,
                }
            )
            for input_level, rgb in enumerate(channels):
                curves.append(
                    {
                        "renderer": renderer,
                        "control": setting.control,
                        "value": setting.value,
                        "input": input_level,
                        "red": round(float(rgb[0]), 4),
                        "green": round(float(rgb[1]), 4),
                        "blue": round(float(rgb[2]), 4),
                    }
                )

    curve_path = run_dir / "curves.csv"
    with curve_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("renderer", "control", "value", "input", "red", "green", "blue"),
        )
        writer.writeheader()
        writer.writerows(curves)

    lines = [
        "# Tone-Control Calibration Report",
        "",
        "| Renderer | Control | Value | Range | Unique | Clip 0 | Clip 255 | Reversals | Flat steps | Chroma drift |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {renderer} | {control} | {value:g} | {minimum:.1f}-{maximum:.1f} | "
            "{uniqueLevels} | {blackClippedInputs} | {whiteClippedInputs} | "
            "{reversals} | {flatSteps} | {meanChromaDriftPct:.3f}% |".format(**row)
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- Reversals must be zero: a brighter input cannot map below a darker input.",
            "- Flat steps and clipped inputs expose lost tonal separation.",
            "- Chroma drift measures RGB-ratio changes in primary-channel ramps.",
            "- Photoshop results are references, not a requirement to reproduce proprietary math exactly.",
            "",
            f"## Missing Photoshop exports ({len(missing_photoshop)})",
            "",
        ]
    )
    lines.extend(f"- `{name}`" for name in missing_photoshop)
    report_path = run_dir / "report.md"
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report_path


def prepare(run_dir: Path) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "photoshop").mkdir(exist_ok=True)
    target = build_target()
    target.save(run_dir / "tone_target.png", optimize=False)
    _write_guide(target, run_dir / "tone_target_guide.png")
    _write_manifest(run_dir)
    _write_instructions(run_dir)
    _render_current(target, run_dir / "current")
    analyze(run_dir)
    return run_dir


def _default_run_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return REPO_ROOT / ".benchmarks" / "tone_calibration" / stamp


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare", help="Create a calibration run.")
    prepare_parser.add_argument("--run-dir", type=Path, default=None)
    prepare_parser.add_argument("--open", action="store_true", dest="open_folder")
    analyze_parser = subparsers.add_parser("analyze", help="Analyze available exports.")
    analyze_parser.add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)

    if args.command == "prepare":
        run_dir = prepare((args.run_dir or _default_run_dir()).resolve())
        print(run_dir)
        print(run_dir / "PHOTOSHOP_STEPS.md")
        if args.open_folder and os.name == "nt":
            os.startfile(run_dir)  # type: ignore[attr-defined]
        return 0

    report = analyze(args.run_dir.resolve())
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
