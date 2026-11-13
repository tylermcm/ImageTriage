"""Light Depth: place a light inside the scene using a monocular depth map.

The light is not a new pixel operation. Its reach is turned into the coverage of an ordinary Camera Raw local mask
(brighter and warmer where the light falls), so the native renderer, export and the hosted editor all treat it like any
other mask. This module is the arithmetic only: depth map and settings in, 8-bit coverage out.

Depth maps are 8-bit, 255 = nearest (``depth_worker``).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

MASK_NAME = "Light Depth"
SIDECAR_TAIL = ".photocraft.lightdepth.json"  # carried with the photo's other edit files (``edit_carry.EDIT_MARKERS``)
SIDECAR_VERSION = 1

# Coverage is authored small: light falls off smoothly, and a continuous-tone raster is expensive in the run-length
# recipe encoding. The renderer scales it to the photo.
COVERAGE_SIDE = 640
PAD_SIDE = 320  # the depth picture shown in the panel

MAX_EXPOSURE = 2.0  # stops at Amount 100
MAX_TEMPERATURE = 60.0  # local Temperature at Warmth +/-100

DEFAULTS = {"amount": 0.0, "x": 0.5, "y": 0.6, "depth": 0.5, "softness": 30.0, "warmth": 0.0, "reach": 50.0, "focus": 0.0}
RANGES = {"amount": (0, 100), "x": (0, 1), "y": (0, 1), "depth": (0, 1), "softness": (0, 100), "warmth": (-100, 100),
          "reach": (0, 100), "focus": (0, 100)}


def valid(settings) -> bool:
    return (isinstance(settings, dict) and settings.keys() == DEFAULTS.keys()
            and all(type(settings[key]) in (int, float) and math.isfinite(settings[key])
                    and low <= settings[key] <= high for key, (low, high) in RANGES.items()))


def normalized(settings) -> dict:
    """``settings`` with every missing or unusable value replaced by its default and every value inside its range."""
    result = dict(DEFAULTS)
    if isinstance(settings, dict):
        for key, (low, high) in RANGES.items():
            value = settings.get(key)
            if type(value) in (int, float) and math.isfinite(value):
                result[key] = float(max(low, min(high, value)))
    return result


def _blur(values, radius: float):
    from PIL import Image, ImageFilter

    if radius < 0.3:
        return values
    import numpy as np

    image = Image.fromarray(np.clip(np.rint(values * 255), 0, 255).astype(np.uint8))
    return np.asarray(image.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32) / 255


def working_depth(depth, side: int = COVERAGE_SIDE):
    """The depth map at working size (long edge ``side`` at most), as float 0..1 with 1 nearest."""
    import numpy as np
    from PIL import Image

    values = np.asarray(depth)
    if values.ndim != 2 or min(values.shape) < 1:
        raise ValueError("The depth map is not a single-channel image")
    image = Image.fromarray(values.astype(np.uint8))
    scale = min(1.0, side / max(image.size))
    if scale < 1:
        image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                             Image.Resampling.BILINEAR)
    return np.asarray(image, dtype=np.float32) / 255


def depth_at(depth, x: float, y: float) -> float:
    """Scene depth (0 far .. 1 near) under a point of the photo, averaged over a small patch so one noisy pixel does
    not decide where the light sits."""
    import numpy as np

    values = np.asarray(depth, dtype=np.float32)
    if values.max(initial=0) > 1:
        values = values / 255
    height, width = values.shape
    cx, cy = min(width - 1, max(0, int(x * width))), min(height - 1, max(0, int(y * height)))
    reach = max(1, round(min(width, height) * 0.01))
    patch = values[max(0, cy - reach):cy + reach + 1, max(0, cx - reach):cx + reach + 1]
    return float(np.median(patch))


def coverage(depth, settings, side: int = COVERAGE_SIDE):
    """How strongly the light falls on each pixel, as uint8 (255 = full light).

    The light sits at ``depth`` in the scene. Things at that distance are fully lit and the light fades for things
    nearer or farther; ``reach`` sets how far through the scene it carries. ``focus`` above zero also narrows it
    sideways around ``x``, turning a sheet of light into a pool. ``softness`` lets it spill across object edges.
    """
    import numpy as np

    settings = normalized(settings)
    scene = working_depth(depth, side)
    height, width = scene.shape
    # A touch of smoothing always: the model's map has per-pixel noise that would otherwise show as grain in the light.
    scene = _blur(scene, max(width, height) * 0.004)
    sigma = 0.05 + 0.45 * (settings["reach"] / 100) ** 1.5
    light = np.exp(-0.5 * ((scene - settings["depth"]) / sigma) ** 2)
    if settings["focus"] > 0:
        spread = 1.2 - 1.05 * settings["focus"] / 100  # in picture widths
        columns = (np.arange(width, dtype=np.float32) + 0.5) / width
        rows = (np.arange(height, dtype=np.float32) + 0.5) / height
        aspect = height / width
        distance = np.hypot(columns[None, :] - settings["x"], (rows[:, None] - settings["y"]) * aspect)
        light = light * np.exp(-0.5 * (distance / spread) ** 2)
    light = _blur(light, max(width, height) * 0.06 * settings["softness"] / 100)
    return np.clip(np.rint(light * 255), 0, 255).astype(np.uint8)


def adjustment(settings) -> dict:
    """The local adjustment the light applies where it falls."""
    settings = normalized(settings)
    result = {"exposure": round(MAX_EXPOSURE * settings["amount"] / 100, 4)}
    if settings["warmth"]:
        result["temperature"] = round(MAX_TEMPERATURE * settings["warmth"] / 100, 2)
    return result


def find_mask(masks) -> int:
    """Index of the mask Light Depth manages, or -1. It is found by name, so renaming it in the Mask tab hands it over
    to the person as an ordinary mask."""
    for index, mask in enumerate(masks or []):
        if isinstance(mask, dict) and mask.get("name") == MASK_NAME:
            return index
    return -1


def apply(masks, settings, shape) -> list:
    """``masks`` with the Light Depth mask created or updated. Only its base coverage and the two values the light
    owns are replaced, so components and other adjustments added to it in the Mask tab stay."""
    from copy import deepcopy

    rows = deepcopy(list(masks or []))
    index = find_mask(rows)
    owned = adjustment(settings)
    if index < 0:
        rows.append({"name": MASK_NAME, "enabled": True, "invert": False, "amount": 100, "shape": shape, "adjust": owned})
        return rows
    mask = rows[index]
    mask["shape"] = shape
    kept = {key: value for key, value in mask.get("adjust", {}).items() if key not in ("exposure", "temperature")}
    mask["adjust"] = {**kept, **owned}
    return rows


def sidecar_path(recipe_path) -> Path:
    """Where a photo's Light Depth settings are kept, next to its recipe (``<photo>.cameraraw.json``)."""
    recipe = Path(recipe_path)
    name = recipe.name
    stem = name[:-len(".cameraraw.json")] if name.endswith(".cameraraw.json") else name
    return recipe.with_name(stem + SIDECAR_TAIL)


def read_sidecar(recipe_path):
    """The saved settings, or None when there are none or the file cannot be used."""
    try:
        data = json.loads(sidecar_path(recipe_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != SIDECAR_VERSION:
        return None
    settings = data.get("settings")
    return dict(settings) if valid(settings) else None


def write_sidecar(recipe_path, settings) -> None:
    """Keep ``settings`` with the recipe, or remove the file when ``settings`` is None."""
    target = sidecar_path(recipe_path)
    if settings is None:
        target.unlink(missing_ok=True)
        return
    import os
    import tempfile

    data = json.dumps({"version": SIDECAR_VERSION, "settings": normalized(settings)}).encode("utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, scratch = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(handle, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(scratch, target)
    except BaseException:
        Path(scratch).unlink(missing_ok=True)
        raise
