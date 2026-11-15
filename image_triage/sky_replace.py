"""Sky Replacement: the sky of a photo swapped for another, by PhotoCraft's native renderer.

The settings are exactly the recipe's ``cameraRaw.sky`` (see PhotoCraft ``camera_raw/sky.rs``): the chosen sky picture's
path, the sky matte as a raster mask, and the sliders. This module holds the arithmetic around them: ranges, the matte's
encoding, and the library of sky pictures the user has imported.
"""
from __future__ import annotations

import hashlib
import math
import shutil
from pathlib import Path

DEFAULTS = {"source": "", "amount": 0.0, "shiftEdge": 0.0, "fadeEdge": 0.0, "brightness": 0.0, "warmth": 0.0,
            "colorMatch": 0.0, "relight": 0.0, "scale": 100.0, "offsetX": 0.0, "offsetY": 0.0, "flip": False}
RANGES = {"amount": (0, 100), "shiftEdge": (-100, 100), "fadeEdge": (0, 100), "brightness": (-100, 100), "warmth": (-100, 100),
          "colorMatch": (0, 100), "relight": (0, 100), "scale": (25, 400), "offsetX": (-100, 100), "offsetY": (-100, 100)}
FIRST_AMOUNT = 100                 # choosing a sky shows it
FIRST_MATCH, FIRST_RELIGHT, FIRST_FADE = 35, 25, 20   # a believable start: the sky sits in the scene instead of on it
MATTE_SIDE = 1024                  # the matte is authored at this long edge; the renderer fits it to the photo's edges
SKY_SIDE = 3000
MAX_LIBRARY = 200
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp")


def _number(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def valid(sky) -> bool:
    """Whether ``sky`` is something the recipe can hold: nothing at all, or settings in range and a raster matte."""
    if sky in (None, {}):
        return True
    if not isinstance(sky, dict) or not set(sky) <= set(DEFAULTS) | {"mask"}:
        return False
    if not isinstance(sky.get("source", ""), str) or len(sky.get("source", "")) > 256 or not isinstance(sky.get("flip", False), bool):
        return False
    if any(key in sky and not (_number(sky[key]) and low <= sky[key] <= high) for key, (low, high) in RANGES.items()):
        return False
    mask = sky.get("mask")
    if mask is None:
        return True
    from .camera_raw_recipe_masks import MAX_RASTER_SIDE

    return (isinstance(mask, dict) and set(mask) == {"type", "w", "h", "data"} and mask["type"] == "raster"
            and all(type(mask[key]) is int and 0 < mask[key] <= MAX_RASTER_SIDE for key in ("w", "h"))
            and isinstance(mask["data"], str) and len(mask["data"]) <= 6 << 20)


def active(sky) -> bool:
    """Whether the recipe's sky changes the picture (as ``SkyReplace::active`` in the renderer)."""
    return (isinstance(sky, dict) and bool(sky.get("source")) and sky.get("mask") is not None
            and _number(sky.get("amount", 0)) and sky.get("amount", 0) > 0)


def normalized(sky) -> dict:
    """Every setting present and inside its range (the matte, when there is one, is kept as it is)."""
    result = dict(DEFAULTS)
    if isinstance(sky, dict):
        for key, (low, high) in RANGES.items():
            if _number(sky.get(key)):
                result[key] = float(max(low, min(high, sky[key])))
        if isinstance(sky.get("source"), str):
            result["source"] = sky["source"][:256]
        result["flip"] = sky.get("flip") is True
        if sky.get("mask") is not None:
            result["mask"] = sky["mask"]
    return result


def library_root() -> Path:
    from .scan_cache import app_data_root

    return app_data_root() / "skies"


SEEDED = ".seeded"


def bundled_root() -> Path:
    return Path(__file__).parent / "assets" / "skies"


def seed_bundled(root: Path | None = None, bundled: Path | None = None) -> None:
    """Copy the skies that ship with the app into the library, each one once. A sky the user removes stays removed: the
    names already copied are remembered beside the library."""
    import json

    root, bundled = (Path(root) if root else library_root()), (Path(bundled) if bundled else bundled_root())
    try:
        sources = sorted(f for f in bundled.iterdir() if f.suffix.lower() in IMAGE_SUFFIXES)
    except OSError:
        return
    marker = root / SEEDED
    try:
        done = set(json.loads(marker.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        done = set()
    fresh = [f for f in sources if f.name not in done]
    if not fresh:
        return
    try:
        root.mkdir(parents=True, exist_ok=True)
        for f in fresh:
            shutil.copyfile(f, root / f"{f.stem}-bundled{f.suffix.lower()}")
            done.add(f.name)
        marker.write_text(json.dumps(sorted(done)), encoding="utf-8")
    except OSError:
        pass          # the library just has fewer skies this time


def library(root: Path | None = None) -> list[dict]:
    """The imported skies, newest first: ``{"path", "name"}``."""
    root = Path(root) if root else library_root()
    try:
        files = sorted((f for f in root.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_SUFFIXES),
                       key=lambda f: f.stat().st_mtime, reverse=True)
    except OSError:
        return []
    return [{"path": str(f), "name": f.stem.rsplit("-", 1)[0]} for f in files[:MAX_LIBRARY]]


def library_has(path, root: Path | None = None) -> bool:
    """Whether ``path`` is a picture of the library (a recipe never names a file from anywhere else)."""
    try:
        return Path(path).resolve().parent == (Path(root) if root else library_root()).resolve() and Path(path).is_file()
    except OSError:
        return False


def import_sky(source, root: Path | None = None) -> Path:
    """Copy a picture into the library as a JPEG of at most ``SKY_SIDE`` pixels; the same picture twice is one entry."""
    from PIL import Image

    source = Path(source)
    root = Path(root) if root else library_root()
    if source.suffix.lower() not in IMAGE_SUFFIXES:
        raise ValueError("Choose a picture file (JPEG, PNG, WebP, TIFF or BMP).")
    with Image.open(source) as opened:
        picture = opened.convert("RGB")
    picture.thumbnail((SKY_SIDE, SKY_SIDE), Image.Resampling.LANCZOS)
    digest = hashlib.sha1(picture.tobytes()[:1 << 20] + str(picture.size).encode()).hexdigest()[:12]
    name = "".join(c if c.isalnum() or c in " -_" else "_" for c in source.stem)[:40].strip() or "sky"
    target = root / f"{name}-{digest}.jpg"
    root.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        picture.save(target, "JPEG", quality=92)
    return target


def forget_sky(path, root: Path | None = None) -> bool:
    """Delete one imported sky (only a file inside the library)."""
    root = (Path(root) if root else library_root()).resolve()
    target = Path(path).resolve()
    if target.parent != root or not target.is_file():
        return False
    target.unlink()
    return True
