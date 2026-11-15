"""AI Erase: the arithmetic around a fill model, with no UI and no model.

An object is selected (a mask), grown, and a crop of the photo around it goes to an inpainting model (LaMa, see
``docs/creative_tools_plan.md``). What comes back is composited into the crop, given the photo's grain, converted to the
renderer's working encoding and stored as a small RGBA picture: the *patch*. The recipe then holds one retouch stroke of
kind ``patch`` naming that file and the part of the frame it covers; PhotoCraft's renderer blends it in with its own
seamless clone, so later global edits still apply. Nothing here changes a pixel of the photo.

Working encoding: PhotoCraft develops RAW in ROMM (ProPhoto-compatible) primaries, D50, gamma 1.8, and retouch runs on those
values near the end of the develop (before sky and local masks). The patch is written in that encoding.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

MODEL_SIDE = 512              # the fill model's working size
GROW_FRACTION = 0.006         # default Grow, as a share of the picture's long edge
GROW_MAX_FRACTION = 0.03
MAX_HOLE_FRACTION = 0.25      # past this much of the picture the fill is invention; the panel says so
CONTEXT = 0.8                 # the crop reaches this many hole-sizes beyond the hole
MIN_CONTEXT = 160             # ... and at least this many pixels
FEATHER_FRACTION = 0.0012     # the patch's edge, as a share of the long edge (at least 2 px)

# --- masks ----------------------------------------------------------------------------------------------------------------


def _gauss(values, sigma: float):
    from PIL import Image, ImageFilter
    import numpy as np

    if sigma < 0.3:
        return values.astype(np.float32)
    image = Image.fromarray(np.clip(np.rint(values * 255), 0, 255).astype(np.uint8))
    return np.asarray(image.filter(ImageFilter.GaussianBlur(sigma)), dtype=np.float32) / 255


def grow_mask(mask, radius: float):
    """``mask`` (uint8, 255 = object) grown outward by about ``radius`` pixels, so the fill also replaces the object's soft
    edge and its shadow-fringe: a tight cut-out leaves a ghost."""
    import numpy as np

    hole = (np.asarray(mask) > 127).astype(np.float32)
    if radius < 0.5:
        return (hole * 255).astype(np.uint8)
    # a Gaussian's 2 % level sits about two sigma outside an edge
    return ((_gauss(hole, radius / 2.05) > 0.02) * 255).astype(np.uint8)


def default_grow(size) -> int:
    return max(4, round(max(size) * GROW_FRACTION))


def max_grow(size) -> int:
    return max(8, round(max(size) * GROW_MAX_FRACTION))


def hole_fraction(mask) -> float:
    import numpy as np

    return float((np.asarray(mask) > 127).mean())


def bbox(mask):
    """``(x0, y0, x1, y1)`` of the object in ``mask`` (x1 and y1 exclusive), or None when it is empty."""
    import numpy as np

    ys, xs = np.nonzero(np.asarray(mask) > 127)
    if xs.size == 0:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def plan_crop(box, size, context: float = CONTEXT, minimum: int = MIN_CONTEXT):
    """The crop ``(x0, y0, x1, y1)`` sent to the model: the object plus context on every side, inside the picture of ``size``
    ``(width, height)``. A compact object is one fill; the model sees what surrounds it and invents what lies behind."""
    width, height = size
    x0, y0, x1, y1 = box
    margin = int(max(minimum, context * max(x1 - x0, y1 - y0)))
    return max(0, x0 - margin), max(0, y0 - margin), min(width, x1 + margin), min(height, y1 + margin)


# --- composing -----------------------------------------------------------------------------------------------------------


def composite(crop, filled, hole, feather: float):
    """``(rgb, alpha)``: the crop with the model's fill inside the hole and the crop's own pixels outside, and the coverage
    that blends the two (1 in the hole, falling to 0 over ``feather`` pixels beyond it). ``crop`` and ``filled`` are
    uint8 RGB, ``hole`` uint8 (255 = replaced)."""
    import numpy as np

    mask = (np.asarray(hole) > 127).astype(np.float32)
    alpha = np.maximum(mask, _gauss(mask, max(0.5, feather)))
    # the blur never reaches exactly 0, so cut its far tail: the patch must not touch pixels well outside the fill
    alpha = np.where(alpha < 0.01, 0.0, alpha).astype(np.float32)
    blended = crop.astype(np.float32) * (1 - alpha[..., None]) + filled.astype(np.float32) * alpha[..., None]
    return np.clip(np.rint(blended), 0, 255).astype(np.uint8), alpha


def feather_for(size) -> float:
    return max(2.0, max(size) * FEATHER_FRACTION)


def match_grain(filled, original, hole, ring: int = 24, seed: int = 0, amount: float = 1.0):
    """Give the fill the photo's grain. A model returns smooth texture, which looks like a clean patch on a noisy photo: the
    fine-grained luminance noise of a ring of the ``original`` around the hole is measured and added to ``filled`` (the
    model's picture, same size) inside the hole where it falls short."""
    import numpy as np

    weights = np.array([0.2126, 0.7152, 0.0722], np.float32)
    pixels = filled.astype(np.float32)
    luma = pixels @ weights
    seen = original.astype(np.float32) @ weights
    inside = np.asarray(hole) > 127
    around = (_gauss(inside.astype(np.float32), ring / 2.05) > 0.02) & ~(_gauss(inside.astype(np.float32), 2 / 2.05) > 0.02)
    if inside.sum() < 16 or around.sum() < 64:
        return filled
    # the finest detail only, and a robust spread (median deviation): ripples, edges and foliage in the surroundings are texture
    # the model has to supply, not grain, and counting them as grain makes the fill look like static
    def spread(values):
        return 1.4826 * float(np.median(np.abs(values - np.median(values))))

    wanted = spread((seen - _gauss(seen / 255, 0.8) * 255)[around]) * max(0.0, amount)
    have = spread((luma - _gauss(luma / 255, 0.8) * 255)[inside])
    if wanted <= have:
        return filled
    rng = np.random.default_rng(seed)
    noise = rng.normal(0.0, math.sqrt(wanted * wanted - have * have), luma.shape).astype(np.float32)
    out = pixels + (noise * inside)[..., None]
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def smooth_chroma(filled, hole, sigma: float = 2.0):
    """``filled`` with the colour (not the brightness) inside ``hole`` smoothed: the model leaves faint coloured speckle that a
    camera's grain never has."""
    import numpy as np

    pixels = filled.astype(np.float32)
    luma = (pixels @ np.array([0.2126, 0.7152, 0.0722], np.float32))[..., None]
    chroma = pixels - luma
    soft = np.stack([_gauss((chroma[..., c] + 128) / 255, sigma) * 255 - 128 for c in range(3)], axis=-1)
    inside = (_gauss((np.asarray(hole) > 127).astype(np.float32), 1.0))[..., None]
    out = pixels + (soft - chroma) * inside
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


# --- colour --------------------------------------------------------------------------------------------------------------

_ROMM_PRIMARIES = ((0.7347, 0.2653), (0.1596, 0.8404), (0.0366, 0.0001))
_D50 = (0.3457, 0.3585)
_D65 = (0.3127, 0.3290)
_SRGB_PRIMARIES = ((0.64, 0.33), (0.30, 0.60), (0.15, 0.06))
ROMM_GAMMA = 1.8


def _xyz(xy):
    return (xy[0] / xy[1], 1.0, (1 - xy[0] - xy[1]) / xy[1])


def _rgb_to_xyz(primaries, white):
    import numpy as np

    columns = np.array([_xyz(p) for p in primaries], np.float64).T
    scale = np.linalg.solve(columns, np.array(_xyz(white), np.float64))
    return columns * scale


def _bradford(source_white, target_white):
    import numpy as np

    cone = np.array([[0.8951, 0.2664, -0.1614], [-0.7502, 1.7135, 0.0367], [0.0389, -0.0685, 1.0296]])
    gains = np.diag((cone @ np.array(_xyz(target_white))) / (cone @ np.array(_xyz(source_white))))
    return np.linalg.inv(cone) @ gains @ cone


def _matrices():
    import numpy as np

    srgb_to_xyz = _rgb_to_xyz(_SRGB_PRIMARIES, _D65)
    romm_to_xyz = _rgb_to_xyz(_ROMM_PRIMARIES, _D50)
    forward = np.linalg.inv(romm_to_xyz) @ _bradford(_D65, _D50) @ srgb_to_xyz
    return forward, np.linalg.inv(forward)


def srgb_to_working(rgb):
    """uint8 sRGB (h, w, 3) as float 0..1 in the renderer's working encoding (ROMM primaries, D50, gamma 1.8)."""
    import numpy as np

    forward, _ = _matrices()
    c = np.asarray(rgb, np.float64) / 255
    linear = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    romm = np.clip(linear @ forward.T, 0, None)
    return (romm ** (1 / ROMM_GAMMA)).astype(np.float32)


def working_to_srgb(values):
    """The inverse of :func:`srgb_to_working`: float 0..1 working values as uint8 sRGB."""
    import numpy as np

    _, inverse = _matrices()
    linear = np.clip(np.asarray(values, np.float64), 0, None) ** ROMM_GAMMA @ inverse.T
    linear = np.clip(linear, 0, 1)
    c = np.where(linear <= 0.0031308, linear * 12.92, 1.055 * linear ** (1 / 2.4) - 0.055)
    return np.clip(np.rint(c * 255), 0, 255).astype(np.uint8)


# --- the patch -----------------------------------------------------------------------------------------------------------


def erase_root() -> Path:
    """Where patches are kept. A real folder under the managed root, never the app's data folder: PhotoCraft's renderer is
    another process and cannot see what Microsoft Store Python keeps in its virtualized data folder."""
    from .ai_paths import managed_ai_root

    return managed_ai_root() / "erase"


def write_patch(photo_path, rgb_working, alpha, root: Path | None = None) -> str:
    """Store the patch (8-bit RGBA PNG: working-encoded fill and coverage) and return its absolute path. The same patch
    for the same photo is one file."""
    import numpy as np
    from PIL import Image

    rgba = np.dstack([np.clip(np.rint(np.asarray(rgb_working) * 255), 0, 255), np.clip(np.rint(np.asarray(alpha) * 255), 0, 255)]).astype(np.uint8)
    folder = Path(root) if root else erase_root()
    folder = folder / hashlib.sha1(str(Path(photo_path).resolve()).lower().encode("utf-8", "replace")).hexdigest()[:12]
    name = hashlib.sha1(rgba.tobytes() + str(rgba.shape).encode()).hexdigest()[:20] + ".png"
    target = folder / name
    if not target.exists():
        folder.mkdir(parents=True, exist_ok=True)
        scratch = target.with_suffix(".tmp")
        Image.fromarray(rgba, "RGBA").save(scratch, format="PNG")
        scratch.replace(target)
    return str(target.resolve())


def patch_stroke(path: str, region, opacity: float = 100.0) -> dict:
    """The recipe's retouch stroke for a stored patch. ``region`` is ``(x0, y0, x1, y1)`` in fractions of the frame."""
    x0, y0, x1, y1 = (float(v) for v in region)
    return {"kind": "patch", "points": [[min(1.0, max(0.0, (x0 + x1) / 2)), min(1.0, max(0.0, (y0 + y1) / 2))]], "size": 0.01,
            "feather": 0.0, "opacity": float(opacity), "patch": {"path": path, "region": [x0, y0, x1, y1]}}


def make_patch(image, mask, fill, grow: float, photo_path, root: Path | None = None, grain: float = 1.0):
    """Everything after the model: ``image`` is the picture (uint8 RGB) the selection was made on, ``mask`` the object (uint8),
    ``fill(crop, hole)`` the model call (uint8 crop and hole -> uint8 filled crop, same size). Returns ``(stroke, info)`` or
    raises ValueError with a sentence for the panel."""
    import numpy as np

    height, width = image.shape[:2]
    hole = grow_mask(mask, grow)
    box = bbox(hole)
    if box is None:
        raise ValueError("Nothing is selected.")
    if hole_fraction(hole) > MAX_HOLE_FRACTION:
        raise ValueError("That is too much of the photo to fill in. Select a smaller object.")
    x0, y0, x1, y1 = plan_crop(box, (width, height))
    crop, cm = image[y0:y1, x0:x1], hole[y0:y1, x0:x1]
    filled = np.asarray(fill(crop, cm))
    if filled.shape != crop.shape:
        raise ValueError("The fill model returned a picture of the wrong size.")
    filled = smooth_chroma(filled, cm)
    filled = match_grain(filled, crop, cm, seed=(x0 * 7919 + y0 * 104729) & 0xFFFFFFFF, amount=grain)
    rgb, alpha = composite(crop, filled, cm, feather_for((width, height)))
    # only the part of the crop the fill touches: the patch is a small picture, not the crop
    ys, xs = np.nonzero(alpha > 0)
    # keep a ring of the photo's own pixels (alpha 0) around the fill: the renderer's seamless clone measures the mismatch there
    ring = max(8, round(3 * feather_for((width, height))))
    px0, py0 = max(0, int(xs.min()) - ring), max(0, int(ys.min()) - ring)
    px1, py1 = min(alpha.shape[1], int(xs.max()) + 1 + ring), min(alpha.shape[0], int(ys.max()) + 1 + ring)
    rgb, alpha = rgb[py0:py1, px0:px1], alpha[py0:py1, px0:px1]
    gx0, gy0 = x0 + px0, y0 + py0
    path = write_patch(photo_path, srgb_to_working(rgb), alpha, root)
    region = (gx0 / width, gy0 / height, (gx0 + rgb.shape[1]) / width, (gy0 + rgb.shape[0]) / height)
    return patch_stroke(path, region), {"box": (gx0, gy0, gx0 + rgb.shape[1], gy0 + rgb.shape[0]), "hole_fraction": hole_fraction(hole)}
