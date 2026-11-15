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
FIRST_MATCH, FIRST_RELIGHT, FIRST_FADE = 35, 25, 3    # a believable start: the sky sits in the scene instead of on it
MATTE_SIDE = 1536                  # the matte is authored at this long edge (the most a raster mask holds)
SKY_SIDE = 3000
MAX_LIBRARY = 200
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp")


def _number(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _box(values, radius: int):
    """Mean over a (2 radius + 1) square at every pixel (edges repeated), for a 2-D or (h, w, n) array."""
    import numpy as np

    size = 2 * radius + 1
    padded = np.pad(values, ((radius + 1, radius), (radius + 1, radius)) + ((0, 0),) * (values.ndim - 2), mode="edge")
    total = np.zeros((padded.shape[0] + 1, padded.shape[1] + 1) + values.shape[2:], dtype=np.float64)
    total[1:, 1:] = padded.cumsum(0).cumsum(1)
    h, w = values.shape[:2]
    area = total[size:size + h, size:size + w] - total[:h, size:size + w] - total[size:size + h, :w] + total[:h, :w]
    return (area / (size * size)).astype(np.float32)


def refine_matte(matte, rgb, radius: int | None = None, eps: float = 1e-3):
    """Snap a soft sky matte onto the photo's own edges.

    ``matte`` is the model's mask (float 0..1, white = sky) at the size of ``rgb`` (float 0..1, h x w x 3): the model sees a
    reduced picture and its edge is a blur several pixels wide, much wider than a tree's outline. A colour-guided filter (He
    et al.) makes the matte follow the photo's edges, and a steep curve then settles what is left of the transition. Trees,
    ridgelines and fine gaps come out crisp, so the sky's edge needs little fade and no longer looks blocky without one.
    """
    import numpy as np

    h, w = matte.shape
    radius = radius or int(max(3, min(12, round(max(h, w) * 0.004))))
    guide = np.sqrt(np.clip(rgb, 0, 1)).astype(np.float32)          # shadows count: the edges of dark trees are the hard ones
    mean_g = _box(guide, radius)
    mean_p = _box(matte, radius)
    covariance = np.stack([_box(guide[..., c] * matte, radius) - mean_g[..., c] * mean_p for c in range(3)], -1)
    variance = np.empty((h, w, 3, 3), dtype=np.float32)
    for i in range(3):
        for j in range(i, 3):
            variance[..., i, j] = variance[..., j, i] = _box(guide[..., i] * guide[..., j], radius) - mean_g[..., i] * mean_g[..., j]
    variance += np.eye(3, dtype=np.float32) * eps
    gain = np.linalg.solve(variance.reshape(-1, 3, 3), covariance.reshape(-1, 3, 1)).reshape(h, w, 3)
    offset = mean_p - (gain * mean_g).sum(-1)
    filtered = (np.stack([_box(gain[..., c], radius) for c in range(3)], -1) * guide).sum(-1) + _box(offset, radius)
    t = np.clip((np.clip(filtered, 0, 1) - 0.3) / 0.4, 0, 1)
    return t * t * (3 - 2 * t)


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
    """Where the sky pictures are kept: a real folder under the managed root (``~/.image-triage``), not the app's data folder.

    PhotoCraft's native renderer reads a sky from the path the recipe names, and it is another process. Under Microsoft Store
    Python the per-user data folder is virtualized (the files land in the package's own cache): only that Python can see
    them, so the renderer found no file and drew no sky. The AI models avoid the same trap the same way.
    """
    from .ai_paths import managed_ai_root

    return managed_ai_root() / "skies"


def previous_library_root() -> Path:
    """Where the first version kept the library (the app's data folder; possibly virtualized, see ``library_root``)."""
    from .scan_cache import app_data_root

    return app_data_root() / "skies"


MIGRATED = ".migrated"


def migrate_previous(root: Path, previous: Path | None = None) -> None:
    """Copy the skies the person imported into the first version's library to ``root``, once. The bundled ones are not copied
    (they are seeded again) and neither is anything the person removed from the new library."""
    root = Path(root)
    marker = root / MIGRATED
    if marker.exists():
        return
    previous = Path(previous) if previous else previous_library_root()
    try:
        if previous.resolve() == root.resolve():
            return
        files = [f for f in previous.iterdir() if f.is_file() and f.suffix.lower() in IMAGE_SUFFIXES and "-bundled" not in f.stem]
    except OSError:
        return
    try:
        root.mkdir(parents=True, exist_ok=True)
        for f in files:
            if not (root / f.name).exists():
                shutil.copyfile(f, root / f.name)
        marker.write_text("1", encoding="utf-8")
    except OSError:
        pass


SEEDED = ".seeded"


def bundled_root() -> Path:
    return Path(__file__).parent / "assets" / "skies"


def seed_bundled(root: Path | None = None, bundled: Path | None = None) -> None:
    """Copy the skies that ship with the app into the library, each one once. A sky the user removes stays removed: the
    names already copied are remembered beside the library."""
    import json

    if root is None:
        migrate_previous(library_root())
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
